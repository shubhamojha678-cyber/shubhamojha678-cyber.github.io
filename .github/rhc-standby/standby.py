#!/usr/bin/env python3
"""rhc-causelist STANDBY - keeps https://shubhamojhaandassociates.com/rhc-causelist/ updating
when the primary system (the Mac) is dead. Runs on a GitHub Actions runner (Linux, stdlib +
beautifulsoup4 + pillow). See STATUS.md next to this file.

  standby.py gate                          decide in seconds: idle | takeover | continue
  standby.py run --mode auto  --repo DIR   act (only when the gate says so): fetch, build, commit, push, verify
  standby.py run --mode drill --repo DIR   full fetch + build into a temp dir, validate, summary. NEVER pushes to main.
  standby.py run --mode pagestest --repo DIR   one EMPTY commit to main, then watch for the Pages build (owner-run, once)
  standby.py run --mode alerttest          open + close one test issue (owner-run, once)
  standby.py fetch-worker SEATPATH DD/MM/YYYY D|S OUT.json     (internal: one portal fetch, own process)

ACTIVATION (conservative): the standby acts only when rhc-causelist/status.json on main says the
primary is dead: written_at older than STALE_S (2 h 15 min; the primary republishes at least every
55 min), or the file already carries "standby": true (we took over earlier and the primary has not
published since). Anything else -> exit. The primary always wins: its publisher resets to
origin/main and replaces rhc-causelist/ wholesale, which removes every file written here.

NEVER WORSE: lists go through the primary's own ingest (vendored cldb.ingest_html / ops.attempt):
parser error, 0 rows, under 30% of the recent median, under 80% of the rows already published, or
"no record" after a published list -> the published seat block stays exactly as it is. A seat's
block for a date is rebuilt only when every list that is already published for it was fetched
again successfully in this run. Party names of sensitive matters are withheld by the primary's own
classifier and exporter (vendored byte for byte: cldb.sensitive_reason, export_static.build).
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("TZ", "Asia/Kolkata")        # every time written here is IST, like the primary
if hasattr(time, "tzset"):
    time.tzset()
sys.dont_write_bytecode = True

import argparse          # noqa: E402
import base64            # noqa: E402
import datetime as dt    # noqa: E402
import hashlib           # noqa: E402
import json              # noqa: E402
import re                # noqa: E402
import shutil            # noqa: E402
import signal            # noqa: E402
import subprocess        # noqa: E402
import tempfile          # noqa: E402
import types             # noqa: E402
import urllib.error      # noqa: E402
import urllib.request    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SUBDIR = "rhc-causelist"
BRANCH = os.environ.get("STANDBY_BRANCH") or "main"      # the override exists for runner tests on a side branch only
LIVE_URL = "https://shubhamojhaandassociates.com/rhc-causelist/"
STALE_S = 2 * 3600 + 15 * 60     # primary silent for longer than this = dead
HEARTBEAT_S = 45 * 60            # while active: refresh status.json at least this often
OFFHOURS_FETCH_S = 55 * 60       # while active, outside the court windows: fetch hourly
LIST_GAP_S = 3.0                 # pause between two list fetches
REQ_GAP_S = float(os.environ.get("STANDBY_REQ_GAP_S") or 1.0)      # pause between two portal requests
RETRY_WAIT_S = 30
SMALL_CONFIRM = True             # False = a held short list is never accepted by the standby, even for a date not yet published
FETCH_BUDGET_S = 10 * 60         # lists not reached inside this budget wait for the next run (their blocks stay)
WALL_CAP_S = 19 * 60
STATE_BRANCH = os.environ.get("STANDBY_STATE_BRANCH") or "rhc-standby-state"
ISSUE_PREFIX = "Cause-list standby active since "
BOT = "rhc-causelist standby"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
NOTE = ("The primary updating system is offline. The lists on this site are being refreshed by the standby "
        "system from the cause lists published on hcraj.nic.in; always confirm on hcraj.nic.in.")
FORBIDDEN = ("not listed", "no hearing", "case is not on the board", "definitely", "guaranteed")   # = publish.FORBIDDEN


def log(msg):
    print(f"{dt.datetime.now(IST).isoformat(timespec='seconds')} {msg}", flush=True)


def ist(ts: str) -> str:
    """'22-Nov-2026 14:30 IST' from an ISO time."""
    try:
        return dt.datetime.fromisoformat(ts).astimezone(IST).strftime("%d-%b-%Y %H:%M IST")
    except Exception:  # noqa: BLE001
        return "time unknown"


def now_ist() -> dt.datetime:
    o = os.environ.get("STANDBY_NOW")              # drills only
    return dt.datetime.fromisoformat(o).astimezone(IST) if o else dt.datetime.now(IST)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- the gate
def decide(status: dict | None, now: dt.datetime):
    """-> (decision, reason). decision: idle | takeover | continue | unknown.
    idle      the primary is alive (fresh status without the standby flag)
    takeover  the primary has been silent for more than STALE_S
    continue  we took over earlier and the primary has not published since
    unknown   the status cannot be read or has no usable time -> do NOT act (not proof of death)"""
    if not isinstance(status, dict):
        return "unknown", "status.json could not be read"
    try:
        age = (now - dt.datetime.fromisoformat(status["written_at"])).total_seconds()
    except Exception:  # noqa: BLE001
        return "unknown", "status.json has no usable written_at"
    if status.get("standby") is True:
        return "continue", f"standby active since {ist(status.get('standby_since') or status['written_at'])}"
    if age > STALE_S:
        return "takeover", f"primary last published {ist(status['written_at'])} ({age / 3600:.1f} h ago, limit {STALE_S / 3600:.2f} h)"
    return "idle", f"primary alive: published {ist(status['written_at'])} ({age / 60:.0f} min ago)"


# ---------------------------------------------------------------- GitHub REST (GITHUB_TOKEN only)
def gh(method: str, path: str, body=None, accept="application/vnd.github+json", timeout=30):
    """-> (http status, parsed json | bytes | None). Never raises on an HTTP status."""
    api = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    url = f"{api}/repos/{repo}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", "rhc-standby")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except Exception as e:  # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}
    if "raw" in accept:
        return code, raw
    try:
        return code, json.loads(raw.decode("utf-8")) if raw else None
    except Exception:  # noqa: BLE001
        return code, None


def have_api() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY"))


def issues_on() -> bool:
    return have_api() and os.environ.get("STANDBY_NO_ISSUE") != "1"      # the switch exists for runner tests only


def open_issue():
    """The ONE open standby issue (number, title) or None. Found by title, among the bot's own issues."""
    code, js = gh("GET", "/issues?state=open&per_page=100")
    if code != 200 or not isinstance(js, list):
        return None
    for it in js:
        if ("pull_request" not in it and str(it.get("title", "")).startswith(ISSUE_PREFIX)
                and (it.get("user") or {}).get("type") == "Bot"):
            return it["number"], it["title"]
    return None


def issue_takeover(status_old: dict, now: dt.datetime) -> str:
    """Open the issue once (GitHub e-mails the owner). -> what happened."""
    if not issues_on():
        return "no token / issues off: issue not opened"
    if open_issue():
        return "issue already open"
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or ""
    body = (f"The cause-list standby took over at **{ist(now.isoformat())}**.\n\n"
            f"- The primary system last published at {ist(str(status_old.get('written_at')))}; nothing since.\n"
            f"- The standby now fetches the Daily and Supplementary lists of both seats from hcraj.nic.in and updates "
            f"`{SUBDIR}/` on `main` (commits named `rhc-causelist: standby ...`).\n"
            f"- It stops by itself as soon as the primary publishes again, and then closes this issue.\n\n"
            f"What to check: is the Mac on, is it online, is the `clsearch` poller running.\n"
            + (f"\n@{owner}\n" if owner else ""))
    code, js = gh("POST", "/issues", {"title": ISSUE_PREFIX + ist(now.isoformat()), "body": body})
    return f"issue #{js.get('number')} opened" if code == 201 and isinstance(js, dict) else f"issue NOT opened (HTTP {code})"


def issue_comment(text: str) -> str:
    if not issues_on():
        return "no token / issues off"
    it = open_issue()
    if not it:
        return "no open issue"
    code, _ = gh("POST", f"/issues/{it[0]}/comments", {"body": text})
    return f"commented on #{it[0]} (HTTP {code})"


def issue_recovered(status: dict) -> str:
    """The primary is back: one comment, then close. Nothing when no issue is open."""
    if not issues_on():
        return "no token / issues off"
    it = open_issue()
    if not it:
        return "no open issue"
    gh("POST", f"/issues/{it[0]}/comments", {"body": f"The primary system is publishing again (status written "
                                                       f"{ist(str(status.get('written_at')))}). The standby has stood down."})
    code, _ = gh("PATCH", f"/issues/{it[0]}", {"state": "closed", "state_reason": "completed"})
    return f"issue #{it[0]} closed (HTTP {code})"


def read_status_remote():
    """status.json of main: REST contents API (exact), else raw.githubusercontent, else the live site.
    -> (dict | None, where)"""
    if have_api():
        code, raw = gh("GET", f"/contents/{SUBDIR}/status.json?ref={BRANCH}", accept="application/vnd.github.raw+json")
        if code == 200 and isinstance(raw, bytes):
            try:
                return json.loads(raw.decode("utf-8")), "api"
            except Exception:  # noqa: BLE001
                pass
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    for where, url in (("raw", f"https://raw.githubusercontent.com/{repo}/{BRANCH}/{SUBDIR}/status.json" if repo else ""),
                       ("live", LIVE_URL + "status.json")):
        if not url:
            continue
        try:
            req = urllib.request.Request(url + f"?cb={int(time.time())}", headers={"User-Agent": "rhc-standby", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8")), where
        except Exception:  # noqa: BLE001
            continue
    return None, "none"


def cmd_gate(_a) -> int:
    now = now_ist()
    status, where = read_status_remote()
    decision, reason = decide(status, now)
    log(f"GATE      {decision} - {reason} [status from {where}]")
    if decision == "idle":
        r = issue_recovered(status)
        if r not in ("no open issue", "no token / issues off"):
            log(f"ISSUE     {r}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"decision={decision}\nact={'true' if decision in ('takeover', 'continue') else 'false'}\n")
    return 1 if decision == "unknown" else 0       # unknown = a red run (GitHub mails the owner); never a takeover


# ---------------------------------------------------------------- the vendored primary code
_V = {}


def vendor(data_dir: str):
    """Import the vendored cldb / ops / export_static / site_build against a scratch data dir."""
    if _V:
        return _V["cldb"], _V["ops"], _V["export_static"], _V["site_build"]
    os.makedirs(data_dir, exist_ok=True)
    os.environ["CLSEARCH_DATA"] = data_dir
    os.environ["CLSEARCH_ALERT_DRY"] = "1"          # the primary's Telegram alerts never fire from here
    sys.path.insert(0, os.path.join(VENDOR, "clsearch"))
    import cldb
    import export_static
    import ops
    import site_build
    cldb.CAUSELIST_DIR = os.path.join(VENDOR, "causelist")      # parser + HC calendar: the vendored copies
    _V.update(cldb=cldb, ops=ops, export_static=export_static, site_build=site_build)
    return cldb, ops, export_static, site_build


def _stub_vision():
    """lib/ocr.py imports Apple Vision at module level. The standby never uses it (glyph templates
    only), so the three modules are satisfied by empty stand-ins - the vendored file stays unpatched."""
    for name, attrs in (("objc", ()), ("Foundation", ("NSURL",)),
                        ("Vision", ("VNImageRequestHandler", "VNRecognizeTextRequest", "VNRequestTextRecognitionLevelAccurate"))):
        m = types.ModuleType(name)
        for a in attrs:
            setattr(m, a, None)
        sys.modules[name] = m


def cmd_fetch_worker(a) -> int:
    """ONE portal fetch in its own process (the parent kills it on overrun)."""
    res = {"html": None, "state": "network_error", "error": "", "captcha": []}
    try:
        _stub_vision()
        sys.path.insert(0, os.path.join(VENDOR, "causelist"))
        from lib import fetch as F
        from lib import ocr
        ocr._vision_candidates = lambda png, limit: []     # no Vision: an unread image is refreshed instead
        F.captcha_learn = lambda *x, **k: None             # the vendored glyph library is read-only
        build = F._build_opener

        def paced():
            op = build()
            real, last = op.open, [0.0]

            def open_(*x, **k):
                wait = REQ_GAP_S - (time.time() - last[0])
                if wait > 0:
                    time.sleep(wait)
                try:
                    return real(*x, **k)
                finally:
                    last[0] = time.time()
            op.open = open_
            return op
        F._build_opener = paced
        with F.seat(a.seat_path):
            html, info = F.fetch_causelist(a.date, lawyer="", cltype=a.type, max_captcha_images=6,
                                           guesses_per_image=25, total_guess_budget=100)
        res = {"html": html, "state": info.state, "error": info.error or "",
               "captcha": [{k: e.get(k) for k in ("attempt", "result", "guess_rank", "candidates")} for e in info.captcha_log]}
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f)
    os.replace(tmp, a.out)
    return 0


_DATED = re.compile(r"Dated\s*:\s*(\d{2}/\d{2}/\d{4})")
SEAT_PATHS = {"jdp": "cishcraj-jdp", "jp": "cishcraj-jp"}
FETCH_LOG: list[dict] = []


def wrong_date(html: str, date_dmy: str) -> str:
    """'' or why the page is refused: its own masthead ("Monday Dated : 23/11/2026") names other days only."""
    dates = set(_DATED.findall(html or ""))
    return f"the page is dated {sorted(dates)[:3]}, asked for {date_dmy}" if dates and date_dmy not in dates else ""


def wrong_kind(html: str, list_type: str) -> str:
    """'' or why the page is refused: asked for the Daily list but the page's own masthead says
    Supplementary (or the other way round). Uses the primary's own detector (lib/parse.py); a page
    that does not say what it is passes."""
    try:
        if os.path.join(VENDOR, "causelist") not in sys.path:
            sys.path.insert(0, os.path.join(VENDOR, "causelist"))
        from lib.parse import declared_list_kind
        kind = declared_list_kind(html)
    except Exception:  # noqa: BLE001
        return ""
    return f"asked for the {list_type} list but the page declares itself {kind}" if kind in ("D", "S") and kind != list_type else ""


def guarded(fetcher):
    """Wrap a fetcher: a page for another date or of the other list kind is a failed attempt
    (stored rows untouched), never a list."""
    def f(seat, date_dmy, list_type):
        html, state, err = fetcher(seat, date_dmy, list_type)
        if state == "list" and html:
            why = wrong_date(html, date_dmy) or wrong_kind(html, list_type)
            if why:
                return None, "wrong_page", "wrong page: " + why
        return html, state, err
    return f


def portal_fetch(seat: str, date_dmy: str, list_type: str):
    """-> (html | None, state, error) like cldb.live_fetch, through OUR worker (no Vision, paced)."""
    out = os.path.join(tempfile.gettempdir(), f"rhc_standby_fetch_{os.getpid()}_{int(time.time() * 1000)}.json")
    timeout = int(180 + 60 * REQ_GAP_S)       # 240 s like the primary (a runner abroad saw single fetches of 117 s)
    t0 = time.time()
    rec = {"seat": seat, "date": date_dmy, "type": list_type}
    try:
        subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "fetch-worker", SEAT_PATHS[seat], date_dmy, list_type, out],
                       timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out, encoding="utf-8") as f:
            r = json.load(f)
        html, state, err = r["html"], r["state"], r.get("error") or ""
        rec["captcha"] = r.get("captcha") or []
    except subprocess.TimeoutExpired:
        html, state, err = None, "network_error", f"TimeoutError: fetch exceeded {timeout} s (worker killed)"
    except Exception as e:  # noqa: BLE001
        html, state, err = None, "network_error", f"{type(e).__name__}: {e}"
    finally:
        for p in (out, out + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass
    rec.update(state=state, error=err[:200], secs=round(time.time() - t0, 1), bytes=len(html or ""))
    FETCH_LOG.append(rec)
    return html, state, err


# ---------------------------------------------------------------- build the update
def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(obj, ascii_=False) -> bytes:
    return json.dumps(obj, ensure_ascii=ascii_, separators=(",", ":")).encode("utf-8")


def seed(con, meta: dict) -> dict:
    """Tell the scratch DB what is already published (state, rows, fetch time per list) - WITHOUT
    rows. The primary's own guards (size floor, 80% rule, no_record-after-list) then work here."""
    seeds = {}
    with con:
        for d, m in (meta.get("lists") or {}).items():
            for seat, tm in (m or {}).items():
                for t, v in (tm or {}).items():
                    v = list(v or []) + [None, None, None]
                    if v[0] not in ("list", "no_record") or seat not in SEAT_PATHS or t not in ("D", "S"):
                        continue
                    con.execute("INSERT OR REPLACE INTO lists(seat,date,list_type,state,rows,fetched_at,changed_at,"
                                "last_attempt_at,last_attempt_state,source) VALUES (?,?,?,?,?,?,?,?,?,'published')",
                                (seat, d, t, v[0], int(v[1] or 0), v[2], v[2], v[2], v[0]))
                    seeds[(seat, d, t)] = [v[0], int(v[1] or 0), v[2]]
    return seeds


def _revert(con, seat, d, seeds):
    """Forget what this run stored for one seat/date: the published block stays, so the list table
    must say exactly what is published."""
    with con:
        con.execute("DELETE FROM entries_fts WHERE rowid IN (SELECT id FROM entries WHERE seat=? AND date=?)", (seat, d))
        con.execute("DELETE FROM entries WHERE seat=? AND date=?", (seat, d))
        for t in ("D", "S"):
            s = seeds.get((seat, d, t))
            if s:
                con.execute("UPDATE lists SET state=?, rows=?, fetched_at=? WHERE seat=? AND date=? AND list_type=?",
                            (s[0], s[1], s[2], seat, d, t))
            else:
                con.execute("UPDATE lists SET state=NULL, rows=0, fetched_at=NULL WHERE seat=? AND date=? AND list_type=?",
                            (seat, d, t))


def check_day(doc: dict) -> str:
    """'' or why this day document must not be published. Works on the site shape and the full shape."""
    try:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", doc["date"]) or not isinstance(doc["seats"], dict):
            return "bad shape"
        total = 0
        for seat, m in doc["lists"].items():
            courts = (doc["seats"].get(seat) or {}).get("courts") or []
            rows = [r for c in courts for r in c["r"]]
            total += len(rows)
            for r in rows:
                if len(r) != 7 or r[6] not in (0, 1, 2) or not r[1]:
                    return f"{seat}: malformed row"
                if (r[2] == 0) != (r[3] == 0):
                    return f"{seat}: a row withholds only one party name"
            nd = sum(1 for r in rows if r[6] in (0, 2))
            ns = sum(1 for r in rows if r[6] == 1)
            dv, sv = m.get("D") or [None, 0], m.get("S") or [None, 0]
            if (dv[0] == "list") != (nd > 0) or (dv[0] == "list" and nd != dv[1]):
                return f"{seat}: Daily says {dv[:2]} but the file has {nd} Daily rows"
            if sv[0] == "list" and (ns > sv[1] or (ns + sum(1 for r in rows if r[6] == 2)) == 0):
                return f"{seat}: Supplementary says {sv[:2]} but the file has {ns} Supplementary-only rows"
            if sv[0] != "list" and (ns or any(r[6] == 2 for r in rows)):
                return f"{seat}: Supplementary rows without a held Supplementary list"
        for seat in doc["seats"]:
            if seat not in doc["lists"]:
                return f"{seat}: rows without a list entry"
        return "" if total else "no rows"
    except Exception as e:  # noqa: BLE001
        return f"unreadable: {type(e).__name__}: {e}"


def check_redaction(con, cldb, d: str, seat: str, block: dict) -> str:
    """'' or why a FRESH seat block fails the privacy check. Two independent tests:
    1. row by row against the DB: every row the classifier marked sensitive (or of a list without
       class codes) carries no party name;
    2. every row that still shows names is re-classified from its public fields alone and must be clean."""
    rows = [r for c in block.get("courts") or [] for r in c["r"]]
    db = con.execute("SELECT * FROM listings WHERE date=? AND seat=? ORDER BY seat, list_type, court_seq, seq", (d, seat)).fetchall()
    if len(db) != len(rows):
        return f"{len(rows)} rows in the block, {len(db)} in the DB"
    blind = {r["list_type"] for r in con.execute(
        "SELECT list_type FROM entries WHERE date=? AND seat=? GROUP BY list_type "
        "HAVING sum(class_code = '' OR class_code IS NULL) * 10 > count(*)", (d, seat))}
    for e, r in zip(db, rows):
        if e["case_no"] != r[1]:
            return "row order differs from the DB"
        must = bool(e["sensitive"]) or e["list_type"] in blind
        if must and (r[2] != 0 or r[3] != 0):
            return f"{r[1]}: sensitive ({e['sensitive'] or 'no class codes'}) but names are present"
        if not must and (r[2] == 0 or r[3] == 0):
            return f"{r[1]}: names withheld without a reason"
        if r[2] != 0 and cldb.sensitive_reason(r[1], "", "", r[2] or "", r[3] or ""):
            return f"{r[1]}: public fields alone classify as sensitive but names are present"
    return ""


def shape(doc: dict):
    """(top-level keys, keys of a court object) of a day document - its FORMAT, not its content."""
    return ({k for k in doc if k != "attribution"},
            {k for s_ in (doc.get("seats") or {}).values() for c in s_.get("courts") or [] for k in c})


def older_than_published(fresh: dict, published: dict) -> str:
    """'' or why the vendored exporter is OLDER than the code that built the published files: the
    published format has fields this package cannot write. Writing such a file under the newer
    app.js could lose information, so the day is refused (re-sync: vendor_sync.py + place.sh).
    A package NEWER than the published files is fine (the primary was just updated)."""
    ft, fc = shape(fresh)
    pt, pc = shape(published)
    miss = sorted((pt - ft) | (pc - fc - {"b", "s", "p"}))      # b / s / p are optional per court
    return f"published day files carry field(s) {miss} that this standby package cannot write (package out of date)" if miss else ""


class Plan:
    def __init__(self):
        self.files: dict[str, bytes] = {}        # path relative to rhc-causelist/ -> bytes
        self.deletes: list[str] = []
        self.material = False
        self.why: list[str] = []
        self.summary: dict = {}
        self.base_meta_sha = ""
        self.status_sha = ""


def landing_section(sb, days: dict, want: list, seat: str, today_iso: str):
    """(the <p class="sum"> lines, the text between <!--summary--> and <!--/summary-->) exactly as
    site_build.landing_html writes them. Reuses site_build.day_line / court_summary: no party names."""
    e, city = sb.e, sb.SHORT[seat]
    sums = "".join(f'<p class="sum"><b>{e(sb.day_line(days[d], seat, d, today_iso))}</b></p>' for d in want)
    body = []
    for d in want:
        day = days[d]
        rows = sb.court_summary(day, seat)
        body.append(f'<div class="card"><h2>{city} cause list, {dt.date.fromisoformat(d):%a %d %b %Y}<span data-day="{d}"></span></h2>'
                    f'<p class="hint">{e(sb.day_line(day, seat, d, today_iso))} — {sb.CONFIRM}</p>')
        if rows:
            body.append('<div class="tw"><table><tr><th>Court</th><th>Bench (as printed in the list)</th><th class="n">Items</th><th class="n">List</th></tr>')
            for r in rows:
                court = f"Court {r['court']}" if r["court"].isdigit() else r["court"]
                body.append(f"<tr><td>{e(court)}</td><td>{e(r['bench'])}"
                            + (f"<br><small>{e(r['sitting'])}</small>" if r["sitting"] else "")
                            + f"</td><td class=\"n\">{r['items']}</td><td class=\"n\">{e(r['types'])}</td></tr>")
            body.append("</table></div>")
        else:
            body.append(f"<p>The list for this date is not yet available to us — {sb.CONFIRM}.</p>")
        body.append("</div>")
    if not want:
        body.append(f'<div class="card"><p>No list is available to us yet — {sb.CONFIRM}.</p></div>')
    return sums, "\n".join(body)


_SUMS = re.compile(r'(?:<p class="sum"><b>.*?</b></p>)+')
_SECT = re.compile(r"(<!--summary-->\n)(.*?)(\n<!--/summary-->)", re.S)


def _want(days, today_iso):
    return [d for d in sorted(days) if d >= today_iso][:2] or sorted(days)[-1:]


def landing_update(sb, html: str, seat: str, old_days: dict, old_today: str, new_days: dict, new_today: str):
    """-> new html, or None when the page must be left alone. SELF-CHECK first: the summary of the
    page as published must be reproduced byte for byte from the published day files. If the page
    layout has moved on (site_build changed) the check fails and the page is not touched."""
    try:
        m_sum, m_sec = _SUMS.search(html), _SECT.search(html)
        if not m_sum or not m_sec:
            return None
        o_sum, o_sec = landing_section(sb, old_days, _want(old_days, old_today), seat, old_today)
        if m_sum.group(0) != o_sum or m_sec.group(2) != o_sec:
            return None
        n_sum, n_sec = landing_section(sb, new_days, _want(new_days, new_today), seat, new_today)
        out = html[:m_sec.start(2)] + n_sec + html[m_sec.end(2):]
        out = out[:m_sum.start()] + n_sum + out[m_sum.end():]
        low = out.lower()
        return None if any(w in low for w in FORBIDDEN) else out
    except Exception:  # noqa: BLE001
        return None


def plan_update(site: str, work: str, now: dt.datetime, *, fetch=True, fetcher=None, sleep=time.sleep,
                force_material=False) -> Plan:
    """Read the published tree at `site` (never written), fetch, and return what would change."""
    cldb, ops, export_static, sb = vendor(os.path.join(work, "data"))
    fetcher = guarded(fetcher or portal_fetch)
    plan = Plan()
    meta_raw = open(os.path.join(site, "data", "meta.json"), "rb").read()
    plan.base_meta_sha = sha(meta_raw)
    meta = json.loads(meta_raw.decode("utf-8"))
    status_old = _load(os.path.join(site, "status.json"))
    for k in ("site_dates", "dates", "days", "lists"):
        if k not in meta:
            raise RuntimeError(f"meta.json has no '{k}': format changed, standby must not write")
    db = os.path.join(work, "data", "clsearch.db")
    for p in (db, db + "-wal", db + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    con = cldb.connect(db)
    seeds = seed(con, meta)
    today = now.date()
    nxt, tg = ops.targets(now)
    results = {}
    # A short list (under 30% of the median / under 80% of the published rows) is HELD.
    #  - a list that is ALREADY PUBLISHED is never replaced by a held short one: the file stays;
    #  - a list NOT yet published is accepted only when two fetches in two RUNS (15 min or more
    #    apart) give exactly the same cases (the primary's own rule, stricter): the first sight is
    #    remembered in status.json ("standby_small").
    def _small_ok(k):
        m = re.match(r"^small:(jdp|jp):(\d{4}-\d{2}-\d{2}):([DS])$", str(k))
        return bool(m) and SMALL_CONFIRM and (seeds.get((m.group(1), m.group(2), m.group(3))) or [None])[0] != "list"
    small_old = status_old.get("standby_small") if status_old.get("standby") is True else None
    small_old = {k: v for k, v in (small_old if isinstance(small_old, dict) else {}).items()
                 if _small_ok(k) and re.match(r"^\d+:[0-9a-f]{64}$", str(v))}
    for k, v in small_old.items():
        ops._kv(con, k, v)
    if fetch:
        # the seat that goes first alternates from run to run, so a slow portal never starves one seat
        order = ("jdp", "jp") if (now.hour * 4 + now.minute // 15) % 2 == 0 else ("jp", "jdp")
        pending = [(seat, d, t, 1) for seat in order for d, t in tg]
        first, t0 = True, time.time()
        for round_ in (1, 2):
            again = []
            if round_ == 2 and pending and time.time() - t0 < FETCH_BUDGET_S:
                sleep(RETRY_WAIT_S)
            for seat, d, t, n in pending:
                if time.time() - t0 >= FETCH_BUDGET_S:
                    log(f"BUDGET    {seat} {d:%d/%m/%Y} {t} not fetched: {FETCH_BUDGET_S} s fetch budget used - left for the next run")
                    results.setdefault((seat, d.isoformat(), t), ("not_attempted", "fetch budget used"))
                    continue
                if not first:
                    sleep(LIST_GAP_S)
                first = False
                cls, res = ops.attempt(con, seat, d.strftime("%d/%m/%Y"), t, fetcher=fetcher, attempt_no=n)
                results[(seat, d.isoformat(), t)] = (cls, res)
                log(f"{cls.upper():<12} {seat} {d:%d/%m/%Y} {t} {res} try#{n}")
                if cls in ops.FAIL and cls != "parse_fail":      # a held list is never asked again in the same run
                    again.append((seat, d, t, n + 1))
            pending = again
    small_new = {r[0]: r[1] for r in con.execute("SELECT k, v FROM kv WHERE k LIKE 'small:%' AND v <> ''") if _small_ok(r[0])}
    if not fetch:
        small_new = dict(small_old)
    # ---- per target date: which seat blocks are entirely fresh, which stay as published
    att = meta.get("attribution") or sb.ATTRIBUTION
    new_meta = json.loads(meta_raw.decode("utf-8"))
    day_info, full_days = {}, {}
    ref = None                                             # the newest published day file = the format in force
    for d_ in reversed(meta["site_dates"]):
        p_ = os.path.join(site, "data", "d", f"cl_{d_}.json")
        if os.path.exists(p_):
            ref = _load(p_)
            break
    for d in sorted({x.isoformat() for x, _ in tg}):
        kept, fresh = [], []
        for seat in ("jdp", "jp"):
            rows = {r["list_type"]: r for r in con.execute("SELECT * FROM lists WHERE seat=? AND date=?", (seat, d))}
            if not rows:
                continue
            have = {t: con.execute("SELECT count(*) FROM entries WHERE seat=? AND date=? AND list_type=?", (seat, d, t)).fetchone()[0]
                    for t in rows}
            if any(r["state"] == "list" and not have[t] for t, r in rows.items()):
                _revert(con, seat, d, seeds)             # a published list was not fetched again: the block stays
                kept.append(seat)
            elif any(have.values()):
                fresh.append(seat)
        live_path = os.path.join(site, "data", "d", f"cl_{d}.json")
        live = _load(live_path) if os.path.exists(live_path) else None
        data = export_static.build(con, d, redact=True)
        data.pop("generated_at", None)
        seats, bad = {}, ""
        for seat in ("jdp", "jp"):
            if seat in kept:
                if live and seat in live.get("seats", {}):
                    seats[seat] = live["seats"][seat]
            elif seat in data["seats"]:
                seats[seat] = data["seats"][seat]
                bad = bad or check_redaction(con, cldb, d, seat, seats[seat])
                want = con.execute("SELECT count(*) FROM listings WHERE date=? AND seat=?", (d, seat)).fetchone()[0]
                if sum(len(c["r"]) for c in seats[seat]["courts"]) != want:
                    bad = bad or f"{seat}: block rows differ from the DB"
        data["seats"] = seats
        info = {"fresh": fresh, "kept": kept, "result": "nothing held"}
        day_info[d] = info
        if not any(v[0] == "list" for m in data["lists"].values() for v in m.values()):
            continue                                       # no list held for this date: no file, no claim
        slim = dict({"attribution": att}, **data)
        slim["lists"] = {s_: {t: v[:2] for t, v in m.items()} for s_, m in data["lists"].items()}
        bad = bad or check_day(slim)
        if not bad and fresh and ref is not None:
            bad = older_than_published({"v": 1, "date": d, "lists": {}, "seats": {s_: seats[s_] for s_ in fresh}}, ref)
        if bad:
            info["result"] = f"REFUSED ({bad}); published file kept"
            log(f"REFUSED   {d}: {bad}")
            for seat in fresh:                             # the list table must again say what is published
                _revert(con, seat, d, seeds)
            if live and d in meta["lists"]:
                full_days[d] = dict(live, lists=meta["lists"][d])
            continue
        if live is not None and {k: v for k, v in live.items() if k != "attribution"} == {k: v for k, v in slim.items() if k != "attribution"}:
            info["result"] = "unchanged"
            info["rows"] = sum(len(c["r"]) for s_ in slim["seats"].values() for c in s_["courts"])
        else:
            raw = _dump(slim)
            plan.files[f"data/d/cl_{d}.json"] = raw
            new_meta["days"][d] = sha(raw)[:10]
            info["result"] = "new" if live is None else "replaced"
            info["rows"] = sum(len(c["r"]) for s_ in slim["seats"].values() for c in s_["courts"])
            info["withheld"] = sum(1 for s_ in slim["seats"].values() for c in s_["courts"] for r in c["r"] if r[2] == 0)
            plan.why.append(f"{d} {info['result']}")
        if d in new_meta["days"]:
            new_meta["lists"][d] = data["lists"]
            if d not in new_meta["dates"]:
                new_meta["dates"] = sorted(set(new_meta["dates"]) | {d})
        full_days[d] = dict(slim, lists=data["lists"])
        if live is not None:
            info["parity"] = {s_: ("identical" if live.get("seats", {}).get(s_) == slim["seats"].get(s_) else
                                   f"differs: fresh {sum(len(c['r']) for c in slim['seats'][s_]['courts'])} rows, published "
                                   f"{sum(len(c['r']) for c in (live.get('seats', {}).get(s_) or {}).get('courts', []))} rows")
                              for s_ in fresh}
    # ---- meta: the dates shown, pruned like the primary (last SITE_DAYS days + future)
    cutoff = (today - dt.timedelta(days=int(meta.get("site_days") or sb.SITE_DAYS))).isoformat()
    for d in list(new_meta["days"]):
        if d < cutoff:
            new_meta["days"].pop(d, None)
            new_meta["lists"].pop(d, None)
            plan.deletes.append(f"data/d/cl_{d}.json")
            plan.why.append(f"{d} pruned")
    new_meta["site_dates"] = sorted(new_meta["days"])
    new_meta["lists"] = {d: v for d, v in new_meta["lists"].items() if d in new_meta["days"]}
    new_meta["published_at"] = now.isoformat(timespec="seconds")
    # ---- status: the primary's own verdict rules on what is held now
    pub = sb.public_status(con, now)
    status = dict({"attribution": status_old.get("attribution") or att}, **pub)
    status["standby"] = True
    status["standby_since"] = status_old.get("standby_since") if status_old.get("standby") is True else now.isoformat(timespec="seconds")
    status["standby_last_fetch_at"] = now.isoformat(timespec="seconds") if fetch else status_old.get("standby_last_fetch_at")
    status["standby_note"] = NOTE
    if small_new:
        status["standby_small"] = small_new
    if small_new != small_old:
        plan.why.append("held list remembered")
    try:
        old_ess = sb.status_essence(status_old)
        age = (now - dt.datetime.fromisoformat(status_old["written_at"])).total_seconds()
    except Exception:  # noqa: BLE001
        old_ess, age = "", 1e9
    if sb.status_essence(pub) != old_ess:
        plan.why.append("status changed")
    if status_old.get("standby") is not True:
        plan.why.append("takeover")
    elif age >= HEARTBEAT_S:
        plan.why.append("heartbeat")
    if force_material:
        plan.why.append("forced")
    plan.material = bool(plan.why)
    plan.files["data/meta.json"] = _dump(new_meta, ascii_=True)
    plan.files["status.json"] = _dump(status)
    plan.status_sha = sha(plan.files["status.json"])
    # ---- landing pages: only the court-wise summary block, only when the self-check reproduces the page
    try:
        old_today = dt.datetime.fromisoformat(status_old["written_at"]).date().isoformat()
        old_days, new_days = {}, {}
        for d in meta["site_dates"]:
            p = os.path.join(site, "data", "d", f"cl_{d}.json")
            if d >= min(old_today, today.isoformat()) or d == meta["site_dates"][-1]:
                old_days[d] = dict(_load(p), lists=meta["lists"].get(d) or {})
        for d in new_meta["site_dates"]:
            if d in full_days:
                new_days[d] = full_days[d]
            elif d in old_days:
                new_days[d] = old_days[d]
            elif d >= today.isoformat() or d == new_meta["site_dates"][-1]:
                new_days[d] = dict(_load(os.path.join(site, "data", "d", f"cl_{d}.json")), lists=new_meta["lists"].get(d) or {})
        landing = {}
        for seat, slug in (("jdp", "jodhpur"), ("jp", "jaipur")):
            p = os.path.join(site, slug, "index.html")
            html = open(p, encoding="utf-8").read()
            new = landing_update(sb, html, seat, old_days, old_today, new_days, today.isoformat())
            if new is None:
                landing[slug] = "left as published (self-check did not reproduce the page)"
            elif new != html:
                plan.files[f"{slug}/index.html"] = new.encode("utf-8")
                landing[slug] = "summary refreshed"
            else:
                landing[slug] = "unchanged"
    except Exception as e:  # noqa: BLE001 - a landing page problem never stops a data update
        landing = {"error": f"{type(e).__name__}: {e}"}
    plan.summary = {
        "at": now.isoformat(timespec="seconds"), "next_hearing_day": nxt.isoformat(), "fetched": bool(fetch),
        "lists": [{"seat": k[0], "date": k[1], "type": k[2], "class": v[0], "result": v[1].split(" [held:")[0][:120]}
                  for k, v in sorted(results.items())],
        "fetch_log": list(FETCH_LOG),
        "days": day_info, "landing": landing, "verdict": pub["verdict"],
        "seat_verdicts": {s_: v["verdict"] for s_, v in pub["seats"].items()},
        "reasons": {s_: v["reasons"] for s_, v in pub["seats"].items() if v["reasons"]},
        "material": plan.material, "why": plan.why,
        "files": sorted(plan.files), "deletes": plan.deletes,
    }
    con.close()
    return plan


def apply_plan(sub: str, plan: Plan):
    for rel in plan.deletes:
        p = os.path.join(sub, rel)
        if os.path.exists(p):
            os.remove(p)
    for rel, raw in plan.files.items():
        p = os.path.join(sub, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "wb") as f:
            f.write(raw)
        os.replace(p + ".tmp", p)


def check_tree(sub: str) -> list[str]:
    """What the browser (app.js) needs from the tree. -> list of problems (empty = consistent)."""
    bad = []
    try:
        meta = _load(os.path.join(sub, "data", "meta.json"))
        st = _load(os.path.join(sub, "status.json"))
    except Exception as e:  # noqa: BLE001
        return [f"meta.json / status.json unreadable: {e}"]
    for k in ("site_dates", "dates", "days", "lists", "cx", "cn", "published_at", "site_days"):
        if k not in meta:
            bad.append(f"meta.json: '{k}' missing")
    if bad:
        return bad
    if meta["site_dates"] != sorted(meta["days"]):
        bad.append("site_dates is not the sorted key list of days")
    on_disk = sorted(n[3:13] for n in os.listdir(os.path.join(sub, "data", "d")) if re.match(r"^cl_\d{4}-\d{2}-\d{2}\.json$", n))
    if on_disk != meta["site_dates"]:
        bad.append(f"day files on disk {sorted(set(on_disk) ^ set(meta['site_dates']))} do not match site_dates")
    for d in meta["site_dates"]:
        p = os.path.join(sub, "data", "d", f"cl_{d}.json")
        if not os.path.exists(p):
            bad.append(f"{d}: day file missing")
            continue
        raw = open(p, "rb").read()
        if sha(raw)[:10] != meta["days"][d]:
            bad.append(f"{d}: hash in meta.json does not match the file")
        doc = json.loads(raw.decode("utf-8"))
        if doc.get("date") != d:
            bad.append(f"{d}: file is for {doc.get('date')}")
        why = check_day(doc)
        if why:
            bad.append(f"{d}: {why}")
        if d not in meta["lists"]:
            bad.append(f"{d}: no lists entry in meta.json")
        elif {s_: {t: v[:2] for t, v in m.items()} for s_, m in meta["lists"][d].items()} != doc["lists"]:
            bad.append(f"{d}: lists in meta.json differ from the day file")
        if d not in meta["dates"]:
            bad.append(f"{d}: not in dates")
    for d in meta["lists"]:
        if d not in meta["days"]:
            bad.append(f"{d}: lists entry without a day file")
    for k in ("written_at", "next_hearing_day", "verdict", "seats"):
        if k not in st:
            bad.append(f"status.json: '{k}' missing")
    for seat, v in (st.get("seats") or {}).items():       # never claim a list is held when it is not
        for x in v.get("lists") or []:
            m = ((meta["lists"].get(x["hearing_date"]) or {}).get(seat) or {}).get(x["type"])
            if x["state"] == "list" and (not m or m[0] != "list" or m[1] != x["rows"]):
                bad.append(f"status.json says {seat} {x['type']} {x['hearing_date']} is held ({x['rows']}) but meta.json says {m}")
    return bad


# ---------------------------------------------------------------- git
def git(args, cwd, check=True, timeout=300):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME=BOT, GIT_COMMITTER_NAME=BOT,
               GIT_AUTHOR_EMAIL=BOT_EMAIL, GIT_COMMITTER_EMAIL=BOT_EMAIL)
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if check and r.returncode:
        raise RuntimeError(f"git {args[0]} failed rc={r.returncode}: {_mask((r.stderr or r.stdout).strip()[-300:])}")
    return r


def _mask(s: str) -> str:
    return re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", s or "")


def commit_and_push(repo: str, plan: Plan, push_url: str, now: dt.datetime, between_tries=None):
    """Apply the plan to <repo>/rhc-causelist, commit ONLY that folder, push to main.
    -> (outcome, detail). outcome: pushed | identical | primary-returned | base-changed | FAILED"""
    sub = os.path.join(repo, SUBDIR)
    last = ""
    for attempt in (1, 2, 3):
        if attempt > 1:
            git(["fetch", "--depth", "1", "origin", BRANCH], repo)
            git(["reset", "--hard", "FETCH_HEAD"], repo)
            git(["clean", "-fdq", "--", SUBDIR], repo)
            decision, reason = decide(_load(os.path.join(sub, "status.json")), now)
            if decision not in ("takeover", "continue"):
                return "primary-returned", reason             # the primary pushed first: it wins, nothing is written
            if sha(open(os.path.join(sub, "data", "meta.json"), "rb").read()) != plan.base_meta_sha:
                return "base-changed", "rhc-causelist/ changed on main while this run was building; left for the next run"
        apply_plan(sub, plan)
        bad = check_tree(sub)
        if bad:
            git(["reset", "--hard", "HEAD"], repo)
            git(["clean", "-fdq", "--", SUBDIR], repo)
            return "FAILED", "tree check: " + "; ".join(bad[:4])
        git(["add", "-A", "--", SUBDIR], repo, timeout=600)
        if not git(["status", "--porcelain", "--", SUBDIR], repo).stdout.strip():
            return "identical", "nothing to commit"
        other = [ln for ln in git(["status", "--porcelain"], repo).stdout.splitlines() if not ln[3:].strip().strip('"').startswith(SUBDIR + "/")]
        if other:
            git(["reset", "--hard", "HEAD"], repo)
            return "FAILED", f"paths outside {SUBDIR}/ would be committed: {other[:3]}"
        git(["commit", "-q", "-m", f"rhc-causelist: standby data {plan.status_sha[:16]}\n\n"
                                   f"Primary system offline; lists refreshed by the standby ({', '.join(plan.why)[:200]})."], repo)
        commit = git(["rev-parse", "HEAD"], repo).stdout.strip()
        if between_tries:
            between_tries(attempt)                           # drills: lets the primary push in between
        r = git(["push", push_url, f"HEAD:refs/heads/{BRANCH}"], repo, check=False, timeout=600)
        if r.returncode == 0:
            return "pushed", commit
        full = (r.stderr or "") + (r.stdout or "")
        last = _mask(full.strip()[-300:])
        if not any(w in full for w in ("non-fast-forward", "fetch first", "[rejected]", "stale info")):
            return "FAILED", f"git push failed: {last}"
        log(f"PUSH      rejected (someone pushed first), retry {attempt}/3")
    return "FAILED", f"push still rejected after 3 tries: {last}"


def push_state(push_url: str, files: dict, message: str) -> str:
    """One orphan commit holding `files`, force-pushed to the state branch (never main, never Pages)."""
    d = tempfile.mkdtemp(prefix="rhc-standby-state-")
    try:
        git(["init", "-q"], d)
        for name, raw in files.items():
            with open(os.path.join(d, name), "wb") as f:
                f.write(raw)
        git(["add", "-A"], d)
        git(["commit", "-q", "-m", message], d)
        git(["push", "--force", push_url, f"HEAD:refs/heads/{STATE_BRANCH}"], d, timeout=300)
        return f"state branch {STATE_BRANCH} updated"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- Pages
def live_status_sha(timeout=20) -> str:
    req = urllib.request.Request((os.environ.get("STANDBY_LIVE_URL") or LIVE_URL) + f"status.json?cb={int(time.time())}",
                                 headers={"User-Agent": "rhc-standby-verify/1", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return sha(r.read())


def verify_live(want_sha: str, wait_s=240, every_s=15, sleep=time.sleep, fetch=live_status_sha, nudge_after_s=100):
    """Poll the live status.json until it is the one just pushed. If it has not changed after
    nudge_after_s, ask GitHub once for a Pages build (REST: POST /pages/builds). -> (ok, detail)"""
    t0, last, nudged = time.time(), "no answer", ""
    while True:
        try:
            got = fetch()
            if got == want_sha:
                return True, f"live status.json matches after {int(time.time() - t0)} s{nudged}"
            last = f"live is {got[:12]}, want {want_sha[:12]}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if not nudged and time.time() - t0 >= nudge_after_s and have_api():
            code, _ = gh("POST", "/pages/builds")
            nudged = f" (Pages build requested, HTTP {code})"
            log(f"PAGES     build requested: HTTP {code}")
        if time.time() - t0 + every_s > wait_s:
            return False, last + nudged
        sleep(every_s)


# ---------------------------------------------------------------- run
def fetch_due(status_old: dict, now: dt.datetime, cldb) -> tuple[bool, str]:
    """While active: every run inside the court windows (the primary's own windows), hourly outside."""
    if status_old.get("standby") is not True:
        return True, "takeover"
    today, hm = now.date(), now.hour * 60 + now.minute
    court_today = bool(cldb.sitting_day(today))
    court_tomorrow = bool(cldb.sitting_day(today + dt.timedelta(days=1)))
    if (court_today and 8 * 60 <= hm <= 16 * 60 + 30) or ((court_today or court_tomorrow) and 17 * 60 <= hm <= 23 * 60):
        return True, "active window"
    try:
        age = (now - dt.datetime.fromisoformat(status_old["standby_last_fetch_at"])).total_seconds()
    except Exception:  # noqa: BLE001
        return True, "no earlier fetch recorded"
    return (age >= OFFHOURS_FETCH_S), f"off-hours (hourly): last fetch {age / 60:.0f} min ago"


def drill_doc(plan: Plan, tree_bad: list, ok: bool, notes: list) -> dict:
    s = plan.summary
    return {"v": 1, "kind": "rhc-standby drill", "at": s["at"], "at_ist": ist(s["at"]), "ok": ok, "notes": notes,
            "python": sys.version.split()[0], "run": os.environ.get("GITHUB_RUN_ID") or "local",
            "next_hearing_day": s["next_hearing_day"], "lists": s["lists"], "fetch_log": s["fetch_log"], "days": s["days"],
            "landing": s["landing"], "verdict": s["verdict"], "tree_check": tree_bad or "consistent"}


def run_drill(repo: str, now: dt.datetime, push_url: str | None) -> int:
    """Full fetch + build into a TEMP copy. Never touches the repo checkout, never pushes to main."""
    work = tempfile.mkdtemp(prefix="rhc-standby-drill-")
    site = os.path.join(repo, SUBDIR)
    plan = plan_update(site, work, now, fetch=True, force_material=True)
    tmp = os.path.join(work, "tree")
    for rel in ("status.json", "data/meta.json"):
        os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
        shutil.copyfile(os.path.join(site, rel), os.path.join(tmp, rel))
    shutil.copytree(os.path.join(site, "data", "d"), os.path.join(tmp, "data", "d"))
    apply_plan(tmp, plan)
    bad = check_tree(tmp)
    s = plan.summary
    notes = []
    fails = [x for x in s["lists"] if x["class"] in ("server_down", "captcha_fail", "local_net", "parse_fail")]
    if fails:
        notes.append(f"{len(fails)} list fetch(es) failed: " + ", ".join(f"{x['seat']} {x['type']} {x['date']} {x['class']}" for x in fails))
    refused = [d for d, i in s["days"].items() if str(i["result"]).startswith("REFUSED")]
    if refused:
        notes.append(f"day(s) refused: {refused}")
    got = [x for x in s["lists"] if x["class"] == "ok"]
    if not got:
        notes.append("no list was fetched successfully")
    ok = not bad and not fails and not refused and bool(got)
    doc = drill_doc(plan, bad, ok, notes)
    print(json.dumps(doc, indent=1, ensure_ascii=False))
    log(f"DRILL     {'PASS' if ok else 'FAIL'} - lists ok {len(got)}/{len(s['lists'])}; tree {'consistent' if not bad else bad[:3]}; "
        f"nothing was pushed to {BRANCH}")
    if push_url:
        try:
            log("STATE     " + push_state(push_url, {"drill.json": json.dumps(doc, indent=1, ensure_ascii=False).encode("utf-8")},
                                          f"rhc-standby drill {doc['at_ist']}: {'PASS' if ok else 'FAIL'}"))
        except Exception as e:  # noqa: BLE001
            log(f"STATE     drill result NOT pushed: {e}")
            return 1
    shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


def run_auto(repo: str, now: dt.datetime, push_url: str | None) -> int:
    site = os.path.join(repo, SUBDIR)
    status_old = _load(os.path.join(site, "status.json"))
    decision, reason = decide(status_old, now)
    log(f"DECIDE    {decision} - {reason}")
    if decision not in ("takeover", "continue"):
        return 1 if decision == "unknown" else 0                # the primary is alive: nothing to do
    if not push_url:
        log("ABORT     no push URL (STANDBY_PUSH_URL): auto mode cannot publish")
        return 1
    work = tempfile.mkdtemp(prefix="rhc-standby-")
    cldb = vendor(os.path.join(work, "data"))[0]
    do_fetch, why = fetch_due(status_old, now, cldb)
    log(f"FETCH     {'yes' if do_fetch else 'no'} - {why}")
    plan = plan_update(site, work, now, fetch=do_fetch)
    log("PLAN      " + json.dumps({k: plan.summary[k] for k in ("days", "landing", "verdict", "material", "why")}, ensure_ascii=False))
    if decision == "takeover":
        log("ISSUE     " + issue_takeover(status_old, now))
    if not plan.material:
        log("PUBLISH   skipped (no list changed, status reads the same, heartbeat not due)")
        return 0
    outcome, detail = commit_and_push(repo, plan, push_url, now)
    log(f"PUBLISH   {outcome} {detail}")
    if outcome == "primary-returned":
        return 0
    if outcome in ("identical", "base-changed"):
        return 0
    if outcome != "pushed":
        log("ISSUE     " + issue_comment(f"Standby run at {ist(now.isoformat())} could not publish: {detail}"))
        return 1
    ok, vd = verify_live(plan.status_sha)
    log(f"VERIFY    {'ok' if ok else 'NOT CONFIRMED'} - {vd}")
    if not ok:
        log("ISSUE     " + issue_comment(f"Standby pushed `{detail[:10]}` at {ist(now.isoformat())} but the live page did not show it "
                                         f"within 4 min ({vd}). GitHub Pages may be slow or not building."))
        return 1
    if plan.summary["verdict"] == "RED" and status_old.get("verdict") != "RED":
        log("ISSUE     " + issue_comment(f"Standby status turned RED at {ist(now.isoformat())}: "
                                         + "; ".join(f"{s_}: {', '.join(r)}" for s_, r in plan.summary["reasons"].items())))
    return 0


def run_pagestest(repo: str, push_url: str | None) -> int:
    """Owner-run once after activation: does a push made with GITHUB_TOKEN redeploy Pages? One EMPTY
    commit to main (no file changes), then GET /pages/builds/latest until it names that commit."""
    if not push_url or not have_api():
        log("PAGESTEST needs STANDBY_PUSH_URL and GITHUB_TOKEN")
        return 1
    code, before = gh("GET", "/pages/builds/latest")
    git(["commit", "-q", "--allow-empty", "-m", "rhc-standby: Pages redeploy test (no content change)"], repo)
    commit = git(["rev-parse", "HEAD"], repo).stdout.strip()
    git(["push", push_url, f"HEAD:refs/heads/{BRANCH}"], repo)
    log(f"PAGESTEST pushed empty commit {commit[:10]}; latest build before: {(before or {}).get('commit', '?')[:10]} (HTTP {code})")
    t0 = time.time()
    while time.time() - t0 < 300:
        time.sleep(15)
        code, b = gh("GET", "/pages/builds/latest")
        if isinstance(b, dict) and b.get("commit") == commit:
            log(f"PAGESTEST build for our commit: status={b.get('status')} after {int(time.time() - t0)} s")
            if b.get("status") == "built":
                log("PAGESTEST PASS - a GITHUB_TOKEN push redeploys Pages")
                return 0
            if b.get("status") == "errored":
                break
    log("PAGESTEST FAIL - no Pages build for the GITHUB_TOKEN push within 5 min; requesting one")
    code, _ = gh("POST", "/pages/builds")
    log(f"PAGESTEST POST /pages/builds -> HTTP {code} (201 = the fallback works)")
    return 1


def run_alerttest() -> int:
    """Owner-run once: prove the issue alert reaches the owner. Opens one clearly-marked issue and closes it."""
    if not have_api():
        log("ALERTTEST needs GITHUB_TOKEN")
        return 1
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or ""
    code, js = gh("POST", "/issues", {"title": "Standby alert test " + ist(now_ist().isoformat()),
                                       "body": "Test of the cause-list standby alert. Nothing is wrong; this issue closes itself.\n"
                                               + (f"\n@{owner}\n" if owner else "")})
    if code != 201:
        log(f"ALERTTEST FAIL - issue not created (HTTP {code})")
        return 1
    c2, _ = gh("PATCH", f"/issues/{js['number']}", {"state": "closed", "state_reason": "not_planned"})
    log(f"ALERTTEST issue #{js['number']} created and closed (HTTP {c2}); check that the e-mail arrived")
    return 0 if c2 == 200 else 1


def cmd_run(a) -> int:
    def _cap(*_):
        log(f"WALL-CAP  run exceeded {WALL_CAP_S} s - killed")
        os._exit(3)
    signal.signal(signal.SIGALRM, _cap)
    signal.alarm(WALL_CAP_S)
    push_url = os.environ.get("STANDBY_PUSH_URL") or None
    now = now_ist()
    log(f"START     mode={a.mode} python={sys.version.split()[0]} now={ist(now.isoformat())}")
    if a.mode == "alerttest":
        return run_alerttest()
    repo = os.path.abspath(a.repo)
    if not os.path.exists(os.path.join(repo, SUBDIR, "data", "meta.json")):
        log(f"ABORT     {repo}/{SUBDIR}/data/meta.json not found")
        return 1
    if a.mode == "pagestest":
        return run_pagestest(repo, push_url)
    if a.mode == "drill":
        return run_drill(repo, now, push_url)
    return run_auto(repo, now, push_url)


def main() -> int:
    ap = argparse.ArgumentParser(description="rhc-causelist standby")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gate")
    r = sp.add_parser("run")
    r.add_argument("--mode", choices=("auto", "drill", "pagestest", "alerttest"), default="auto")
    r.add_argument("--repo", default=".")
    w = sp.add_parser("fetch-worker")
    for n in ("seat_path", "date", "type", "out"):
        w.add_argument(n)
    a = ap.parse_args()
    return {"gate": cmd_gate, "run": cmd_run, "fetch-worker": cmd_fetch_worker}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
