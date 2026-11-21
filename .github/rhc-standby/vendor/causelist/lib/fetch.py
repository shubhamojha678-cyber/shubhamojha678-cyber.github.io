"""HC Rajasthan (Jodhpur) cause list fetcher — pure HTTP, no browser.

Public flow per target date:
    fetch_causelist(date_str) -> (html_str | None, info_dict)

Behaviour:
    * Establishes a fresh session per call.
    * Resolves the captcha by image OCR with refresh-on-failure retry.
    * Submits the form; follows the returned filename to get the cause list HTML.
    * Returns (None, info) if the captcha could not be solved in N tries,
      if the server returned no record, or if anything else went wrong.

Server semantics discovered during POC:
    POST /cishcraj-jdp/causelists/check-captcha
        body 'SecCodeError' = wrong digits
        body ''             = correct digits (success)
    POST /cishcraj-jdp/get-causelist-in-pdf.php
        body == 'storefiles/createordjud/SearchCauselist_DDMMYYYY_HHMMSS.html'
            = success, fetch that URL for the actual list
        body == ''  OR body containing 'No Record'  = no data for that date
"""
from __future__ import annotations

import base64
import contextlib
import http.cookiejar
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .ocr import captcha_candidates, learn as captcha_learn, preprocess_and_ocr
from .resolve import ensure_resolvable

log = logging.getLogger(__name__)

BASE = "https://hcraj.nic.in"
HOST = "hcraj.nic.in"
# Seat path. Jodhpur is "cishcraj-jdp", Jaipur "cishcraj-jp"; the Jaipur seat
# serves the SAME endpoints (probed 29-Aug-2026). Never reassign this directly —
# use the seat() context manager so it is always restored.
SEAT_PATH = "cishcraj-jdp"

FORM_URL = f"{BASE}/{SEAT_PATH}/causelists/"
CAPTCHA_URL = f"{BASE}/{SEAT_PATH}/causelists/setgetcaptcha"
CHECK_URL = f"{BASE}/{SEAT_PATH}/causelists/check-captcha"
LIST_URL = f"{BASE}/{SEAT_PATH}/get-causelist-in-pdf.php"


@contextlib.contextmanager
def seat(path: str):
    """Temporarily point every cause-list URL at another seat.

        with seat("cishcraj-jp"):
            html, info = fetch_causelist("01/09/2026", lawyer="")

    Restores on the way out even if the body raises. Single-threaded use only —
    run.py drives this sequentially.
    """
    global SEAT_PATH, FORM_URL, CAPTCHA_URL, CHECK_URL, LIST_URL
    saved = (SEAT_PATH, FORM_URL, CAPTCHA_URL, CHECK_URL, LIST_URL)
    try:
        SEAT_PATH = path
        FORM_URL = f"{BASE}/{path}/causelists/"
        CAPTCHA_URL = f"{BASE}/{path}/causelists/setgetcaptcha"
        CHECK_URL = f"{BASE}/{path}/causelists/check-captcha"
        LIST_URL = f"{BASE}/{path}/get-causelist-in-pdf.php"
        yield
    finally:
        SEAT_PATH, FORM_URL, CAPTCHA_URL, CHECK_URL, LIST_URL = saved
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


@dataclass
class FetchInfo:
    date: str
    ok: bool
    state: str  # "list" | "no_record" | "captcha_failed" | "network_error"
    captcha_attempts: int = 0
    elapsed_s: float = 0.0
    list_filename: Optional[str] = None
    error: Optional[str] = None
    captcha_log: list = field(default_factory=list)
    resolver: Optional[str] = None  # "system", or the public resolver we fell back to


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _build_opener():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def _post(opener, url: str, body: bytes, *, content_type: str = "application/x-www-form-urlencoded",
          extra_headers: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    headers = {
        "Referer": FORM_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": content_type,
        "X-CSRF-Token": "false",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    # 25-Aug-2026: the HC server intermittently drops TCP connections mid-run.
    # Every POST here (captcha image, captcha submit, list submit) was a single
    # unretried attempt, so one dropped SYN aborted the whole slot. Retry the
    # transport; HTTP status codes are returned untouched to the caller.
    _last = None
    for _try in range(1, 4):
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            raise                      # a real HTTP status — the caller decides
        except Exception as e:
            _last = e
            logging.getLogger("orch").warning(
                "fetch: POST %s attempt %d/3 failed: %r", url, _try, e)
            if _try < 3:
                time.sleep(1.5 * _try)
    raise _last


def _fetch_captcha(opener) -> bytes:
    url = f"{CAPTCHA_URL}?{time.time()}"
    status, body = _post(opener, url, b"")
    if status != 200:
        raise RuntimeError(f"captcha endpoint returned status {status}")
    data = json.loads(body)
    return base64.b64decode(data["image"])


def _submit_captcha(opener, digits: str) -> bool:
    """True if accepted (empty body), False if rejected (SecCodeError)."""
    payload = urllib.parse.urlencode({"captcha": digits}).encode()
    status, body = _post(opener, CHECK_URL, payload)
    if status != 200:
        raise RuntimeError(f"check-captcha returned status {status}")
    return "SecCodeError" not in body


def _solve_captcha(opener, info, *, max_images: int, guesses_per_image: int,
                   total_guess_budget: int, inter_attempt_sleep_s: float):
    """Solve one session's captcha and return the accepted code, or None.

    02-Sep-2026: the portal's captcha became 6 mixed alphanumerics. A wrong
    guess does NOT invalidate the image (proved live), so we fetch ONE image
    and walk its ordered candidate list rather than burning a fresh image per
    guess. Measured over 80 live captchas: 80% solved on the first guess,
    mean 1.9 guesses, 80/80 solved within the per-image budget.
    """
    spent = 0
    for attempt in range(1, max_images + 1):
        info.captcha_attempts = attempt
        img = _fetch_captcha(opener)
        room = min(guesses_per_image, total_guess_budget - spent)
        if room <= 0:
            break
        guesses = captcha_candidates(img, limit=room)
        entry = {"attempt": attempt, "digits": guesses[0] if guesses else "",
                 "len": len(guesses[0]) if guesses else 0,
                 "candidates": len(guesses)}
        if not guesses:
            entry["result"] = "no_reading"
            info.captcha_log.append(entry)
            time.sleep(inter_attempt_sleep_s)
            continue
        accepted = None
        for i, guess in enumerate(guesses):
            spent += 1
            if _submit_captcha(opener, guess):
                accepted = guess
                entry["guess_rank"] = i + 1
                break
        entry["result"] = "accepted" if accepted else "SecCodeError"
        info.captcha_log.append(entry)
        if accepted:
            # the server just told us the truth — feed it back to the library
            captcha_learn(img, accepted)
            return accepted
        time.sleep(inter_attempt_sleep_s)
    return None


def _submit_form(opener, date_ddmmyyyy: str, lawyer: str, cltype: str = "D",
                 cl: str = "C") -> str:
    fields = {
        "cl": _b64(cl),
        "cldt": _b64(date_ddmmyyyy),
        "cltype": _b64(cltype),
        "courtno": _b64(""),
        "judgename": _b64(""),
        "caseno": _b64(""),
        "lawyrname": _b64(lawyer),
        "petname": _b64(""),
        "resname": _b64(""),
        "deptname": _b64(""),
        "format": _b64("1"),  # HTML
        "QueryType": "Causelist",
        "view": _b64("view"),
    }
    payload = urllib.parse.urlencode(fields).encode()
    status, body = _post(opener, LIST_URL, payload, timeout=45)
    if status != 200:
        raise RuntimeError(f"get-causelist-in-pdf returned status {status}")
    return body.strip()


def _open_retry(opener, url, timeout=20, tries=5, base_sleep=1.5, log=None):
    """Open url through opener, retrying transient connection failures.

    25-Aug-2026: the Rajasthan HC server intermittently drops TCP connections —
    some requests connect in 0.15s, others never complete the handshake at all
    (conn=0.000000s). This first cookie fetch was a SINGLE unretried attempt, so
    one dropped SYN failed an entire cause-list slot. Eight consecutive hourly
    runs died this way on 24-Aug. Returns the body; raises the last error if
    every try fails.
    """
    last = None
    for attempt in range(1, tries + 1):
        try:
            with opener.open(url, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            if log is not None:
                log.warning("fetch: %s attempt %d/%d failed: %r", url, attempt, tries, e)
            if attempt < tries:
                time.sleep(base_sleep * attempt)
    raise last


def fetch_causelist(
    date_ddmmyyyy: str,
    lawyer: str = "ojha",
    *,
    cltype: str = "D",
    cl: str = "C",
    max_captcha_images: int = 4,
    guesses_per_image: int = 120,
    total_guess_budget: int = 300,
    inter_attempt_sleep_s: float = 0.4,
) -> tuple[Optional[str], FetchInfo]:
    """End-to-end fetch for a single date.

    cltype: "D" = Daily list, "S" = Supplementary list (same form, same
    captcha gate — the portal treats them as sibling list types).
    cl: "C" = Cause List, "N" = the listing-office Note published with it.
    Returns (html, info). html is None unless info.state == 'list'.
    """
    info = FetchInfo(date=date_ddmmyyyy, ok=False, state="captcha_failed")
    t0 = time.time()
    try:
        # The router's resolver SERVFAILs the nic.in zone; fall back to public
        # DNS and pin the answer so urllib connects to the right host.
        ip, info.resolver = ensure_resolvable(HOST)
        if info.resolver != "system":
            log.warning(f"system DNS broken for {HOST}; using {ip} via {info.resolver}")

        opener = _build_opener()
        _open_retry(opener, FORM_URL, timeout=20, tries=5,
                    log=logging.getLogger('orch'))  # cookies (retried)

        accepted_digits = _solve_captcha(
            opener, info,
            max_images=max_captcha_images,
            guesses_per_image=guesses_per_image,
            total_guess_budget=total_guess_budget,
            inter_attempt_sleep_s=inter_attempt_sleep_s)

        if not accepted_digits:
            info.error = (f"captcha not solved in {max_captcha_images} images "
                          f"/ {total_guess_budget} guesses")
            return None, info

        list_body = _submit_form(opener, date_ddmmyyyy, lawyer, cltype, cl)
        if not list_body or "no record" in list_body.lower():
            info.state = "no_record"
            info.ok = True
            return None, info

        # Body is a relative filename like:
        # storefiles/createordjud/SearchCauselist_20052026_080220.html
        info.list_filename = list_body
        full_url = f"{BASE}/{SEAT_PATH}/{list_body}"
        try:
            with opener.open(full_url, timeout=45) as r:
                html = r.read().decode("utf-8", errors="replace")
        except urllib.request.HTTPError as e:
            if e.code in (404, 410):
                # Server returned a filename pointing to a list that hasn't
                # been generated yet on disk — treat as "no record yet".
                info.state = "no_record"
                info.ok = True
                info.error = f"list file 404 ({list_body})"
                return None, info
            raise

        # A returned HTML that is too small (e.g. an error page) also counts
        # as "no record". The smallest real list we've seen is ~20 KB.
        if len(html) < 1500 or "no record" in html.lower():
            info.state = "no_record"
            info.ok = True
            return None, info

        info.state = "list"
        info.ok = True
        return html, info

    except Exception as e:
        info.state = "network_error"
        info.error = repr(e)
        return None, info
    finally:
        info.elapsed_s = round(time.time() - t0, 2)


def fetch_note(
    date_ddmmyyyy: str,
    cltype: str = "S",
    *,
    max_captcha_images: int = 4,
    guesses_per_image: int = 120,
    total_guess_budget: int = 300,
    inter_attempt_sleep_s: float = 0.4,
) -> tuple[Optional[bytes], FetchInfo]:
    """Fetch the listing-office NOTE (cl=N) for a date as raw bytes.

    format=2 asks the portal for its own PDF. The server answers the literal
    string 'NA' when no note exists for the date (probed live 18-Aug-2026:
    every shape returns NA when absent). Returns (bytes|None, info); bytes may
    be a PDF (%PDF magic) or small HTML — caller decides how to package.
    """
    info = FetchInfo(date=date_ddmmyyyy, ok=False, state="captcha_failed")
    t0 = time.time()
    try:
        ip, info.resolver = ensure_resolvable(HOST)
        if info.resolver != "system":
            log.warning(f"system DNS broken for {HOST}; using {ip} via {info.resolver}")
        opener = _build_opener()
        _open_retry(opener, FORM_URL, timeout=20, tries=5,
                    log=logging.getLogger('orch'))

        accepted = _solve_captcha(
            opener, info,
            max_images=max_captcha_images,
            guesses_per_image=guesses_per_image,
            total_guess_budget=total_guess_budget,
            inter_attempt_sleep_s=inter_attempt_sleep_s)
        if not accepted:
            info.error = (f"captcha not solved in {max_captcha_images} images "
                          f"/ {total_guess_budget} guesses")
            return None, info

        fields = {
            "cl": _b64("N"),
            "cldt": _b64(date_ddmmyyyy),
            "cltype": _b64(cltype),
            "format": _b64("2"),
            "QueryType": "Causelist",
        }
        status, body = _post(opener, LIST_URL,
                             urllib.parse.urlencode(fields).encode(), timeout=45)
        body = (body or "").strip()
        if not body or body.upper() == "NA" or "no record" in body.lower():
            info.state = "no_record"
            info.ok = True
            return None, info

        info.list_filename = body
        full_url = f"{BASE}/{SEAT_PATH}/{body}"
        try:
            with opener.open(full_url, timeout=45) as r:
                data = r.read()
        except urllib.request.HTTPError as e:
            if e.code in (404, 410):
                info.state = "no_record"
                info.ok = True
                info.error = f"note file 404 ({body})"
                return None, info
            raise
        # Notes can be genuinely tiny — only an empty/near-empty answer is
        # "nothing there" (unlike the 1500-byte floor on full cause lists).
        if len(data) < 200:
            info.state = "no_record"
            info.ok = True
            return None, info
        info.state = "list"
        info.ok = True
        return data, info
    except Exception as e:
        info.state = "network_error"
        info.error = repr(e)
        return None, info
    finally:
        info.elapsed_s = round(time.time() - t0, 2)
