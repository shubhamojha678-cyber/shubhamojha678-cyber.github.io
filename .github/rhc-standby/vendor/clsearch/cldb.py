"""clsearch core: schema, normalisation, ingest. Stdlib only (sqlite3 + FTS5).

Reads/imports ~/bin/causelist/lib (production) but never writes there.
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

sys.dont_write_bytecode = True          # never write .pyc into the production causelist tree

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("CLSEARCH_DATA") or os.path.join(ROOT, "data")   # drills point this at a temp dir
RAW_DIR = os.path.join(DATA, "raw")
DB_PATH = os.path.join(DATA, "clsearch.db")
CAUSELIST_DIR = os.path.join(HOME, "bin", "causelist")

SEATS = {"jdp": "cishcraj-jdp", "jp": "cishcraj-jp"}
SEAT_NAMES = {"jdp": "Jodhpur (Principal Seat)", "jp": "Jaipur Bench"}
GOOD_STATES = ("list", "no_record")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
  seat TEXT NOT NULL, date TEXT NOT NULL, list_type TEXT NOT NULL,
  state TEXT,                 -- last GOOD state: list | no_record
  rows INTEGER DEFAULT 0,
  sha256 TEXT,                -- sha256 of the raw html (or of the parsed payload when imported)
  content_sha TEXT,           -- sha256 of the parsed rows (raw may differ only by a timestamp)
  fetched_at TEXT,            -- last GOOD fetch
  changed_at TEXT,            -- last time the stored rows were actually rewritten
  raw_path TEXT, source TEXT, generated TEXT,
  last_attempt_at TEXT, last_attempt_state TEXT, last_error TEXT,
  PRIMARY KEY (seat, date, list_type)
);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  seat TEXT NOT NULL, date TEXT NOT NULL, list_type TEXT NOT NULL,
  court_seq INTEGER, court_no TEXT, judge TEXT, bench TEXT, sitting TEXT,
  seq INTEGER, item TEXT,
  case_no TEXT, case_key TEXT, case_type TEXT, case_num INTEGER, case_year INTEGER,
  urn TEXT, petitioner TEXT, respondent TEXT,
  pet_counsel TEXT, res_counsel TEXT, class_code TEXT,
  sensitive TEXT,             -- '' or the reason party names should be masked publicly
  part TEXT DEFAULT ''        -- 'A' / 'B' from the court's own heading "Daily|Supplementary Causelist (PART-A|B)"
);
CREATE INDEX IF NOT EXISTS ix_entries_list ON entries(seat, date, list_type);
CREATE INDEX IF NOT EXISTS ix_entries_key  ON entries(case_key, date);
CREATE INDEX IF NOT EXISTS ix_entries_num  ON entries(case_num, case_year, date);
-- norm = normalised names (see norm_name), one per ' | ' segment. d = date token
-- 'D20261123' so a name query can be intersected with one date inside FTS.
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
  case_no, petitioner, respondent, counsel, norm, d,
  tokenize = "unicode61 remove_diacritics 2", prefix = '3 4 5'
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL,
  query_kind TEXT NOT NULL CHECK (query_kind IN ('case','advocate','party')),
  query TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  verified INTEGER NOT NULL DEFAULT 0,
  last_notified TEXT,
  UNIQUE (email, query_kind, query)
);
-- Every portal attempt, one row, with its failure class:
--   ok | no_record (normal)  |  server_down | captcha_fail | local_net | parse_fail
CREATE TABLE IF NOT EXISTS fetch_log (
  id INTEGER PRIMARY KEY, seat TEXT, date TEXT, type TEXT, attempt INTEGER,
  class TEXT, detail TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS ix_fetch_log ON fetch_log(seat, type, id);
-- One open incident per (seat, date, type): consecutive failures, backoff, alert times.
-- Only the live poller opens / closes incidents; the backfill never does.
CREATE TABLE IF NOT EXISTS incidents (
  seat TEXT, date TEXT, type TEXT, consecutive INTEGER DEFAULT 0, last_class TEXT, opened_at TEXT,
  next_retry_at REAL DEFAULT 0, alerted INTEGER DEFAULT 0, alerted_ts REAL DEFAULT 0,
  PRIMARY KEY (seat, date, type)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
-- CNR (the court's CINO, e.g. RJHC010541342024) -> case, learnt from the hidden cino="<base64>"
-- attribute the court's own list HTML carries on every case link. No portal call is ever made for it.
CREATE TABLE IF NOT EXISTS cnr (
  cnr TEXT PRIMARY KEY, seat TEXT NOT NULL, case_no TEXT NOT NULL, case_type TEXT,
  case_num INTEGER, case_year INTEGER, first_seen TEXT, last_seen TEXT
);
-- What we hold, per seat and list type (only GOOD fetches count).
CREATE VIEW IF NOT EXISTS coverage AS
SELECT seat, list_type,
       min(CASE WHEN state='list' THEN date END) AS first_date,
       max(CASE WHEN state='list' THEN date END) AS last_date,
       sum(state='list') AS list_count, sum(state='no_record') AS no_record_count,
       sum(rows) AS total_rows,
       (SELECT count(*) FROM coverage_gaps g WHERE g.seat=lists.seat AND g.list_type=lists.list_type
          AND g.status='missing') AS missing_court_days,
       (SELECT count(*) FROM coverage_gaps g WHERE g.seat=lists.seat AND g.list_type=lists.list_type
          AND g.status='unverified') AS unverified_days
FROM lists WHERE state IN ('list','no_record') GROUP BY seat, list_type;
-- Dates between first_date and last_date with no stored list. status:
--   missing    = a verified court sitting day (hc_calendar) with no list held
--   unverified = cannot say it was a court day (calendar does not cover that year,
--                or a supplementary list, which is not published every day)
CREATE TABLE IF NOT EXISTS coverage_gaps (
  seat TEXT NOT NULL, list_type TEXT NOT NULL, date TEXT NOT NULL,
  status TEXT NOT NULL, stored_state TEXT,
  PRIMARY KEY (seat, list_type, date)
);
-- One row per listing. A matter printed in BOTH lists of the same seat/date is merged into one
-- 'D+S' row ONLY when the court AND the item are the same in both. When they differ (the
-- supplementary list moved it to another court / item) BOTH rows show, each with its own badge.
CREATE VIEW IF NOT EXISTS listings AS
SELECT e.*,
       CASE WHEN e.list_type = 'D' AND e.case_key <> '' AND EXISTS (
              SELECT 1 FROM entries s WHERE s.case_key = e.case_key AND s.date = e.date
                 AND s.seat = e.seat AND s.list_type = 'S' AND s.court_no = e.court_no AND s.item = e.item AND (s.part = e.part OR s.part = '' OR e.part = ''))
            THEN 'D+S' ELSE e.list_type END AS list_types
FROM entries e
WHERE NOT (e.list_type = 'S' AND e.case_key <> '' AND EXISTS (
              SELECT 1 FROM entries d WHERE d.case_key = e.case_key AND d.date = e.date
                 AND d.seat = e.seat AND d.list_type = 'D' AND d.court_no = e.court_no AND d.item = e.item AND (d.part = e.part OR d.part = '' OR e.part = '')));
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    _migrate(con)
    con.executescript(SCHEMA)
    return con


def _migrate(con):
    """Bring an older DB to the current shape (idempotent, cheap)."""
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(incidents)")]
        if cols and "date" not in cols:                      # incidents were keyed (seat, type): transient state
            with con:
                con.execute("DROP TABLE incidents")
        ecols = [r[1] for r in con.execute("PRAGMA table_info(entries)")]
        if ecols and "part" not in ecols:                    # 22-Nov-2026: PART-A / PART-B per row
            with con:
                con.execute("ALTER TABLE entries ADD COLUMN part TEXT DEFAULT ''")
        v = con.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name='listings'").fetchone()
        if v and "s.part = e.part OR s.part" not in (v[0] or ""):      # older views: no part condition (and before that, no item condition)
            with con:
                con.execute("DROP VIEW listings")
    except sqlite3.OperationalError:
        pass                                                 # another process is migrating right now


# ---------- normalisation ----------
def iso(date_ddmmyyyy: str) -> str:
    return dt.datetime.strptime(date_ddmmyyyy, "%d/%m/%Y").strftime("%Y-%m-%d")


def ddmmyyyy(iso_date: str) -> str:
    return dt.datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d/%m/%Y")


def day_name(date_ddmmyyyy: str) -> str:
    return dt.datetime.strptime(date_ddmmyyyy, "%d/%m/%Y").strftime("%A").upper()


_CASE_RE = re.compile(r"^\s*(.*?)\s*(\d+)\s*/\s*(\d{4})\s*$")


def case_parts(case_no: str):
    """'C.W. 27610/2026' -> ('CW276102026', 'CW', 27610, 2026)."""
    m = _CASE_RE.match(case_no or "")
    if not m:
        return re.sub(r"[^A-Z0-9]", "", (case_no or "").upper()), "", None, None
    typ = re.sub(r"[^A-Z0-9]", "", m.group(1).upper())
    num, year = int(m.group(2)), int(m.group(3))
    return f"{typ}{num}{year}", typ, num, year


_HONORIFICS = {"ADV", "ADVOCATE", "MR", "MRS", "MS", "MISS", "SH", "SHRI", "SMT", "SUSHRI",
               "DR", "KUM", "KUMARI", "SR", "LD"}


def norm_name(s: str, index: bool = False) -> str:
    """Uppercase, drop punctuation, join initials: 'Mr. K. V. Vyas' -> 'KV VYAS'.
    Honorifics: MR / MS / SR / DR / SH / LD are also common INITIALS ("M.S. PUROHIT", "SR GODARA"),
    so a token is dropped as an honorific only when at least TWO more tokens follow it
    ("MR RAJESH KUMAR" -> "RAJESH KUMAR", but "MS PUROHIT" stays "MS PUROHIT").
    index=True (what is stored / searched IN) never drops anything: a query with or without the
    honorific then finds the row, and "MS PUROHIT" no longer means "every Purohit"."""
    toks = re.sub(r"[^A-Z0-9]+", " ", (s or "").upper()).split()
    out: list[str] = []
    run = ""
    for i, t in enumerate(toks):
        if not index and t in _HONORIFICS and len(toks) - i - 1 >= 2:
            continue
        if len(t) == 1 and t.isalpha():
            run += t
            continue
        if run:
            out.append(run)
            run = ""
        out.append(t)
    if run:
        out.append(run)
    return " ".join(out)


def norm_index(s: str) -> str:
    return norm_name(s, index=True)


# Party names in these matters should not be publicly searchable / exported.
_SENSITIVE = [
    ("pocso", re.compile(r"POCSO|PROTECTION OF CHILDREN", re.I)),
    ("sexual-offence", re.compile(r"\b37[56]\b|\b376[- ]?[A-E]{1,2}\b|\b354[- ]?[A-D]?\b|\bRAPE\b|\b(?:6[3-9]|7[0-9])\b(?:\(\w+\))*[^\]]*\bB\.?N\.?S\.?S?\b|SEXUAL"
                                  # 377 IPC (not "CMA 377/2016"); 67A / 67B IT Act (sexually explicit / child material)
                                  r"|\b377\b(?![/\d])[^\]]*\bI\.?P\.?C|\b67[- ]?[AB]\b[^\]]*\bI\.?T\.? ?ACT", re.I)),
    ("juvenile", re.compile(r"JUVENILE|\bJ\.?J\.? ACT|CHILD IN CONFLICT", re.I)),
    ("matrimonial", re.compile(r"\[100[1-6]-|CONJUGAL|CONSUGAL|DIVORCE|MATRIMONIAL|HINDU MARRIAGE|RESTITUTION OF CONJUGAL|FAMILY COURT|MAINTENANCE|\b125 ?CR|\b144 ?BNSS|DOMESTIC VIOLENCE|\bD\.?V\.? ACT|DOWRY|\b498[- ]?A", re.I)),
    ("child-custody", re.compile(r"CUSTODY OF (MINOR|CHILD)|CHILD CUSTODY|GUARDIAN|ADOPTION", re.I)),
    ("habeas-corpus", re.compile(r"HABEAS|\[304-", re.I)),
    ("mental-health", re.compile(r"MENTAL HEALTH|LUNACY|UNSOUND MIND", re.I)),
    # maintenance in every spelling the court uses - but not "maintained / maintainability"
    ("maintenance", re.compile(r"\bMAINT(?!AIN(?:ED|ABILITY|ABLE|ING|S)?\b)\w*|MENTEN|MAINTAINCE|MAINTANCE", re.I)),
    # cruelty (85 BNS), dowry death (80 BNS / 304-B IPC), bigamy (494 / 495 IPC), child marriage
    ("cruelty-dowry-bigamy", re.compile(
        r"CHILD MARRIAGE|DOWRY|304[- ]?B\b|(?<![/\d])\b49[45]\b(?![/\d])"
        r"|\b8[05]\b(?:\(\w+\))*[^\]]*\bB\.?N\.?S\.?S?\b", re.I)),
    ("trafficking", re.compile(r"IMMORAL TRAFFIC|\bITPA\b|\bPITA\b", re.I)),
    # runaway-couple protection petitions
    ("protection", re.compile(r"POLICE PROTECTION|PROTECTION (?:FOR|OF) LIFE|LIFE AND LIBERTY"
                              # 21-Nov-2026 review: the Jaipur registry words them differently
                              r"|PROTECTION TO (?:THE )?LIFE|LIVE[- ]?IN RELATION|HONOU?R KILLING"
                              r"|(?:INTER[- ]?CASTE|LOVE) MARRIAGE|MARRIAGE AGAINST THE WILL|RIGHT TO LIFE LIVE", re.I)),
    # termination-of-pregnancy writs (the petitioner is, in the main, a rape survivor or a minor) and
    # disputed-parentage / DNA matters
    ("pregnancy-parentage", re.compile(r"TERMINATION OF PREGNANCY|\bM\.?T\.?P\.? ACT|PATERNITY|PARENTAGE"
                                       r"|\bDNA (?:TEST|SAMPL|EXAMIN|PROFIL)", re.I)),
    # kidnapping / abduction (S.363/366/366A IPC, S.137/139/140 BNS) and any matter that names a
    # minor. Over-withholding is the safe direction; "minor penalty / mineral" service and mining
    # heads are excluded so ordinary writs keep their names.
    ("minor-kidnapping", re.compile(
        r"KIDNAP|ABDUCT"
        r"|\b36[36][- ]?A?\b(?![/\d])[^\]]*\bI\.?P\.?C"
        r"|\b(?:137|139|140)\b(?:\(\w+\))*[^\]]*\bB\.?N\.?S\.?S?\b"
        r"|\bMINOR\b(?!\s*(?:PENAL|PENLAT|PANEL|MINERAL|PUNISH|IRRIG|HEAD|FOREST|WORK|MISTAKE|OFFEN))", re.I)),
]


# The court writes a minor's or a juvenile's status INTO the party name ("X (MINOR)", "JUVENILE R").
# Only this check looks at party names: the other rules must not ("Executive Engineer (Maintenance)").
_PARTY_MINOR = re.compile(r"\bMINORS?\b(?!\s*(?:MINERAL|IRRIG|FOREST|WORKS?\b|HEAD))|JUVENILE|CHILD IN CONFLICT", re.I)
_PARTY_MATRI = re.compile(r"\(\s*DIVORC\w*\s*\)|\bDIVORCEE\b", re.I)          # "X (DIVORCE)" written into the name


def sensitive_reason(case_no: str, class_code: str, authority: str = "", petitioner: str = "",
                     respondent: str = "") -> str:
    """'' or the comma-joined reasons why party names of this row must be withheld publicly.
    THE one classifier: live ingest, backfill and retag_sensitive.py all call it."""
    blob = f"{case_no} {class_code or ''} {authority or ''}"
    code_only = f"{case_no} {class_code or ''}"          # "Executive Engineer (Maintenance)" lives in authority / party
    hits = [name for name, rx in _SENSITIVE
            if rx.search(code_only if name in ("maintenance", "protection", "pregnancy-parentage") else blob)]
    if re.match(r"\s*Hab", case_no or "", re.I) and "habeas-corpus" not in hits:
        hits.append("habeas-corpus")
    # transfer applications are, in the main, wife v husband: withheld by case type
    if re.match(r"\s*(?:C\.?\s*TRNS\.?\s*APPLI|CRL\.?\s*TSFR\.?\s*PT)", case_no or "", re.I):
        hits.append("transfer")
    if _PARTY_MINOR.search(f"{petitioner or ''} | {respondent or ''}"):
        hits.append("minor-party")
    if _PARTY_MATRI.search(f"{petitioner or ''} | {respondent or ''}") and "matrimonial" not in hits:
        hits.append("matrimonial")
    return ",".join(hits)


# ---------- payload -> rows ----------
def payload_rows(payload: dict) -> list[dict]:
    rows = []
    seq = 0
    for ci, c in enumerate(payload.get("courts") or []):
        for x in c.get("cases") or []:
            seq += 1
            case_no = (x.get("case_no") or "").strip()
            key, typ, num, year = case_parts(case_no)
            resp = re.sub(r"^\s*VS\.?(?:\s+|$)", "", x.get("respondent") or "", flags=re.I).strip()   # a bare "VS" = no name printed
            pc = "; ".join(x.get("petitioner_counsel") or [])
            rc = "; ".join(x.get("respondent_counsel") or [])
            rows.append(dict(
                court_seq=ci, court_no=str(c.get("court_no") or "").strip(),
                judge=c.get("judge") or "", bench=c.get("bench") or "",
                sitting=c.get("sitting") or "", seq=seq, item=(x.get("item") or "").strip(),
                case_no=case_no, case_key=key, case_type=typ, case_num=num, case_year=year,
                urn=x.get("urn") or "", petitioner=(x.get("petitioner") or "").strip(),
                respondent=resp, pet_counsel=pc, res_counsel=rc,
                class_code=x.get("class_code") or "", part=c.get("part") or "",
                sensitive=sensitive_reason(case_no, x.get("class_code") or "", x.get("authority") or "",
                                           x.get("petitioner") or "", resp),
            ))
    return rows


_COLS = ("court_seq court_no judge bench sitting seq item case_no case_key case_type case_num "
         "case_year urn petitioner respondent pet_counsel res_counsel class_code sensitive part").split()


def _content_sha(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps([r[c] for c in _COLS], ensure_ascii=False).encode())
    return h.hexdigest()


def date_token(date_iso: str) -> str:
    return "D" + date_iso.replace("-", "")


def fts_values(r, date_iso: str):
    counsel = f"{r['pet_counsel']}; {r['res_counsel']}".strip("; ")
    norm = " | ".join(norm_index(p) for p in
                      [r["petitioner"], r["respondent"], *counsel.split(";")] if p.strip())
    return (f"{r['case_no']} {r['case_key']}", r["petitioner"], r["respondent"], counsel, norm,
            date_token(date_iso))


def rebuild_fts(con):
    """Drop and rebuild the FTS index from entries (after an FTS schema change)."""
    with con:
        con.execute("DROP TABLE IF EXISTS entries_fts")
        con.executescript(SCHEMA)
        for r in con.execute("SELECT * FROM entries").fetchall():
            con.execute("INSERT INTO entries_fts(rowid,case_no,petitioner,respondent,counsel,norm,d) "
                        "VALUES (?,?,?,?,?,?,?)", (r["id"], *fts_values(r, r["date"])))


def ts_iso(epoch):
    """epoch seconds (the firm pipeline's wrapper ts) -> local ISO, or None."""
    try:
        return dt.datetime.fromtimestamp(float(epoch)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return None


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def note_failure(con, seat, date_iso, list_type, state, error=""):
    """Record a failed attempt. NEVER touches entries or the last-good columns."""
    with con:
        con.execute("INSERT OR IGNORE INTO lists(seat,date,list_type) VALUES (?,?,?)",
                    (seat, date_iso, list_type))
        con.execute("UPDATE lists SET last_attempt_at=?, last_attempt_state=?, last_error=? "
                    "WHERE seat=? AND date=? AND list_type=?",
                    (now(), state, (error or "")[:500], seat, date_iso, list_type))


def store(con, seat, date_iso, list_type, state, payload, raw_sha, *, raw_path="", source="",
          force_empty=False, fetched_at=None) -> str:
    """Upsert one GOOD result (state list | no_record). Returns what happened:
    unchanged | raw-changed-same-rows | replaced | new | no_record | kept-no_record-suspect."""
    assert state in GOOD_STATES, state
    rows = payload_rows(payload) if state == "list" else []
    csha = _content_sha(rows)
    prev = con.execute("SELECT * FROM lists WHERE seat=? AND date=? AND list_type=?",
                       (seat, date_iso, list_type)).fetchone()
    ts = fetched_at or now()      # imports pass the time the portal was really read
    # A published list does not un-publish. 'no_record' after a stored list is almost
    # always portal flake -> keep the rows, log the attempt, do not regress.
    if state == "no_record" and prev and prev["state"] == "list" and prev["rows"] and not force_empty:
        note_failure(con, seat, date_iso, list_type, "no_record",
                     "no_record after a stored list; rows kept")
        return "kept-no_record-suspect"
    if state == "list" and not rows:
        note_failure(con, seat, date_iso, list_type, "parse_empty", "html parsed to 0 rows")
        return "parse_empty"
    with con:
        con.execute("INSERT OR IGNORE INTO lists(seat,date,list_type) VALUES (?,?,?)",
                    (seat, date_iso, list_type))
        same_rows = bool(prev and prev["state"] == state and prev["content_sha"] == csha)
        if same_rows:
            what = "unchanged" if prev["sha256"] == raw_sha else "raw-changed-same-rows"
        else:
            what = "replaced" if prev and prev["state"] else "new"
            ids = [r[0] for r in con.execute(
                "SELECT id FROM entries WHERE seat=? AND date=? AND list_type=?",
                (seat, date_iso, list_type))]
            con.executemany("DELETE FROM entries_fts WHERE rowid=?", [(i,) for i in ids])
            con.execute("DELETE FROM entries WHERE seat=? AND date=? AND list_type=?",
                        (seat, date_iso, list_type))
            for r in rows:
                cur = con.execute(
                    f"INSERT INTO entries(seat,date,list_type,{','.join(_COLS)}) "
                    f"VALUES (?,?,?,{','.join('?' * len(_COLS))})",
                    (seat, date_iso, list_type, *[r[c] for c in _COLS]))
                con.execute("INSERT INTO entries_fts(rowid,case_no,petitioner,respondent,counsel,norm,d) "
                            "VALUES (?,?,?,?,?,?,?)", (cur.lastrowid, *fts_values(r, date_iso)))
            con.execute("UPDATE lists SET changed_at=? WHERE seat=? AND date=? AND list_type=?",
                        (ts, seat, date_iso, list_type))
        con.execute(
            "UPDATE lists SET state=?, rows=?, sha256=?, content_sha=?, fetched_at=?, "
            "raw_path=CASE WHEN ?<>'' THEN ? ELSE raw_path END, source=?, generated=?, "
            "last_attempt_at=?, last_attempt_state=?, last_error=NULL "
            "WHERE seat=? AND date=? AND list_type=?",
            (state, len(rows), raw_sha, csha, ts, raw_path, raw_path, source,
             (payload or {}).get("generated") or "", ts, state, seat, date_iso, list_type))
    if state == "no_record":
        return "no_record"
    return what


def save_raw(html: str, seat: str, date_iso: str, list_type: str, sha: str, raw_dir: str = RAW_DIR) -> str:
    os.makedirs(raw_dir, exist_ok=True)
    p = os.path.join(raw_dir, f"{seat}_{date_iso}_{list_type}_{sha[:12]}.html.gz")
    if not os.path.exists(p):
        tmp = p + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, p)
    return p


_PART_TITLE_RE = re.compile(r"(Daily|Supplementary)\s+Causelist\s*\(\s*PART-?\s*([AB])\s*\)", re.I)


def chunk_parts(html: str) -> list:
    """['A', 'B', ...] - the part of every bench block of a list, in order, from the court's own
    heading ("Daily Causelist (PART-B)"); '' where a block carries no part. Same chunking as the parser."""
    if CAUSELIST_DIR not in sys.path:
        sys.path.insert(0, CAUSELIST_DIR)
    from lib.parse import _split_bench_chunks  # production parser, read-only import
    out = []
    for ch in _split_bench_chunks(html):
        m = _PART_TITLE_RE.search(re.sub(r"<[^>]+>", " ", ch["chunk"]))
        out.append(m.group(2).upper() if m else "")
    return out


def annotate_parts(payload: dict, html: str) -> bool:
    """Stamp court['part'] on every court of a parsed payload. Only when the parser's courts and the
    HTML's bench blocks line up one to one (they do: one court per block); otherwise leave parts empty
    rather than guess. -> True when stamped."""
    try:
        parts = chunk_parts(html)
    except Exception:  # noqa: BLE001
        return False
    courts = payload.get("courts") or []
    if not courts or len(parts) != len(courts):
        return False
    for c, p in zip(courts, parts):
        c["part"] = p
    return True


def parse_html(html: str, date_dmy: str) -> dict:
    if CAUSELIST_DIR not in sys.path:
        sys.path.insert(0, CAUSELIST_DIR)
    from lib.parse import parse_causelist_html  # production parser, read-only import
    return parse_causelist_html(html, date_ddmmyyyy=date_dmy, day_name=day_name(date_dmy))


def ingest_html(con, seat, date_dmy, list_type, html, *, source="live", raw_dir=None,
                force=False) -> str:
    raw_dir = raw_dir or RAW_DIR
    sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
    d = iso(date_dmy)
    prev = con.execute("SELECT sha256,state FROM lists WHERE seat=? AND date=? AND list_type=?",
                       (seat, d, list_type)).fetchone()
    if prev and prev["state"] == "list" and prev["sha256"] == sha:      # unchanged re-poll: no parse
        with con:
            con.execute("UPDATE lists SET fetched_at=?, last_attempt_at=?, last_attempt_state='list', "
                        "last_error=NULL WHERE seat=? AND date=? AND list_type=?",
                        (now(), now(), seat, d, list_type))
        return "unchanged"
    try:
        payload = parse_html(html, date_dmy)
    except Exception as e:  # noqa: BLE001
        raise HeldList(f"parser raised {type(e).__name__}: {e}", _hold(html, seat, d, list_type, sha))
    annotate_parts(payload, html)
    n = sum(len(c.get("cases") or []) for c in payload.get("courts") or [])
    if not payload.get("courts") or n == 0:
        raise HeldList("parsed to 0 courts / 0 rows", _hold(html, seat, d, list_type, sha))
    floor, med = size_floor(con, seat, list_type, d)
    if not force and floor and n < floor:
        held = HeldList(f"rows={n} is under 30% of the median {med:.0f} of the last stored lists "
                        f"(possible partial list) - held, not stored", _hold(html, seat, d, list_type, sha))
        held.small = True                 # ops.attempt may accept it once a 2nd fetch gives the same cases
        held.keys = sorted((x.get("case_no") or "").strip() for c in payload.get("courts") or [] for x in c.get("cases") or [])
        raise held
    keys = sorted((x.get("case_no") or "").strip() for c in payload.get("courts") or [] for x in c.get("cases") or [])
    full = con.execute("SELECT state, rows, content_sha FROM lists WHERE seat=? AND date=? AND list_type=?",
                       (seat, d, list_type)).fetchone()
    if not force and full and full["state"] == "list" and full["rows"] and n < 0.8 * full["rows"]:
        # a re-fetch that lost more than a fifth of the stored rows: truncated answer until proven otherwise
        held = HeldList(f"rows={n} is under 80% of the {full['rows']} rows already stored for this list "
                        f"(possible truncated answer) - held, stored rows kept", _hold(html, seat, d, list_type, sha))
        held.small, held.keys = True, keys
        raise held
    # Raw HTML differs by a timestamp line at every poll: keep a new raw file only when the parsed
    # rows changed (so the newest raw always matches the stored rows), not on every fetch.
    same_rows = bool(full and full["state"] == "list" and full["content_sha"] == _content_sha(payload_rows(payload)))
    raw_path = "" if same_rows else save_raw(html, seat, d, list_type, sha, raw_dir)
    what = store(con, seat, d, list_type, "list", payload, sha, raw_path=raw_path, source=source)
    record_cnr(con, seat, d, html)
    return what


_CINO_RE = re.compile(r'cino="([A-Za-z0-9+/=]{16,32})"[^>]*>\s*<span>([^<]{3,80})</span>')
CNR_RE = re.compile(r"^RJHC0[12]\d{10}$")


def cnr_pairs(html: str):
    """[(cnr, case_no)] from a list's raw HTML: <a cino="UkpIQzAx..." class="casedetails"><span>C.W. 2357/2026</span>."""
    out, seen = [], set()
    for b64, cn in _CINO_RE.findall(html or ""):
        try:
            c = base64.b64decode(b64).decode("ascii").strip().upper()
        except Exception:  # noqa: BLE001
            continue
        cn = re.sub(r"\s+", " ", cn).strip()
        if CNR_RE.match(c) and c not in seen and case_parts(cn)[2] is not None:
            seen.add(c)
            out.append((c, cn))
    return out


def record_cnr(con, seat: str, date_iso: str, html: str) -> int:
    """Upsert the CNR -> case pairs of one list. Never raises: a CNR problem must not stop a list."""
    try:
        pairs = cnr_pairs(html)
        if not pairs:
            return 0
        with con:
            for c, cn in pairs:
                _k, typ, num, year = case_parts(cn)
                con.execute(
                    "INSERT INTO cnr(cnr,seat,case_no,case_type,case_num,case_year,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(cnr) DO UPDATE SET "
                    "case_no=CASE WHEN excluded.last_seen>=cnr.last_seen THEN excluded.case_no ELSE cnr.case_no END, "
                    "case_type=CASE WHEN excluded.last_seen>=cnr.last_seen THEN excluded.case_type ELSE cnr.case_type END, "
                    "case_num=CASE WHEN excluded.last_seen>=cnr.last_seen THEN excluded.case_num ELSE cnr.case_num END, "
                    "case_year=CASE WHEN excluded.last_seen>=cnr.last_seen THEN excluded.case_year ELSE cnr.case_year END, "
                    "first_seen=min(cnr.first_seen, excluded.first_seen), last_seen=max(cnr.last_seen, excluded.last_seen)",
                    (c, seat, cn, typ, num, year, date_iso, date_iso))
        return len(pairs)
    except Exception:  # noqa: BLE001
        return -1


class HeldList(Exception):
    """A fetched list that must not be stored or published (parse_fail). .path = held raw."""
    small = False                         # True only for the "under 30% of the median" hold
    keys = ()

    def __init__(self, reason, path=""):
        super().__init__(reason)
        self.path = path


def _hold(html, seat, d, list_type, sha):
    return save_raw(html, seat, d, list_type, sha, os.path.join(DATA, "held"))


def size_floor(con, seat, list_type, date_iso):
    """(floor, median): 30% of the median row count of the last 10 stored lists of this
    seat/type. DAILY lists only, compared within the same day class (Saturday boards are a
    fraction of a weekday board). Supplementary lists legitimately range from ~20 to 200+
    rows, so the ratio rule would hold good lists - for S only 'parser raised / 0 rows' holds.
    Needs 3+ samples."""
    if list_type != "D":
        return 0, 0
    sat = dt.date.fromisoformat(date_iso).weekday() == 5
    vals = [r["rows"] for r in con.execute(
        "SELECT rows, date FROM lists WHERE seat=? AND list_type=? AND state='list' AND date<>? "
        "ORDER BY date DESC LIMIT 60", (seat, list_type, date_iso))
        if (dt.date.fromisoformat(r["date"]).weekday() == 5) == sat][:10]
    if len(vals) < 3:
        return 0, 0
    vals.sort()
    med = (vals[(len(vals) - 1) // 2] + vals[len(vals) // 2]) / 2
    return 0.3 * med, med


FETCH_TIMEOUT_S = 240


def live_fetch(seat: str, date_dmy: str, list_type: str, timeout=None):
    """One portal fetch -> (html|None, state, error), run in a CHILD PROCESS (fetch_worker.py) that
    is killed after FETCH_TIMEOUT_S. No signal.alarm here: the caller's own wall-clock alarm stays
    armed, and nothing inside the production fetch library can swallow the timeout."""
    import subprocess
    import tempfile
    out = os.path.join(tempfile.gettempdir(), f"clsearch_fetch_{os.getpid()}_{int(time.time() * 1000)}.json")
    cmd = [sys.executable, "-B", os.path.join(ROOT, "fetch_worker.py"), SEATS[seat], date_dmy, list_type, out]
    try:
        subprocess.run(cmd, timeout=timeout or FETCH_TIMEOUT_S, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out, encoding="utf-8") as f:
            r = json.load(f)
        return r["html"], r["state"], r.get("error") or ""
    except subprocess.TimeoutExpired:
        return None, "network_error", f"TimeoutError: fetch exceeded {timeout or FETCH_TIMEOUT_S} s (worker killed)"
    except Exception as e:  # noqa: BLE001 - any failure is a failed attempt, never a wipe
        return None, "network_error", f"{type(e).__name__}: {e}"
    finally:
        for p in (out, out + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass


# ---------- coverage ----------
def likely_sitting_day(d: dt.date) -> bool:
    """Calendar-free rule: Monday-Saturday except Sundays and the 2nd / 4th Saturday."""
    if d.weekday() == 6:
        return False
    if d.weekday() == 5 and (d.day - 1) // 7 in (1, 3):
        return False
    return True


def calendar_years():
    if CAUSELIST_DIR not in sys.path:
        sys.path.insert(0, CAUSELIST_DIR)
    try:
        from lib import hc_calendar as H
    except Exception:
        return None, set()
    return H, ({k.year for k in getattr(H, "HOLIDAYS_2026", {})} or {2026})


def sitting_day(d: dt.date):
    """True / False when the HC calendar can say. A PAST year the calendar does not cover is None
    (unverified: it is the Jodhpur calendar and only for the years it lists). A year AFTER the
    calendar ends falls back to likely_sitting_day (Mon-Sat except 2nd / 4th Saturday) so polling
    and RED checks keep working on 1 January; holidays then simply come back as 'no_record'."""
    if d.weekday() == 6:
        return False
    try:                                   # 22-Nov-2026: the court's OWN 2026 calendar (courtcal.py, verified against
        import courtcal                    # the lists it published) beats the old 2nd/4th-Saturday rule
        if d.year in courtcal.YEARS:
            return courtcal.sits("hc", d)
    except ImportError:
        pass
    H, years = calendar_years()
    if H is None:
        return None
    if d.year not in years:
        return likely_sitting_day(d) if d.year > max(years) else None
    return bool(H.is_sitting_day(d)[0])


def refresh_coverage(con) -> list[dict]:
    """Rebuild coverage_gaps and return the coverage rows (for coverage.json)."""
    held = {}
    for r in con.execute("SELECT seat,list_type,date,state FROM lists"):
        held[(r["seat"], r["list_type"], r["date"])] = r["state"]
    spans = con.execute("SELECT seat,list_type,min(date) a,max(date) b FROM lists WHERE state='list' "
                        "GROUP BY seat,list_type").fetchall()
    gaps = []
    for sp in spans:
        d, end = dt.date.fromisoformat(sp["a"]), dt.date.fromisoformat(sp["b"])
        while d <= end:
            st = held.get((sp["seat"], sp["list_type"], d.isoformat()))
            if st != "list":
                sit = sitting_day(d)
                if sit is not False:
                    if sp["list_type"] == "S":
                        # a supplementary list is optional: no_record is a normal answer
                        status = None if st == "no_record" else "unverified"
                    else:
                        status = "missing" if sit else "unverified"
                    if status:
                        gaps.append((sp["seat"], sp["list_type"], d.isoformat(), status, st))
            d += dt.timedelta(days=1)
    with con:
        con.execute("DELETE FROM coverage_gaps")
        con.executemany("INSERT INTO coverage_gaps VALUES (?,?,?,?,?)", gaps)
    out = []
    for r in con.execute("SELECT * FROM coverage ORDER BY seat, list_type"):
        row = dict(r)
        row["missing_dates"] = [g[2] for g in gaps if g[0] == r["seat"] and g[1] == r["list_type"]
                                and g[3] == "missing"]
        row["unverified_dates"] = [g[2] for g in gaps if g[0] == r["seat"] and g[1] == r["list_type"]
                                   and g[3] == "unverified"]
        row["list_dates"] = sorted(d_ for (s_, t_, d_), st in held.items()
                                   if s_ == r["seat"] and t_ == r["list_type"] and st == "list")
        out.append(row)
    return out
