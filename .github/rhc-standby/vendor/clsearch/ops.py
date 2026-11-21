"""clsearch operations layer: failure classes, backoff, incidents, alerts, captcha health,
status.json. Deterministic, stdlib only. Reliability comes before features.

Failure classes (every portal attempt is one fetch_log row):
  ok | no_record        normal answers (no_record = nothing published; NOT a failure)
  server_down           HTTP 5xx, connection refused/reset/timeout from hcraj while the internet is up
  captcha_fail          the captcha was not solved (lib/ocr.py glyph templates, Apple Vision fallback)
  local_net             DNS failure (lib/resolve) or no route: 1.1.1.1:443 is unreachable too
  parse_fail            parser raised, 0 courts, or daily rows < 30% of recent median -> HELD, never published
                        (a small list is accepted as 'ok / small_list_confirmed' when two consecutive
                        fetches give exactly the same cases: a real half-day board is not a failure)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import time

import cldb

FAIL = ("server_down", "captcha_fail", "local_net", "parse_fail")
TGSEND = os.path.join(cldb.HOME, "bin", "lib", "tgsend")      # the ONE existing Telegram sender
ALERT_AFTER = 3
REALERT_S = 6 * 3600           # an open, alerted incident reminds the owner every 6 h
PROD_LOCK = os.path.join(cldb.CAUSELIST_DIR, ".run_lock")      # production pipeline's own portal lock (run.sh)
BACKOFF_CAP_S = 900
CAPTCHA_WINDOW, CAPTCHA_MIN_RATE, CAPTCHA_MIN_SAMPLES = 10, 0.8, 5
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def data(*p):
    return os.path.join(cldb.DATA, *p)


def backoff_s(consecutive: int) -> int:
    """30 s, 60 s, 120 s ... capped at 15 min."""
    return min(30 * 2 ** max(consecutive - 1, 0), BACKOFF_CAP_S)


def internet_up(timeout=5.0) -> bool:
    """Tell 'hcraj is down' from 'we are offline': can we reach 1.1.1.1:443 ?"""
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


_DNS = re.compile(r"could not resolve|gaierror|nodename nor servname|Name or service not known|"
                  r"No route to host|Network is unreachable|ENETUNREACH|EHOSTUNREACH", re.I)


def classify_network(err: str, probe=internet_up) -> str:
    if _DNS.search(err or ""):
        return "local_net"
    return "server_down" if probe() else "local_net"


def log_fetch(con, seat, date_iso, t, attempt, cls, detail=""):
    with con:
        con.execute("INSERT INTO fetch_log(seat,date,type,attempt,class,detail,ts) VALUES (?,?,?,?,?,?,?)",
                    (seat, date_iso, t, attempt, cls, (detail or "")[:500], cldb.now()))


# ---------- alerts ----------
def alert(cls: str, key: str, text: str) -> str:
    """Send ONE owner alert through ~/bin/lib/tgsend. CLSEARCH_ALERT_DRY=1 -> nothing is sent,
    the alert is appended to data/alerts_dry.jsonl. If Telegram does not confirm delivery the
    alert goes to data/alerts_pending.jsonl for the watchdog. Returns dry | sent | pending."""
    rec = {"ts": cldb.now(), "class": cls, "key": key, "text": text}
    os.makedirs(cldb.DATA, exist_ok=True)
    if os.environ.get("CLSEARCH_ALERT_DRY") == "1":
        with open(data("alerts_dry.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return "dry"
    try:
        rc = subprocess.run([TGSEND, text], timeout=90, capture_output=True).returncode
    except Exception as e:  # noqa: BLE001 - missing sender / timeout = not delivered
        rc, rec["error"] = 1, repr(e)
    if rc == 0:
        return "sent"
    with open(data("alerts_pending.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return "pending"


def resend_pending(max_n=5) -> int:
    """Alerts Telegram did not confirm (alerts_pending.jsonl) are sent again at the start of a poll
    cycle, at most max_n per cycle; what still fails stays in the file. -> number delivered."""
    p = data("alerts_pending.jsonl")
    if os.environ.get("CLSEARCH_ALERT_DRY") == "1" and not os.environ.get("CLSEARCH_TGSEND"):
        return 0
    try:
        lines = [x for x in open(p, encoding="utf-8").read().splitlines() if x.strip()]
    except OSError:
        return 0
    sent, keep = 0, []
    for i, line in enumerate(lines):
        if i >= max_n:
            keep.append(line)
            continue
        try:
            rc = subprocess.run([os.environ.get("CLSEARCH_TGSEND") or TGSEND, json.loads(line)["text"]], timeout=60, capture_output=True).returncode
        except Exception:  # noqa: BLE001
            rc = 1
        if rc == 0:
            sent += 1
        else:
            keep.append(line)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(x + "\n" for x in keep))
    os.replace(tmp, p)
    return sent


def production_busy(max_age_s=45 * 60) -> bool:
    """True while the firm's production cause-list run holds its portal lock (~/bin/causelist/.run_lock,
    stale after 45 min by its own rule). clsearch then leaves the portal alone for this cycle."""
    try:
        return time.time() - os.path.getmtime(PROD_LOCK) < max_age_s
    except OSError:
        return False


def _kv(con, k, v=None):
    if v is None:
        r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else None
    with con:
        con.execute("INSERT INTO kv(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


# ---------- incidents ----------
def _name(seat, t, date_iso):
    return f"{cldb.SEAT_NAMES[seat]} {'Daily' if t == 'D' else 'Supplementary'} list of {cldb.ddmmyyyy(date_iso)}"


def _after(con, seat, t, date_iso, cls, detail, now_ts):
    """Update the (seat, date, type) incident after a LIVE attempt. One alert when it reaches 3
    failures in a row, a reminder every 6 h while it stays open, and a recovery note when an
    incident that alerted closes."""
    key = (seat, date_iso, t)
    if cls not in FAIL:
        inc = con.execute("SELECT * FROM incidents WHERE seat=? AND date=? AND type=?", key).fetchone()
        if inc:
            with con:
                con.execute("DELETE FROM incidents WHERE seat=? AND date=? AND type=?", key)
            if inc["alerted"]:
                alert("recovered", f"{seat}:{date_iso}:{t}", f"<b>CLSEARCH recovered</b>: {_name(seat, t, date_iso)} "
                      f"fetched again after {inc['consecutive']} failed attempt(s) (was: {inc['last_class']}).")
        return
    with con:
        con.execute("INSERT OR IGNORE INTO incidents(seat,date,type,consecutive,opened_at) VALUES (?,?,?,0,?)",
                    (*key, cldb.now()))
        con.execute("UPDATE incidents SET consecutive=consecutive+1, last_class=? WHERE seat=? AND date=? AND type=?",
                    (cls, *key))
    inc = con.execute("SELECT * FROM incidents WHERE seat=? AND date=? AND type=?", key).fetchone()
    with con:
        con.execute("UPDATE incidents SET next_retry_at=? WHERE seat=? AND date=? AND type=?",
                    (now_ts + backoff_s(inc["consecutive"]), *key))
    first = inc["consecutive"] >= ALERT_AFTER and not inc["alerted"]
    again = inc["alerted"] and now_ts - (inc["alerted_ts"] or 0) >= REALERT_S
    if first or again:
        with con:
            con.execute("UPDATE incidents SET alerted=1, alerted_ts=? WHERE seat=? AND date=? AND type=?", (now_ts, *key))
        alert(cls, f"{seat}:{t}",
              f"<b>clsearch</b>: {_name(seat, t, date_iso)} {'STILL ' if again else ''}failing - {inc['consecutive']} attempts "
              f"in a row, class <b>{cls}</b>. {detail[:200]} Still retrying; stored lists untouched.")


def captcha_health(con):
    """(rate, samples) over the last 10 attempts that reached the captcha."""
    rows = [r[0] for r in con.execute(
        "SELECT class FROM fetch_log WHERE class IN ('ok','no_record','parse_fail','captcha_fail') "
        "ORDER BY id DESC LIMIT ?", (CAPTCHA_WINDOW,))]
    if not rows:
        return 1.0, 0
    return sum(c != "captcha_fail" for c in rows) / len(rows), len(rows)


def captcha_degraded(con) -> bool:
    return _kv(con, "captcha_degraded") == "1"


def _captcha_check(con):
    rate, n = captcha_health(con)
    if n >= CAPTCHA_MIN_SAMPLES and rate < CAPTCHA_MIN_RATE:
        if not captcha_degraded(con):
            _kv(con, "captcha_degraded", "1")
            alert("captcha_degraded", "captcha",
                  f"<b>clsearch</b>: captcha solve rate {rate:.0%} over the last {n} attempts "
                  f"(floor 80%). Polling slowed to every 15 min until it recovers. Check lib/ocr.py templates.")
    elif captcha_degraded(con) and rate >= CAPTCHA_MIN_RATE:
        _kv(con, "captcha_degraded", "0")


# ---------- one attempt ----------
def attempt(con, seat, date_dmy, t, *, fetcher=None, attempt_no=1, probe=internet_up, now_ts=None,
            incident=True, defer_no_record=False):
    """ONE portal attempt for one list -> (class, result). Logs the class. With incident=True (the
    live poller) it maintains the incident / backoff / alert state; the backfill passes
    incident=False and never opens, closes or alerts an incident. A failure never touches stored
    rows. defer_no_record=True: a 'none published' answer is reported but NOT stored (backfill asks
    twice, on two nights, before it believes that a sitting day had no Daily list)."""
    fetcher = fetcher or cldb.live_fetch
    d = cldb.iso(date_dmy)
    now_ts = time.time() if now_ts is None else now_ts
    try:
        html, state, err = fetcher(seat, date_dmy, t)
    except Exception as e:  # noqa: BLE001 - e.g. ConnectionResetError
        html, state, err = None, "network_error", repr(e)
    res, detail = "", err or ""
    if state == "list" and html:
        try:
            res = cldb.ingest_html(con, seat, date_dmy, t, html)
            cls = "ok"
            if _kv(con, f"small:{seat}:{d}:{t}"):          # a good fetch in between: the two short ones were not consecutive
                _kv(con, f"small:{seat}:{d}:{t}", "")
            if res == "parse_empty":
                cls, detail = "parse_fail", "parsed to 0 rows"
        except cldb.HeldList as e:
            cls, detail = "parse_fail", f"{e} [held: {e.path}]"
            # A genuinely short board (half day, vacation bench) looks like a partial list. Rule:
            # two CONSECUTIVE attempts for the same seat/date/type that give exactly the same cases
            # (same count, same case numbers) are accepted as a real small list. Different -> held.
            kk = f"small:{seat}:{d}:{t}"
            if e.small and e.keys:
                sig = f"{len(e.keys)}:" + hashlib.sha256("\n".join(e.keys).encode()).hexdigest()
                if _kv(con, kk) == sig:
                    res = cldb.ingest_html(con, seat, date_dmy, t, html, force=True, source="live small_list_confirmed")
                    cls, detail = "ok", f"small_list_confirmed: {len(e.keys)} rows, two consecutive fetches identical"
                    _kv(con, kk, "")
                else:
                    _kv(con, kk, sig)
                    detail += " [first sight of this small list: accepted if the next fetch is identical]"
            else:
                _kv(con, kk, "")
    elif state == "no_record" and defer_no_record:
        cls, res = "no_record", "deferred"
    elif state == "no_record":
        res = cldb.store(con, seat, d, t, "no_record", {"courts": []},
                         hashlib.sha256(b"no_record").hexdigest(), source="live")
        cls = "no_record"
    elif state == "captcha_failed":
        cls = "captcha_fail"
    else:
        cls = classify_network(err, probe)
    if cls in FAIL:
        res = f"FAILED:{cls}"
        cldb.note_failure(con, seat, d, t, cls, detail)
    log_fetch(con, seat, d, t, attempt_no, cls, detail or res)
    if incident:
        _after(con, seat, t, d, cls, detail, now_ts)
        _captcha_check(con)
    return cls, res


def fetch_and_store(con, seat, date_dmy, t, *, fetcher=None, tries=3, sleep=time.sleep, log=print,
                    probe=internet_up, incident=True, defer_no_record=False) -> str:
    """Up to `tries` attempts with 30 s / 60 s / ... backoff (used by fetch_all + backfill)."""
    res = "FAILED"
    for n in range(1, tries + 1):
        cls, res = attempt(con, seat, date_dmy, t, fetcher=fetcher, attempt_no=n, probe=probe,
                           incident=incident, defer_no_record=defer_no_record)
        if cls not in FAIL:
            return res
        log(f"  attempt {n}/{tries} {seat} {date_dmy} {t}: {cls}")
        if n < tries:
            sleep(backoff_s(n))
    return res


# ---------- court days ----------
def next_sitting_after(d: dt.date) -> dt.date:
    for k in range(1, 40):
        x = d + dt.timedelta(days=k)
        if cldb.sitting_day(x):
            return x
    return d + dt.timedelta(days=1)


def targets(now: dt.datetime):
    """What a poll covers: the next hearing day (Daily + Supplementary) and, on a sitting day,
    TODAY's Daily and Supplementary (after midnight 'the next hearing day' moves on, but today's
    board is the one people are in court for)."""
    today = now.date()
    nxt = next_sitting_after(today)
    out = [(nxt, "D"), (nxt, "S")]
    if cldb.sitting_day(today):
        out += [(today, "D"), (today, "S")]
    return nxt, out


# ---------- status.json ----------
def _age_h(iso_ts, now):
    try:
        return (now - dt.datetime.fromisoformat(iso_ts)).total_seconds() / 3600
    except Exception:
        return None


def build_status(con, now: dt.datetime | None = None) -> dict:
    """GREEN / AMBER / RED per seat. RED only for a real fault:
       - the next hearing day's DAILY list is not held after 21:00 IST the evening before, or
       - on a sitting day, TODAY's DAILY list is not held after 09:00 IST, or
       - on a court day, no daily list has been fetched successfully for more than 26 h.
    Everything that is merely waiting (not yet published, backoff, held) is AMBER -
    a queue is not a fault."""
    now = now or dt.datetime.now(IST)
    nxt, tg = targets(now)
    today = now.date()
    out = {"v": 1, "written_at": now.isoformat(timespec="seconds"), "next_hearing_day": nxt.isoformat(),
           "today_is_court_day": bool(cldb.sitting_day(today)), "captcha": {}, "seats": {}}
    rate, n = captcha_health(con)
    out["captcha"] = {"solve_rate": round(rate, 2), "samples": n, "degraded": captcha_degraded(con)}
    worst = "GREEN"
    for seat in ("jdp", "jp"):
        lists, reasons, verdict = {}, [], "GREEN"
        for d, t in tg:
            r = con.execute("SELECT * FROM lists WHERE seat=? AND date=? AND list_type=?",
                            (seat, d.isoformat(), t)).fetchone()
            inc = con.execute("SELECT * FROM incidents WHERE seat=? AND date=? AND type=?", (seat, d.isoformat(), t)).fetchone()
            ent = {"hearing_date": d.isoformat(),
                   "state": r["state"] if r else None,
                   "last_success_at": r["fetched_at"] if r else None,
                   "last_attempt_at": r["last_attempt_at"] if r else None,
                   "last_failure_class": (r["last_attempt_state"] if r and r["last_attempt_state"] in FAIL
                                          else None),
                   "rows": r["rows"] if r else 0, "hash": (r["sha256"] or "")[:16] if r else "",
                   "consecutive_failures": inc["consecutive"] if inc else 0}
            lists[f"{d.isoformat()}:{t}"] = ent
            have = bool(r and r["state"] == "list")
            if t == "D" and d == nxt:
                deadline = dt.datetime.combine(nxt - dt.timedelta(days=1), dt.time(21, 0), IST)
                if not have and now >= deadline:
                    verdict = "RED"
                    reasons.append(f"daily list for {nxt:%d-%b} not held after 21:00 the evening before"
                                   + (f" (last failure: {ent['last_failure_class']})"
                                      if ent["last_failure_class"] else " (portal answers: none published)"))
                elif not have:
                    verdict = max(verdict, "AMBER", key=["GREEN", "AMBER", "RED"].index)
                    reasons.append(f"waiting for the daily list of {nxt:%d-%b}")
            if t == "D" and d == today and not have and now >= dt.datetime.combine(today, dt.time(9, 0), IST):
                verdict = "RED"
                reasons.append(f"today's daily list ({today:%d-%b}) not held after 09:00"
                               + (f" (last failure: {ent['last_failure_class']})" if ent["last_failure_class"] else ""))
            if inc and verdict != "RED":
                verdict = "AMBER"
                reasons.append(f"{t} list retrying after {inc['consecutive']} failure(s): {inc['last_class']}")
        # freshness = the current / next hearing dates only (a backfill of old dates must not refresh it)
        last_ok = con.execute("SELECT max(fetched_at) FROM lists WHERE seat=? AND list_type='D' AND state='list' "
                              "AND date>=?", (seat, today.isoformat())).fetchone()[0]
        age = _age_h(last_ok, now) if last_ok else None
        if cldb.sitting_day(today) and (age is None or age > 26):
            verdict = "RED"
            reasons.append("no daily list fetched successfully for more than 26 h on a court day"
                           if age is not None else "no daily list held for today or a later date")
        out["seats"][seat] = {"verdict": verdict, "reasons": reasons, "last_daily_success_at": last_ok,
                              "lists": lists}
        worst = max(worst, verdict, key=["GREEN", "AMBER", "RED"].index)
    out["verdict"] = worst
    return out


def write_status(con, now=None) -> dict:
    st = build_status(con, now)
    os.makedirs(cldb.DATA, exist_ok=True)
    tmp = data("status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, data("status.json"))
    return st


# ---------- housekeeping ----------
def prune(con, now=None, trash_days=90):
    """Bounded disk use. Nothing is destroyed at once: files leave for data/trash/<day>/ and only
    trash older than 90 days is removed.
      poll.log            rotated daily to poll-YYYYMMDD.log, 14 days kept
      health_history      30 days kept          fetch_log (DB)  30 days kept
      held/               14 days kept
      raw/                per (seat, date, type): the first-seen file + the latest (the one the
                          stored rows came from); in-between copies go to trash
    -> dict of counts."""
    import shutil
    now = now or dt.datetime.now(IST)
    out = {"raw": 0, "held": 0, "logs": 0, "history": 0, "fetch_log": 0, "trash_purged": 0}
    trash = data("trash", now.strftime("%Y%m%d"))

    def bin_(path):
        os.makedirs(trash, exist_ok=True)
        shutil.move(path, os.path.join(trash, os.path.basename(path)))

    raw = data("raw")
    if os.path.isdir(raw):
        keep = {os.path.basename(r[0]) for r in con.execute("SELECT raw_path FROM lists WHERE raw_path<>''") if r[0]}
        groups = {}
        for n in os.listdir(raw):
            parts = n.split("_")
            if n.endswith(".html.gz") and len(parts) >= 4:
                groups.setdefault(tuple(parts[:3]), []).append(n)
        for names in groups.values():
            names.sort(key=lambda n: os.path.getmtime(os.path.join(raw, n)))
            safe = {names[0], names[-1]} | (keep & set(names))
            for n in names:
                if n not in safe:
                    bin_(os.path.join(raw, n))
                    out["raw"] += 1
    held = data("held")
    if os.path.isdir(held):
        for n in os.listdir(held):
            if time.time() - os.path.getmtime(os.path.join(held, n)) > 14 * 86400:
                bin_(os.path.join(held, n))
                out["held"] += 1
    plog = data("poll.log")
    try:
        first = open(plog, encoding="utf-8").readline()[:10]
        if first and first != now.strftime("%Y-%m-%d"):
            os.replace(plog, data(f"poll-{first.replace('-', '')}.log"))
            out["logs"] += 1
    except OSError:
        pass
    for n in os.listdir(cldb.DATA):
        if n.startswith("poll-") and n.endswith(".log") and time.time() - os.path.getmtime(data(n)) > 14 * 86400:
            bin_(data(n))
            out["logs"] += 1
    hh = data("health_history.jsonl")
    cut = (now - dt.timedelta(days=30)).isoformat()
    try:
        lines = open(hh, encoding="utf-8").read().splitlines()
        keep_l = [x for x in lines if x[8:33] >= cut[:25]]
        if len(keep_l) < len(lines):
            with open(hh + ".tmp", "w", encoding="utf-8") as f:
                f.write("".join(x + "\n" for x in keep_l))
            os.replace(hh + ".tmp", hh)
            out["history"] = len(lines) - len(keep_l)
    except OSError:
        pass
    with con:
        out["fetch_log"] = con.execute("DELETE FROM fetch_log WHERE ts < ?", (cut,)).rowcount
    troot = data("trash")
    if os.path.isdir(troot):
        for n in os.listdir(troot):
            if time.time() - os.path.getmtime(os.path.join(troot, n)) > trash_days * 86400:
                shutil.rmtree(os.path.join(troot, n), ignore_errors=True)
                out["trash_purged"] += 1
    return out
