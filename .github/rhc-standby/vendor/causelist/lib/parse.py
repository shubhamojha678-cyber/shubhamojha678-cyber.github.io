"""Parse HC Rajasthan (Jodhpur) cause list HTML -> structured payload.

Strategy:
  * Split the HTML on every BENCH HEADING — "Court No : X", and since
    10-Sep-2026 also "Before Dy. Registrar (Judicial)" / "Before Registrar
    (Admin.)" (own block, board_visible=False) and "THE CHIEF JUSTICE COURT"
    (Court 1). See BENCH_HEADING_RE.
  * Inside each block: judge(s), stage banner, then a sequence of case rows.
  * Each case is two <tr nobr="true"> rows: first has 5 cells (item / case
    + URN / title / petitioner-counsel / respondent-counsel), second has the
    class code in a colspan cell.

Output schema matches build_causelist.py's expectation (see refs/example_payload.json).
"""
from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------- helpers ----------------
def _text(node) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ").split())


def _split_brs(cell: Tag) -> list[str]:
    """Return the cell's text content split by <br> tags, trimmed, non-empty.

    03-Sep-2026: the portal's class-code rows carry a BARE `<br>` ("ADJRN P-1,
    R-0, O-1<br>1502 EX.(9)"). Once html.parser has seen one in a court chunk,
    it treats later `<br/>` tags as containers and NESTS the following text
    inside them — the title cell became `RADHY SHYAM<br>STATE OF
    RAJASTHAN</br>`, and this splitter, which only flushed on a <br> and never
    read its children, dropped the respondent. 4 of 11 firm matters on the
    03-Sep list, 4 of 8 on 07-Sep and 3 of 9 on 08-Sep went out with no
    respondent. Text found INSIDE a <br> is text that FOLLOWS the break."""
    parts: list[str] = []
    buf: list[str] = []

    def flush():
        s = " ".join("".join(buf).split())
        if s:
            parts.append(s)
        buf.clear()

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                buf.append(str(child))
            elif isinstance(child, Tag):
                if child.name == "br":
                    flush()
                    walk(child)          # swallowed text = the next line(s)
                else:
                    # nested tag — collapse its text contribution into buffer
                    buf.append(child.get_text(" "))

    walk(cell)
    flush()
    return parts


def _join_bracketed(parts: list[str]) -> list[str]:
    """Re-join an authority bracket that the portal broke across lines:
    ['A', '(STATE OF', 'RAJASTHAN)', 'B'] -> ['A', '(STATE OF RAJASTHAN)', 'B'].
    Without this the closing line ')' counted as a party and became the
    respondent (03-Sep-2026)."""
    out: list[str] = []
    open_buf: list[str] = []
    for p in parts:
        if open_buf:
            open_buf.append(p)
            if p.endswith(")"):
                out.append(" ".join(open_buf)); open_buf = []
            continue
        if p.startswith("(") and not p.endswith(")"):
            open_buf = [p]
            continue
        out.append(p)
    if open_buf:                    # never closed — keep the lines as they were
        out.extend(open_buf)
    return out


def _clean_judge(s: str) -> str:
    s = re.sub(r"^HON'?BLE\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^HON&#39;BLE\s+", "", s, flags=re.IGNORECASE)
    return s.strip()


# ---------------- court block extraction ----------------
COURT_HEADING_RE = re.compile(r"Court\s+No\s*:\s*([0-9A-Za-z\-—]+)")

# EVERY BENCH HEADING, NOT ONLY "Court No." (10-Sep-2026, a false 🔴 NOW).
# The portal titles each bench with the one-cell first row of a centred header
# table — <td valign="top" colspan="3"><b>HEADING</b></td> — followed by the
# judge/time rows. The splitter used to look for "Court No :" alone, so any
# other heading was invisible to it:
#
#   * "Before Dy. Registrar (Judicial)" / "Before Registrar (Admin.)" — the
#     block was swallowed by the PRECEDING judge's chunk. On 10-Sep C.M.A.
#     954/2025 BHEDIYA, item 2 before the Dy. Registrar (Judicial) at 02:30 PM,
#     came out as Court 25 item 2 — which is a stranger's bail (NEMARAM) — and
#     the board watcher sent a 🔴 NOW when Court 25 reached 2. A Registrar does
#     not appear on the display board at all.
#   * "THE CHIEF JUSTICE COURT" — printed FIRST in the list, so the block sat
#     in the head region before the first "Court No :" and every row in it was
#     DROPPED. (No firm matter was lost on the archived days; the next one
#     before the Chief Justice would have been.)
#
# The shapes below are the COMPLETE set seen in the archive (90 filtered
# HTMLs 21-May..15-Sep + 6 whole-day supplementary HTMLs, surveyed 10-Sep):
#   "Court No : N" 978 · "Before Dy. Registrar (Judicial)" 24 ·
#   "Before Registrar (Admin.)" 20 · "THE CHIEF JUSTICE COURT" 10.
# Anything else is UNKNOWN: it still gets its own chunk (its rows are never
# folded into a courtroom and never dropped) but is marked off-board, so the
# watcher attends it by the clock instead of trusting a guessed courtroom.
BENCH_HEADING_RE = re.compile(
    r"<td\b[^>]*\bcolspan\s*=\s*[\"']?3[\"']?[^>]*>\s*<b>(.*?)</b>\s*</td>",
    re.S | re.I)
# From 07-Oct-2026 the bench heading lost its <b>: the bold now comes from the
# row style, and a masthead "RAJASTHAN HIGH COURT AT JODHPUR" (still <b>) sits
# in the same table. BENCH_HEADING_RE then saw only the masthead, so THE CHIEF
# JUSTICE COURT block came out "unknown" and off the board. In the archive
# (all 2026 raw HTMLs, surveyed 09-Oct) this bold-row shape carries ONLY bench
# headings: Court No 96 · CJ 5 · Registrar 4 · nothing else.
BOLD_ROW_HEADING_RE = re.compile(
    r"<tr\b[^>]*font-weight\s*:\s*bold[^>]*>\s*"
    r"<td\b[^>]*\bcolspan\s*=\s*[\"']?3[\"']?[^>]*>(.*?)</td>",
    re.S | re.I)
_REGISTRAR_HEADING_RE = re.compile(r"^\s*Before\s+(.*\bRegistrar\b.*?)\s*$", re.I)
# Court No 1 under its own name. Until 08-Sep the Acting Chief Justice's block
# was headed "Court No : 1"; from 09-Sep (the new Chief Justice) the same block
# is headed "THE CHIEF JUSTICE COURT". The portal's own court index (the quick
# cause-list table the board watcher downloads every morning) lists court 1 as
# "THE CHIEF JUSTICE" on 09- and 10-Sep, and courtroom 1 on the display board
# runs that list — so this is Court 1 by the portal's own numbering, not a guess.
_CJ_HEADING_RE = re.compile(r"^\s*THE\s+CHIEF\s+JUSTICE\s+COURT\s*$", re.I)
_SITTING_RE = re.compile(
    r"(\d{1,2}:\d{2}\s*[AP]\.?M\.?\s*TO\s*\d{1,2}:\d{2}\s*[AP]\.?M\.?)", re.I)


def _heading_text(raw: str) -> str:
    return " ".join(BeautifulSoup(raw, "html.parser").get_text(" ").split())


def classify_heading(text: str) -> dict:
    """What a bench heading IS. Returns {"court_no", "bench", "bench_kind",
    "board_visible"}; bench_kind is "court" | "chief_justice" | "registrar" |
    "unknown". Only a numbered court (or Court 1 under the Chief Justice's
    name) is a courtroom on the display board."""
    t = " ".join((text or "").split())
    m = COURT_HEADING_RE.search(t)
    if m:
        return {"court_no": m.group(1).strip(), "bench": "", "bench_kind": "court",
                "board_visible": True}
    if _CJ_HEADING_RE.match(t):
        return {"court_no": "1", "bench": "THE CHIEF JUSTICE COURT",
                "bench_kind": "chief_justice", "board_visible": True}
    m = _REGISTRAR_HEADING_RE.match(t)
    if m:
        return {"court_no": m.group(1).strip(), "bench": m.group(1).strip(),
                "bench_kind": "registrar", "board_visible": False}
    return {"court_no": t, "bench": t, "bench_kind": "unknown",
            "board_visible": False}


def _split_bench_chunks(html: str) -> list[dict]:
    """Every bench block of the document, in order: {"court_no", "chunk",
    "heading", "bench", "bench_kind", "board_visible", "sitting"}.

    A "Court No :" heading splits exactly where it always did (the enclosing
    <table>), so a courtroom chunk is byte-identical to the old splitter's
    unless a non-courtroom block followed it — which is the bug."""
    marks: dict[int, str] = {}
    for m in COURT_HEADING_RE.finditer(html):
        idx = html.rfind("<table", 0, m.start())
        marks[idx if idx != -1 else m.start()] = m.group(0)
    # A bold-row heading is the bench itself (see BOLD_ROW_HEADING_RE); it
    # outranks any <b> cell of the same table, which may be the masthead.
    for m in BOLD_ROW_HEADING_RE.finditer(html):
        text = _heading_text(m.group(1))
        if not text or COURT_HEADING_RE.search(text):
            continue                    # a numbered court: already marked above
        idx = html.rfind("<table", 0, m.start())
        marks[idx if idx != -1 else m.start()] = text
    # Among <b> cells: a recognised bench heading beats an unknown one (the
    # masthead), so first-bold-cell-wins can never name a bench "unknown"
    # when the table also carries its real heading.
    for m in BENCH_HEADING_RE.finditer(html):
        text = _heading_text(m.group(1))
        if not text or COURT_HEADING_RE.search(text):
            continue                    # a numbered court: already marked above
        idx = html.rfind("<table", 0, m.start())
        idx = idx if idx != -1 else m.start()
        have = marks.get(idx)
        if have is None or (classify_heading(have)["bench_kind"] == "unknown"
                            and classify_heading(text)["bench_kind"] != "unknown"):
            marks[idx] = text
    starts = sorted(marks.items())
    out: list[dict] = []
    for i, (idx, text) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(html)
        chunk = html[idx:end]
        info = classify_heading(text)
        # the heading table's own time cells ("02:30 PM TO 04:30 PM")
        head_end = chunk.find("</table>")
        sitting = [" ".join(s.split()) for s in
                   _SITTING_RE.findall(chunk[:head_end if head_end != -1 else 2000])]
        out.append({**info, "chunk": chunk, "heading": text,
                    "sitting": " / ".join(dict.fromkeys(sitting))})
    return out


def _split_court_chunks(html: str) -> list[tuple[str, str]]:
    """Return list of (court_no, html_chunk) — chunk is the slice up to the
    next bench block (or end of document). A non-courtroom bench's court_no
    is its own name ("Dy. Registrar (Judicial)"), never a number."""
    return [(c["court_no"], c["chunk"]) for c in _split_bench_chunks(html)]


# ---------------- per-court parser ----------------
def _parse_court_chunk(court_no: str, chunk_html: str) -> dict | None:
    soup = BeautifulSoup(chunk_html, "html.parser")

    # Judges — every cell that mentions HON'BLE and is short enough to be a name.
    # Length cap rejects the trailing NOTICE / court-master table (Court 23 footer)
    # whose <td> contains "Published under the authority of Honourable The Acting
    # Chief Justice" along with phone numbers + meeting IDs. Real judge cells are
    # short and always contain the word JUSTICE.
    judges: list[str] = []
    for td in soup.find_all("td"):
        t = _text(td)
        T = t.upper()
        if "HON" not in T or "BLE" not in T:
            continue
        if "JUSTICE" not in T:
            continue
        if len(t) > 200:
            continue
        cleaned = _clean_judge(t)
        if cleaned and cleaned not in judges:
            judges.append(cleaned)

    # NOTE block: the listing-office note printed under the court header
    # (WebEx meeting IDs, mentioning rules, book-list requests …). One <span>
    # per line inside a <td> whose text starts "NOTE:".
    note_lines: list[str] = []
    for td in soup.find_all("td"):
        t = _text(td)
        if t.upper().startswith("NOTE:"):
            spans = td.find_all("span")
            if spans:
                note_lines = [s for s in (_text(x) for x in spans) if s]
            else:
                rest = t[5:].strip()
                note_lines = [rest] if rest else []
            break

    # Subject-matter banner: a standalone one-cell table printed right after
    # the judge name(s) and before the NOTE block — describes the court's
    # overall subject jurisdiction for the day (e.g. "C.A.T MATTERS, PIL
    # MATTERS, CONTEMPT MATTERS, COMMERCIAL APPELLATE DIVISION MATTERS." or
    # "HEARING OF OLD CIVIL MISC. APPEALS (OTHER THAN MAC MATTERS) (WHEN DB-2
    # IS NOT SITTING)"). Distinguishing signature: its <td> carries BOTH
    # border-top AND border-bottom (the NOTE cell only ever has border-bottom,
    # judge-name cells have neither). Some courts (e.g. Court 14, 07-Sep-2026)
    # carry ONLY this banner with an EMPTY colspan="5" stage cell below it —
    # without capturing this separately, those court blocks printed with NO
    # category/stage heading at all. (06-Sep-2026, user-caught: "in which
    # category the same falls" was missing on some courts.)
    subject = ""
    for td in soup.find_all("td"):
        style = td.get("style") or ""
        if "border-top" in style and "border-bottom" in style:
            t = _text(td)
            if t:
                subject = t
                break

    # Stage banner: a <td colspan="5"> whose text isn't the case-table header
    # and which precedes the first case row.
    stage = ""
    for tr in soup.find_all("tr"):
        first_td = tr.find("td")
        if not first_td:
            continue
        colspan = first_td.get("colspan")
        if colspan and str(colspan) == "5":
            t = _text(first_td)
            if t and "S.No" not in t and "Case Type" not in t:
                # Take the FIRST stage banner found (others apply to later groups
                # which we'll attach to the same court block in the simple
                # implementation — fine for our PDF as KeepTogether already
                # works at the court level).
                if not stage:
                    stage = t

    # The short stage (FOR ORDERS / FOR HEARING / FRESH WITH STAY / FRESH
    # WITHOUT STAY / SUBSEQUENT LISTING …) is what he actually reads — keep
    # it as the category heading whenever the portal published one. Only
    # fall back to the longer subject-matter description when the colspan=5
    # cell was empty (Court 14 case) — never prepend both, or the compact
    # single-Legal-page layout (feedback_causelist_single_page) fills up with
    # a repeated roster-jurisdiction paragraph on every court that already
    # had a perfectly good short stage.
    if not stage and subject:
        stage = subject

    # Case rows
    cases: list[dict] = []
    rows = soup.find_all("tr", attrs={"nobr": "true"})

    i = 0
    while i < len(rows):
        tr = rows[i]
        tds = tr.find_all("td", recursive=False)
        if len(tds) >= 5:
            try:
                case = _parse_case_row(tds, rows, i)
                if case:
                    cases.append(case)
            except Exception as e:
                # A row this parser trips on could BE the firm's matter — a
                # silently dropped row is a silently missed hearing. Log the
                # row so the failure is visible in the run log. (22-Aug-2026)
                import logging
                logging.getLogger("parse").warning(
                    "case row %d dropped by parser (%s): %s",
                    i, type(e).__name__,
                    " | ".join(_text(t)[:40] for t in tds[:5]))
            # Look ahead one row for class-code attached to this case
            if i + 1 < len(rows):
                cls_tds = rows[i + 1].find_all("td", recursive=False)
                cls_text = ""
                for c in cls_tds:
                    cs = c.get("colspan")
                    if cs and int(cs) >= 2:
                        cls_text = _text(c)
                        break
                if cls_text and cases:
                    cases[-1]["class_code"] = cls_text
                    i += 2
                    continue
        i += 1

    if not cases and not stage:
        # Empty court block (header only). Skip.
        return None

    return {
        "court_no": court_no,
        "judge": " / ".join(judges) if judges else "",
        "stage": stage,
        "note": note_lines,
        "cases": cases,
    }


def _parse_case_row(tds: list[Tag], rows: list[Tag], idx: int) -> dict | None:
    # Cell 0: item (may include "With NN")
    item = _text(tds[0])
    if not item:
        return None

    # Cell 1: case number + URN
    case_cell = tds[1]
    span = case_cell.find("span")
    case_no = _text(span) if span else _text(case_cell).split("URN")[0].strip()
    urn_match = re.search(r"URN[:\s]*([A-Z0-9\s/]+)", _text(case_cell), re.IGNORECASE)
    urn = urn_match.group(1).strip() if urn_match else ""

    # Cell 2: petitioner / authority / respondent (split by <br>)
    title_parts = _join_bracketed(_split_brs(tds[2]))
    petitioner = ""
    respondent = ""
    authority = None
    non_auth = [p for p in title_parts if not (p.startswith("(") and p.endswith(")"))]
    auths = [p[1:-1] for p in title_parts if p.startswith("(") and p.endswith(")")]
    if non_auth:
        petitioner = non_auth[0]
    if len(non_auth) > 1:
        respondent = non_auth[-1]
    if auths:
        authority = " / ".join(auths)

    # Cells 3 / 4: counsel — split by <br>, retain raw lines incl. -P / -R suffix
    pet_counsel = [c for c in _split_brs(tds[3]) if c]
    resp_counsel = [c for c in _split_brs(tds[4]) if c]

    return {
        "item": item,
        "case_no": case_no,
        "urn": urn,
        "petitioner": petitioner,
        "respondent": respondent,
        "authority": authority,
        "petitioner_counsel": pet_counsel,
        "respondent_counsel": resp_counsel,
        "class_code": "",
    }


def _parse_head_notice(html: str) -> list[str]:
    """The document-level N O T I C E block printed ABOVE the first court
    (bench changes, special sittings). Returns its lines (marker excluded),
    empty list when the document has none."""
    # the head region ends at the FIRST bench heading of any kind — "THE
    # CHIEF JUSTICE COURT" is printed before the first "Court No :", and its
    # rows must not be read as notice text
    chunks = _split_bench_chunks(html)
    head_html = html[:html.find(chunks[0]["chunk"])] if chunks else html
    text = BeautifulSoup(head_html, "html.parser").get_text("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    start = None
    for i, ln in enumerate(lines):
        if re.fullmatch(r"N\s*O\s*T\s*I\s*C\s*E", ln):
            start = i
            break
    if start is None:
        return []
    out = []
    for ln in lines[start + 1:]:
        # portal boilerplate that trails the notice text
        if re.search(r"Causelist\s*\(Filtered\)", ln, re.IGNORECASE):
            continue
        if re.fullmatch(r"\*+", ln):
            continue
        out.append(ln.rstrip("* ").rstrip())
    return out


def split_notice(lines: list[str]) -> dict:
    """Structure raw notice lines: date, intro line(s), numbered items
    (continuations merged), 'BY ORDER …' closing. Single source of truth for
    notice structure — the PDF builder and the LLM digester both use it."""
    date_line, intro, items, closing = "", [], [], []
    for ln in lines:
        if not date_line and re.match(r"^DATE\s*[:.]", ln, re.IGNORECASE):
            date_line = re.sub(r"^DATE\s*[:.]\s*", "", ln, flags=re.IGNORECASE)
            continue
        if re.match(r"^BY ORDER", ln, re.IGNORECASE):
            closing.append(ln)
        elif closing:
            closing[-1] += " " + ln
        elif re.match(r"^\d+\s*\.", ln):
            items.append(ln)
        elif items:
            items[-1] += " " + ln
        else:
            intro.append(ln)
    return {"date": date_line, "intro": intro, "items": items, "closing": closing}


# ---------------- public API ----------------
def parse_causelist_html(html: str, *, date_ddmmyyyy: str, day_name: str,
                        generated_ddmmyyyy: str | None = None) -> dict:
    """Parse the full cause list HTML into the build_causelist.py payload schema.

    The returned payload is UNFILTERED. Apply firm-filtering downstream.
    """
    courts: list[dict] = []
    for ch in _split_bench_chunks(html):
        parsed = _parse_court_chunk(ch["court_no"], ch["chunk"])
        if not (parsed and parsed["cases"]):
            continue
        if ch["bench_kind"] != "court":
            # A bench that is not a numbered courtroom says so, in the payload
            # itself: `bench` is its printed name, `board_visible` whether the
            # display board can ever show it, `sitting` its printed hours. The
            # watcher arms a board_visible=False matter "attend by clock" and
            # the list builder heads it with the bench, not "Court No :".
            parsed["bench"] = ch["bench"]
            parsed["bench_kind"] = ch["bench_kind"]
            parsed["board_visible"] = ch["board_visible"]
            if ch["sitting"]:
                parsed["sitting"] = ch["sitting"]
            if ch["bench_kind"] == "unknown":
                import logging
                logging.getLogger("parse").warning(
                    "UNKNOWN bench heading %r (%d cases) — kept as its own "
                    "off-board block, not guessed into a courtroom",
                    ch["heading"], len(parsed["cases"]))
        courts.append(parsed)
    return {
        "day": day_name,
        "date": date_ddmmyyyy,
        "generated": generated_ddmmyyyy or date_ddmmyyyy,
        "notice": _parse_head_notice(html),
        "courts": courts,
    }


# ── WHAT THE PAGE SAYS IT IS ────────────────────────────────────────────────
# 08-Nov-2026, out of the 3-Oct card. The portal's advocate-name query does
# NOT reliably honour cltype="S": asked for the supplementary list it can
# answer with the DAILY one, and the answer is indistinguishable from a
# genuine supplementary once it has been parsed into rows. On Sat 03-Oct-2026
# that put all four of the firm's DAILY rows into the supplementary payload
# (Ct 8 #102, Ct 8 #103, Ct 11 #5, Ct 21 #18) on a day the High Court
# published no supplementary list for Court 11 or Court 21 at all.
#
# Every page the portal serves declares its own kind in the masthead that
# stands immediately above the first court block:
#     "Daily Causelist (Filtered) Saturday Dated : 03/10/2026"
#     "Supplementary Causelist (PART-A) Friday Dated : 30/10/2026"
#     "Supplementary Causelist* Saturday Dated : 31/10/2026"
# so the page can simply be asked. MEASURED over all 50 stored supplementary
# fetches (both query names, 18-Aug..06-Nov-2026) the masthead set of a page
# is unanimous on every one of them, and exactly TWO pages declare "Daily":
# 03-Oct's primary (1,034,782 B) and 26-Oct's `_vyas` (1,124,136 B).
#
# The marker must be the MASTHEAD and not the mere words, because a head
# NOTICE block routinely prints "...CASES LISTED IN THE DAILY CAUSELIST..." as
# prose: a bare substring search calls 15 of those 50 pages "Daily" when they
# are genuine supplementaries. Requiring the "<Weekday> Dated :" that only the
# masthead carries removes all 15.
_MASTHEAD_KIND_RE = re.compile(
    r"(Supplementary|Daily)\s+Cause\s*list"       # the printed kind
    r"[^A-Za-z]{0,20}"                            # "*", " ", ...
    r"(?:\(Filtered\)|\(PART-[A-Z0-9]+\))?"       # the portal's variants
    r"\s*[A-Za-z]*day\s+Dated\s*:",               # ...Monday Dated :
    re.IGNORECASE)


def declared_list_kind(html: str) -> str | None:
    """'S' / 'D' — the kind THIS PAGE prints in its own masthead, or None.

    SUPPLEMENTARY WINS A MIXED PAGE, so this can only ever be used to reject a
    page that is purely Daily. No mixed page has ever been observed, and the
    asymmetry is deliberate: the caller's action on "D" is to throw the page
    away, and throwing away a page that carries even one genuine supplementary
    masthead would cost him a listing. None = "the page does not say", which
    every caller must treat as "keep it" for the same reason.
    """
    if not html:
        return None
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    kinds = {m.group(1).upper()[0] for m in _MASTHEAD_KIND_RE.finditer(text)}
    if "S" in kinds:
        return "S"
    return "D" if "D" in kinds else None
