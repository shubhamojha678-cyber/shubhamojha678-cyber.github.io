#!/usr/bin/env python3
"""export_static.py --date DD/MM/YYYY [--out DIR] [--no-redact]      (also rewrites coverage.json)
   export_static.py --coverage-only

Writes one compact JSON per date for a static (GitHub Pages) client-side search page and
reports raw + gzip size. Only what the public page needs: no URN, no class codes, no
internal ids. Party names of sensitive matters (POCSO / sexual offences / juvenile /
matrimonial / custody / habeas) are WITHHELD by default; the case number, court, item and
counsel stay so the matter is still findable by number or advocate. NOTHING IS PUBLISHED
by this script - it only writes a local file.

Shape: {v,date,generated_at,lists:{seat:{D:state,S:state}},
        seats:{seat:{courts:[{n,j,b,s,r:[[item,case_no,pet,res,pet_counsel,res_counsel,lt]]}]}}}
lt: 0 = D, 1 = S, 2 = D+S.  pet == 0 means "names withheld".
"""
import argparse
import gzip
import json
import os

import cldb

LT = {"D": 0, "S": 1, "D+S": 2}


def bench_text(judge: str, bench: str = "") -> str:
    """The bench as printed in the COURT HEADER only. The parser sometimes glues the list's signature
    line ("BY ORDER OF HON'BLE THE CHIEF JUSTICE") or a swallowed note / class line ("[5200-...]")
    onto it; only the parts that name a judge or a registrar are kept. A Registrar's court, whose
    only text is the signature line, shows its bench name ("Registrar (Admin.)")."""
    import re
    keep = []
    for part in re.split(r"\s+/\s+", judge or ""):
        p = part.strip()
        if not p or p.startswith("[") or re.match(r"BY ORDER OF", p, re.I):
            continue
        if re.search(r"JUSTICE|REGISTRAR|JUDGE|LOK ADALAT|MEDIAT", p, re.I):
            keep.append(p)
    return " / ".join(keep) or (bench or "").strip()


def build(con, date_iso: str, redact: bool = True) -> dict:
    out = {"v": 1, "date": date_iso, "generated_at": cldb.now(), "lists": {}, "seats": {}}
    for r in con.execute("SELECT seat,list_type,state,rows,fetched_at FROM lists WHERE date=?", (date_iso,)):
        out["lists"].setdefault(r["seat"], {})[r["list_type"]] = [r["state"], r["rows"], r["fetched_at"]]
    # A list whose rows mostly carry no class code (imported from an older parsed file) cannot be
    # classified as sensitive or not -> every party name in that list is withheld.
    blind = {(r["seat"], r["list_type"]) for r in con.execute(
        "SELECT seat, list_type FROM entries WHERE date=? GROUP BY seat, list_type "
        "HAVING sum(class_code = '' OR class_code IS NULL) * 10 > count(*)", (date_iso,))} if redact else set()
    cur_key, court = None, None
    for r in con.execute("SELECT * FROM listings WHERE date=? ORDER BY seat, list_type, court_seq, seq",
                         (date_iso,)):
        key = (r["seat"], r["list_type"], r["court_seq"])
        if key != cur_key:
            cur_key = key
            court = {"n": r["court_no"], "j": bench_text(r["judge"], r["bench"]), "t": r["list_type"], "r": []}
            if r["part"]:
                court["p"] = r["part"]          # 'A' / 'B': item numbers restart in Part B, so court+item alone is ambiguous
            if r["bench"]:
                court["b"] = r["bench"]
            if r["sitting"]:
                court["s"] = r["sitting"]
            out["seats"].setdefault(r["seat"], {"courts": []})["courts"].append(court)
        hide = redact and (r["sensitive"] or (r["seat"], r["list_type"]) in blind)
        court["r"].append([r["item"], r["case_no"], 0 if hide else r["petitioner"],
                           0 if hide else r["respondent"], r["pet_counsel"], r["res_counsel"],
                           LT[r["list_types"]]])
    return out


def write(con, date_dmy: str, out_dir: str, redact: bool = True):
    d = cldb.iso(date_dmy)
    data = build(con, d, redact)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"cl_{d}{'' if redact else '_unredacted'}.json")
    with open(p, "wb") as f:
        f.write(raw)
    gz = gzip.compress(raw, 9)
    n = sum(len(c["r"]) for s in data["seats"].values() for c in s["courts"])
    hidden = sum(1 for s in data["seats"].values() for c in s["courts"] for r in c["r"] if r[2] == 0)
    return p, len(raw), len(gz), n, hidden


def _nice(iso_date):
    import datetime as dt
    return dt.date.fromisoformat(iso_date).strftime("%d-%b-%Y")


def write_coverage(con, out_dir: str):
    """coverage.json for the site banner: from which date data exists, per seat, and the
    court days inside that span for which no list is held."""
    data = coverage_doc(cldb.refresh_coverage(con))
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "coverage.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return p, data


BACKFILL_LOCK = "/tmp/clsearch-backfill.lock"


def backfill_in_progress() -> bool:
    return os.path.exists(BACKFILL_LOCK) or not os.path.exists(os.path.join(cldb.DATA, "backfill_DONE"))


def block_start(list_dates, gap_dates):
    """First date of the unbroken run of held lists that ends at the newest one. Up to two court
    days in a row without a list are tolerated (a holiday next to an off-Saturday); three or more
    end the block - what lies before it is history that is still being filled in."""
    if not list_dates:
        return None
    import datetime as dt
    held, gaps = set(list_dates), set(gap_dates)
    d, lo = dt.date.fromisoformat(list_dates[-1]), dt.date.fromisoformat(list_dates[0])
    start, run = list_dates[-1], 0
    while d >= lo:
        iso = d.isoformat()
        if iso in held:
            start, run = iso, 0
        elif iso in gaps:
            run += 1
            if run >= 3:
                break
        d -= dt.timedelta(days=1)
    return start


def coverage_doc(cov: list, today=None) -> dict:
    """The coverage.json document from cldb.refresh_coverage() rows (no DB access).
    While history is still being backfilled the banner names only the unbroken recent range and
    says older lists are being added - never a large 'days not held' number. Once the backfill is
    done it gives the true first date and the court days the court's site did not serve."""
    import datetime as dt
    today = today or dt.date.today()
    filling = backfill_in_progress()
    seats, parts, older = {}, [], False
    for seat in ("jdp", "jp"):
        short = cldb.SEAT_NAMES[seat].split(" (")[0].replace(" Bench", "")
        rows = {c["list_type"]: c for c in cov if c["seat"] == seat}
        d = rows.get("D")
        if not d or not d["first_date"]:
            seats[seat] = {"name": cldb.SEAT_NAMES[seat], "from": None}
            parts.append(f"{short}: no data yet")
            continue
        lists = {}
        for t, r in rows.items():
            blk = block_start(r.get("list_dates") or [], r["missing_dates"] + r["unverified_dates"]) or r["first_date"]
            lists[t] = {"first": r["first_date"], "block_first": blk, "last": r["last_date"], "count": r["list_count"],
                        "no_record": r["no_record_count"],
                        "missing_court_days": [x for x in r["missing_dates"] if not filling or x >= blk],
                        "unverified_days": len(r["unverified_dates"])}
        start = lists["D"]["block_first"] if filling else d["first_date"]
        older = older or (filling and start > "2025-01-31")
        seats[seat] = {"name": cldb.SEAT_NAMES[seat], "from": start, "to": d["last_date"], "lists": lists}
        n = len(lists["D"]["missing_court_days"])
        parts.append(f"{short}: data from {_nice(start)}"
                     + (f" ({n} court day{'s' if n > 1 else ''} not available from the court\u2019s site \u2014 see list)"
                        if n and not filling else ""))
    banner = " \u00b7 ".join(parts) + ". "
    if older:
        banner += "Older lists back to January 2025 are being added. "
    banner += "Lists before these dates are not in our database."
    return {"v": 2, "generated_at": cldb.now(), "banner": banner, "backfill_in_progress": filling, "seats": seats,
            "note": "from = first date of the unbroken range we hold (while older lists are being added) or the first "
                    "date held; missing_court_days = verified sitting days (HC calendar) with no daily list held; "
                    "unverified_days = number of days we cannot confirm were court days."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--coverage-only", action="store_true")
    ap.add_argument("--out", default=os.path.join(cldb.DATA, "static"))
    ap.add_argument("--no-redact", action="store_true")
    ap.add_argument("--db", default=cldb.DB_PATH)
    a = ap.parse_args()
    con = cldb.connect(a.db)
    if not a.coverage_only:
        if not a.date:
            ap.error("--date is required (or --coverage-only)")
        p, raw, gz, n, hidden = write(con, a.date, a.out, redact=not a.no_redact)
        print(f"{p}\n  rows={n} names_withheld={hidden} raw={raw:,} B ({raw/1024:.0f} KB) "
              f"gzip={gz:,} B ({gz/1024:.0f} KB) = {gz/max(n,1):.0f} B/row gzipped")
    p, cov = write_coverage(con, a.out)
    print(f"{p}  ({os.path.getsize(p):,} B)\n  banner: {cov['banner']}")

if __name__ == "__main__":
    main()
