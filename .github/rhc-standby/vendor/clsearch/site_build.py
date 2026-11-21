#!/usr/bin/env python3
"""site_build.py - builds the static public site INTO a publish build directory.

Called by publish.publish() inside its atomic build -> validate -> swap, so the pages and the
data go live together under one manifest. Nothing here uploads anything.

  <version>/site/                     the deployable root (GitHub Pages project site)
     index.html app.js app.css        search page (results are rendered client-side only)
     jodhpur/ jaipur/ status/         static pages: court-wise summary, NO party names
     jodhpur/<date>/ jaipur/<date>/   one court-wise summary page per date (last DATE_PAGES_DAYS days), NO party names
     404.html robots.txt sitemap.xml .nojekyll status.json
     sw.js app.webmanifest icon-*.png installable app: network-first for lists, cache-first only for versioned assets
     data/meta.json coverage.json     what the client needs to find the files below
     data/d/cl_<date>.json            day files for the last SITE_DAYS days + future dates (no clock times)
     data/cx/b|d/<year>-<k>.json      case-number index over ALL held dates (no names):
                                      b = dates before the current month, d = current month on
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys

import cldb
import export_static
import ops

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "site")
BASE_URL = "https://shubhamojhaandassociates.com/rhc-causelist/"
SITE_DAYS = 14                 # day files (names) kept on the site: last 14 days + future dates.
#                                The case index keeps EVERY date. (Small on purpose: while the site is
#                                a sub-folder of the firm repo every changed byte stays in its history.)
BASE_TTL_S = 24 * 3600         # the pre-current-month index layer is rebuilt at most once a day
SHARD_RECORDS = 3500           # appearances per index shard (about 25-35 KB gzip)
DATE_PAGES_DAYS = 60           # date pages (court-wise summary, no names) are built for the last 60 days + coming
#                                dates. Every page is canonical to itself and is in the sitemap with the time its
#                                list really changed. A date older than this is no longer built (the firm repo
#                                stays bounded): its URL then answers 404 and is gone from the sitemap, so search
#                                engines drop it; the case-number index still covers that date in the search.
ICONS = ("icon-180.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png")
HI_MONTH = ("", "जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर")
# Full forms of the case-type labels printed in the lists, keyed like entries.case_type (label in capitals,
# letters and digits only). ONLY labels whose meaning is certain; every other label stays as printed.
# Checked against the labels in the database on 22-Nov-2026 (SELECT case_type, count(*) FROM entries GROUP BY 1).
CASE_TYPES = {
    "CW": ("Civil Writ Petition", "सिविल रिट याचिका"),
    "CRLMBAIL": ("Criminal Misc. Bail Application (bail)", "आपराधिक विविध जमानत आवेदन (जमानत)"),
    "CRLMPET": ("Criminal Misc. Petition", "आपराधिक विविध याचिका"),
    "CMA": ("Civil Misc. Appeal", "सिविल विविध अपील"),
    "CISTAPPL": ("Civil First Appeal", "सिविल प्रथम अपील"),
    "CIINDAPPL": ("Civil Second Appeal", "सिविल द्वितीय अपील"),
    "CRLR": ("Criminal Revision Petition", "आपराधिक पुनरीक्षण याचिका"),
    "CRLA": ("Criminal Appeal", "आपराधिक अपील"),
    "CRLAS": ("Criminal Appeal (Single Bench)", "आपराधिक अपील (एकल पीठ)"),
    "CRLAD": ("Criminal Appeal (Division Bench)", "आपराधिक अपील (खंडपीठ)"),
    "SAW": ("Special Appeal (Writ)", "विशेष अपील (रिट)"),
    "SAC": ("Special Appeal (Civil)", "विशेष अपील (सिविल)"),
    "WRITCONT": ("Contempt Petition (Writ)", "अवमानना याचिका (रिट)"),
    "WRITCONTEMPTPET": ("Contempt Petition (Writ)", "अवमानना याचिका (रिट)"),
    "CCP": ("Civil Contempt Petition", "सिविल अवमानना याचिका"),
    "CRLCP": ("Criminal Contempt Petition", "आपराधिक अवमानना याचिका"),
    "CRLW": ("Criminal Writ Petition", "आपराधिक रिट याचिका"),
    "SUSPOFSENTAPP": ("Suspension of Sentence Application (in appeal)", "सज़ा निलंबन आवेदन (अपील में)"),
    "SUSPOFSENTREV": ("Suspension of Sentence Application (in revision)", "सज़ा निलंबन आवेदन (पुनरीक्षण में)"),
    "CR": ("Civil Revision Petition", "सिविल पुनरीक्षण याचिका"),
    "CRLMAPPLI": ("Criminal Misc. Application", "आपराधिक विविध आवेदन"),
    "CMAPPL": ("Civil Misc. Application", "सिविल विविध आवेदन"),
    "CTRNSAPPLI": ("Civil Transfer Application", "सिविल स्थानांतरण आवेदन"),
    "CRLTSFRPT": ("Criminal Transfer Petition", "आपराधिक स्थानांतरण याचिका"),
    "HABCORPUS": ("Habeas Corpus Petition", "बंदी प्रत्यक्षीकरण याचिका"),
    "WRITREVIEW": ("Review Petition (Writ)", "पुनर्विलोकन याचिका (रिट)"),
    "CRVW": ("Civil Review Petition", "सिविल पुनर्विलोकन याचिका"),
    "CRLLVA": ("Criminal Leave to Appeal", "आपराधिक अपील की अनुमति याचिका"),
    "CIVLVAPPEA": ("Civil Leave to Appeal", "सिविल अपील की अनुमति याचिका"),
    "CIVLEAVETOAPPEAL": ("Civil Leave to Appeal", "सिविल अपील की अनुमति याचिका"),
    "WRITMAPPL": ("Writ Misc. Application", "रिट विविध आवेदन"),
    "WMAPPL": ("Writ Misc. Application", "रिट विविध आवेदन"),
    "WRITRESTO": ("Writ Restoration Application", "रिट पुनर्स्थापन आवेदन"),
    "RESAPPL": ("Restoration Application", "पुनर्स्थापन आवेदन"),
    "ITAPPEAL": ("Income Tax Appeal", "आयकर अपील"),
    "ITREF": ("Income Tax Reference", "आयकर संदर्भ"),
    "STR": ("Sales Tax Revision", "बिक्री कर पुनरीक्षण"),
    "CEXCISEAPP": ("Central Excise Appeal", "केंद्रीय उत्पाद शुल्क अपील"),
    "EXCISAPPEAL": ("Excise Appeal", "उत्पाद शुल्क अपील"),
    "CUSTOMAPPEAL": ("Customs Appeal", "सीमा शुल्क अपील"),
    "CROSSOBJCIVIL": ("Cross Objection (Civil)", "प्रति-आपत्ति (सिविल)"),
    "CROSSOBJ": ("Cross Objection", "प्रति-आपत्ति"),
    "CROSSOBJWRIT": ("Cross Objection (Writ)", "प्रति-आपत्ति (रिट)"),
    "XOBJWRIT": ("Cross Objection (Writ)", "प्रति-आपत्ति (रिट)"),
    "CRLBAILCAN": ("Bail Cancellation Application", "जमानत निरस्तीकरण आवेदन"),
    "CRLBCANC": ("Bail Cancellation Application", "जमानत निरस्तीकरण आवेदन"),
    "ARBIAPPL": ("Arbitration Application", "मध्यस्थता आवेदन"),
    "COAPPL": ("Company Application", "कंपनी आवेदन"),
    "COPET": ("Company Petition", "कंपनी याचिका"),
    "COA": ("Company Appeal", "कंपनी अपील"),
    "CRLSTAY": ("Stay Application (Criminal)", "स्थगन आवेदन (आपराधिक)"),
    "CRIMSTAY": ("Stay Application (Criminal)", "स्थगन आवेदन (आपराधिक)"),
    "CIVILSTAY": ("Stay Application (Civil)", "स्थगन आवेदन (सिविल)"),
    "WRITSTAY": ("Stay Application (Writ)", "स्थगन आवेदन (रिट)"),
    "EXFIRSTAPP": ("Execution First Appeal", "निष्पादन प्रथम अपील"),
    "EXFAPPEAL": ("Execution First Appeal", "निष्पादन प्रथम अपील"),
    "EXSECONDAPP": ("Execution Second Appeal", "निष्पादन द्वितीय अपील"),
    "EXSAPPEAL": ("Execution Second Appeal", "निष्पादन द्वितीय अपील"),
    "MURDERREF": ("Murder Reference", "मर्डर रेफरेंस (मृत्युदंड की पुष्टि)"),
    "CRLDREF": ("Criminal Death Reference", "मृत्युदंड संदर्भ"),
    "CRLREF": ("Criminal Reference", "आपराधिक संदर्भ"),
    "CIVILREF": ("Civil Reference", "सिविल संदर्भ"),
    "CRLJAILAPP": ("Criminal Jail Appeal", "जेल अपील (आपराधिक)"),
    "ELECTPET": ("Election Petition", "चुनाव याचिका"),
    "OTHERORIGINALSUIT": ("Other Original Suit", "अन्य मूल वाद"),
    "OOS": ("Other Original Suit", "अन्य मूल वाद"),
}
EPOCH = dt.date(2025, 1, 1)
CONFIRM = "always confirm on hcraj.nic.in"
TN = {"D": "Daily", "S": "Supplementary"}
SHORT = {"jdp": "Jodhpur", "jp": "Jaipur"}
MAINT = ("Compiled and maintained by Shubham Ojha &amp; Associates, Advocates, Jodhpur \u00b7 \u00a9 2026 \u00b7 Source: daily and "
         "supplementary cause lists published by the Rajasthan High Court (hcraj.nic.in) \u00b7 Not an official court service")
MAINT_HI = ("\u0938\u0902\u0915\u0932\u0928 \u090f\u0935\u0902 \u0938\u0902\u091a\u093e\u0932\u0928: \u0936\u0941\u092d\u092e \u0913\u091d\u093e \u090f\u0902\u0921 \u090f\u0938\u094b\u0938\u093f\u090f\u091f\u094d\u0938, \u0905\u0927\u093f\u0935\u0915\u094d\u0924\u093e, \u091c\u094b\u0927\u092a\u0941\u0930 \u00b7 \u0938\u094d\u0930\u094b\u0924: \u0930\u093e\u091c\u0938\u094d\u0925\u093e\u0928 \u0909\u091a\u094d\u091a \u0928\u094d\u092f\u093e\u092f\u093e\u0932\u092f "
            "(hcraj.nic.in) \u0926\u094d\u0935\u093e\u0930\u093e \u092a\u094d\u0930\u0915\u093e\u0936\u093f\u0924 \u0935\u093e\u0926 \u0938\u0942\u091a\u093f\u092f\u093e\u0901 \u00b7 \u092f\u0939 \u0928\u094d\u092f\u093e\u092f\u093e\u0932\u092f \u0915\u0940 \u0906\u0927\u093f\u0915\u093e\u0930\u093f\u0915 \u0938\u0947\u0935\u093e \u0928\u0939\u0940\u0902 \u0939\u0948\u0964")
ATTRIBUTION = "Compiled by Shubham Ojha & Associates \u2014 https://shubhamojhaandassociates.com/rhc-causelist/ \u2014 source hcraj.nic.in"
META_TAGS = ('<meta name="author" content="Shubham Ojha &amp; Associates, Advocates, Jodhpur">\n'
             '<meta name="copyright" content="\u00a9 2026 Shubham Ojha &amp; Associates. Compilation; source: cause lists published by the Rajasthan High Court (hcraj.nic.in).">')
UNOFFICIAL = ("Unofficial compilation \u2014 not a website of the Rajasthan High Court; the court\u2019s published cause list is "
              "the only official record.")
UNOFFICIAL_HI = "\u092f\u0939 \u0930\u093e\u091c\u0938\u094d\u0925\u093e\u0928 \u0909\u091a\u094d\u091a \u0928\u094d\u092f\u093e\u092f\u093e\u0932\u092f \u0915\u0940 \u0906\u0927\u093f\u0915\u093e\u0930\u093f\u0915 \u0935\u0947\u092c\u0938\u093e\u0907\u091f \u0928\u0939\u0940\u0902 \u0939\u0948"
UNOFF_HTML = f'<p class="unoff">{UNOFFICIAL} <span lang="hi">{UNOFFICIAL_HI}\u0964</span></p>'
TERMS = [
    ("Source and official record",
     "This site is a compilation of the Daily and Supplementary cause lists published by the Rajasthan High Court on "
     "hcraj.nic.in for its Principal Seat at Jodhpur and its Bench at Jaipur. It is not an official court service. The "
     "court\u2019s own website is the only official record; if anything here differs from it, the court\u2019s record prevails."),
    ("Not legal advice", "The site shows public court information only. Nothing on it is legal advice or a solicitation."),
    ("Permitted use", "You may use the search for personal, non-commercial lookup of a case, an advocate or a party."),
    ("Not permitted without written permission",
     "Automated bulk download or scraping of this compilation, republishing it in whole or in substantial part, and "
     "framing or embedding this site inside another site or app are not permitted without the written permission of "
     "Shubham Ojha & Associates."),
    ("Attribution", "Any quotation from this site must name the source: \u201cRajasthan High Court cause list search, compiled by "
                    "Shubham Ojha & Associates (shubhamojhaandassociates.com/rhc-causelist/)\u201d."),
    ("Privacy", "Party names in sensitive matters (for example matters involving children, sexual offences, family and "
                "matrimonial disputes and habeas corpus) are withheld here; the case number, court and item stay searchable."),
    ("No warranty", "The compilation is provided as it is, without any warranty. A list may be incomplete, delayed or out of "
                    "date, and a case that does not appear here may still be on the court\u2019s board \u2014 always confirm on hcraj.nic.in."),
]
DISCLAIMER = ("This page shows public court information copied from the cause lists published by the "
              "Rajasthan High Court. It is not legal advice and not a solicitation. It may be incomplete "
              "or out of date — " + CONFIRM + ".")
# Volatile = carries a clock time; excluded from the content hash that decides "unchanged".
VOLATILE = ("site/status.json", "site/data/meta.json", "site/index.html", "site/hi/index.html", "site/jodhpur/index.html",
            "site/jaipur/index.html", "site/status/index.html", "site/when-is-my-case-listed/index.html",
            "site/hi/case-kab-lagega/index.html")
_PLAIN = {"server_down": "the court website did not respond", "captcha_fail": "the court website did not answer our request",
          "local_net": "a network problem on our side", "parse_fail": "a list was held back for checking"}

e = html.escape


def _dmy(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def _nice(iso):
    return dt.date.fromisoformat(iso[:10]).strftime("%d-%b-%Y")


def _when(ts, today_iso):
    try:
        t = dt.datetime.fromisoformat(ts)
    except Exception:
        return "time unknown"
    return t.strftime("%H:%M") if t.date().isoformat() == today_iso else t.strftime("%d-%b %H:%M")


def _fill(tpl, **kw):
    out = open(os.path.join(SRC, tpl), encoding="utf-8").read()
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{\w+\}\}", out)
    if left:
        raise ValueError(f"{tpl}: unfilled {left}")
    return out


def public_status(con, now):
    """status.json for the public: what is held and when it was fetched. No internals."""
    st = ops.build_status(con, now)
    seats = {}
    for seat, v in st["seats"].items():
        lists = []
        for key, x in v["lists"].items():
            lists.append({"hearing_date": x["hearing_date"], "type": key.split(":")[1], "state": x["state"],
                          "rows": x["rows"], "last_success_at": x["last_success_at"],
                          "retrying": bool(x["consecutive_failures"])})
        reasons = []
        for r in v["reasons"]:
            for k, plain in _PLAIN.items():
                r = r.replace(k, plain)
            reasons.append(r)
        seats[seat] = {"name": cldb.SEAT_NAMES[seat], "verdict": v["verdict"], "reasons": reasons,
                       "last_daily_success_at": v["last_daily_success_at"], "lists": lists}
    return {"v": 1, "written_at": now.isoformat(timespec="seconds"), "next_hearing_day": st["next_hearing_day"],
            "today_is_court_day": st["today_is_court_day"], "verdict": st["verdict"], "seats": seats}


def status_essence(pub: dict) -> str:
    """Hash of the status without clock times: a change here is worth a publish."""
    ess = {"n": pub["next_hearing_day"], "v": pub["verdict"],
           "s": {s: [v["verdict"], v["reasons"], [[x["hearing_date"], x["type"], x["state"], x["rows"], x["retrying"]]
                                                  for x in v["lists"]]] for s, v in pub["seats"].items()}}
    return hashlib.sha256(json.dumps(ess, sort_keys=True).encode()).hexdigest()


def _list_line(x, today_iso):
    what = f"{TN[x['type']]} list for {_dmy(x['hearing_date'])}: "
    if x["state"] == "list":
        return what + f"<b>held</b>, {x['rows']} entries, fetched {_when(x['last_success_at'], today_iso)}"
    if x["state"] == "no_record" and x["type"] == "S":
        return what + f"none published when we checked ({_when(x['last_success_at'], today_iso)})"
    return what + "<b>not yet available to us</b>"


def _updated_html(pub, today_iso):
    out = ""
    for seat in ("jdp", "jp"):
        v = pub["seats"].get(seat)
        if v:
            out += f"<li><b>{SHORT[seat]}</b> — " + " · ".join(_list_line(x, today_iso) for x in v["lists"]) + "</li>"
    return out + f"<li>Checked {_when(pub['written_at'], today_iso)} IST</li>"


def _footer(root):
    home = root or "./"
    return (f'<footer><div class="in"><p>{UNOFFICIAL} <span lang="hi">{UNOFFICIAL_HI}\u0964</span></p><p>{DISCLAIMER}</p><p>{MAINT} \u00b7 <a href="{root}terms/">Terms of use</a></p>'
            f'<p lang="hi">{MAINT_HI}</p>'
            f'<p><a href="/">Shubham Ojha &amp; Associates \u2014 home page</a> \u00b7 <a href="{home}">Search</a> \u00b7 '
            f'<a href="{root}when-is-my-case-listed/">When is my case listed?</a> \u00b7 <a href="{root}hi/case-kab-lagega/" hreflang="hi" lang="hi">\u092e\u0947\u0930\u093e \u0915\u0947\u0938 \u0915\u092c \u0932\u0917\u0947\u0917\u093e?</a> \u00b7 '
            f'<a href="{root}jodhpur/">Jodhpur summary</a> \u00b7 '
            f'<a href="{root}jaipur/">Jaipur summary</a> \u00b7 <a href="{root}status/">Data status</a> \u00b7 '
            f'<a href="{root}calendar/">Court calendar 2026</a> \u00b7 <a href="{root}terms/">Terms of use</a> \u00b7 <a href="{root}hi/" hreflang="hi" lang="hi">\u0939\u093f\u0902\u0926\u0940 \u092e\u0947\u0902 \u0916\u094b\u091c\u0947\u0902</a></p></div></footer>')


# ---------------------------------------------------------------- case-number index
def _index_rows(con, where, args):
    """Appearances {year: {(num, type): [(day, seat, court, item, lt)]}} + display names of types.
    Same rule as the `listings` view (same court AND item in both lists of one seat/date = one D+S row),
    done in plain scans: the view's per-row sub-query is too slow over every date held."""
    years, disp = {}, {}
    in_s = {}
    for r in con.execute(
        f"SELECT seat, date, case_key, court_no, item, part FROM entries WHERE list_type='S' AND case_key <> '' AND {where}", args):
        in_s.setdefault(tuple(r)[:5], set()).add(r["part"] or "")
    both = set()
    sel = ("SELECT case_type, case_no, case_num, case_year, seat, date, court_no, item, case_key, part FROM entries "
           f"WHERE list_type=? AND case_num IS NOT NULL AND case_year IS NOT NULL AND {where}")
    for lt_code in ("D", "S"):
        for r in con.execute(sel, (lt_code, *args)):
            key = (r["seat"], r["date"], r["case_key"], r["court_no"], r["item"])     # D+S only on the same court AND item
            if lt_code == "D":                                                         # ... AND part (unknown part on either side = match)
                lt = 0
                sp = in_s.get(key)
                if sp is not None and (not r["part"] or "" in sp or r["part"] in sp):
                    lt = 2
                    both.add(key)
            elif key in both:
                continue
            else:
                lt = 1
            typ = r["case_type"] or ""
            if typ not in disp:
                disp[typ] = re.sub(r"\s*\d+\s*/\s*\d{4}\s*$", "", r["case_no"]).strip()
            day = (dt.date.fromisoformat(r["date"]) - EPOCH).days
            years.setdefault(r["case_year"], {}).setdefault((r["case_num"], typ), []).append(
                (day, 0 if r["seat"] == "jdp" else 1, sys.intern(r["court_no"]), sys.intern(r["item"]), lt))
    return years, disp


def _write_layer(years, disp, d):
    """-> ({"y": {year: [first num of each shard]}, "v": hash10}, [file names])"""
    os.makedirs(d, exist_ok=True)
    ymap, h, names = {}, hashlib.sha256(), []
    for year in sorted(years):
        cases = sorted(years[year].items())
        shards, cur, n, last_num = [], [], 0, None
        for (num, typ), apps in cases:
            if n >= SHARD_RECORDS and num != last_num:
                shards.append(cur)
                cur, n = [], 0
            cur.append((num, typ, apps))
            n += len(apps)
            last_num = num
        if cur:
            shards.append(cur)
        ymap[str(year)] = [0] + [s[0][0] for s in shards[1:]]
        for k, s in enumerate(shards):
            types, courts, rows = [], [], []
            for num, typ, apps in s:
                dn = disp[typ]
                if dn not in types:
                    types.append(dn)
                rec = [num, types.index(dn)]
                for day, seat, court, item, lt in sorted(apps):
                    if court not in courts:
                        courts.append(court)
                    rec += [day, seat, courts.index(court), item, lt]
                rows.append(rec)
            raw = json.dumps({"attribution": ATTRIBUTION, "t": types, "c": courts, "r": rows}, ensure_ascii=False, separators=(",", ":")).encode()
            name = f"{year}-{k}.json"
            with open(os.path.join(d, name), "wb") as f:
                f.write(raw)
            h.update(name.encode() + raw)
            names.append(name)
    return {"y": ymap, "v": h.hexdigest()[:10]}, names


def _cov_text(cov):
    """'Jodhpur Daily from 18-May-2026; ...' - what an all-dates search may truthfully claim."""
    bits = []
    for seat in ("jdp", "jp"):
        for t, x in sorted(((cov.get("seats") or {}).get(seat, {}).get("lists") or {}).items()):
            first = x["block_first"] if cov.get("backfill_in_progress") else x["first"]
            if x["count"] and first:
                bits.append(f"{SHORT[seat]} {TN[t]} from {_nice(first)}")
    return "; ".join(bits)


def _banner_html(cov, root):
    return e(cov["banner"]).replace("see list", f'<a href="{root}status/#gaps">see list</a>')


def build_case_index(con, out_dir, month_start_iso, cov, now_ts=None):
    """Sharded index of every (case number -> date, seat, court, item, list type) held. No names.
      d/  dates of the current month onwards: rebuilt at every publish (always fresh)
      b/  every earlier date: rebuilt at most once a day (BASE_TTL_S), when the month turns, or when
          the backfill has just finished - and otherwise re-used from data/cx_base_cache, so a
          running backfill does not rewrite tens of MB at every publish.
    -> ({layer: {"y":..., "v":...}, "base_as_of": iso, "cov": text}, [relative file names])"""
    import shutil
    import time
    now_ts = time.time() if now_ts is None else now_ts
    cache = os.path.join(cldb.DATA, "cx_base_cache")
    stamp = None
    try:
        stamp = json.load(open(os.path.join(cache, "stamp.json"), encoding="utf-8"))
    except Exception:
        pass
    done = os.path.join(cldb.DATA, "backfill_DONE")
    fresh = (stamp and stamp.get("month_start") == month_start_iso and now_ts - stamp.get("built_ts", 0) < BASE_TTL_S
             and not (os.path.exists(done) and os.path.getmtime(done) > stamp.get("built_ts", 0))
             and all(os.path.exists(os.path.join(cache, "b", n)) for n in stamp.get("names", [])))
    if not fresh:
        tmp = cache + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        years, disp = _index_rows(con, "date < ?", (month_start_iso,))
        meta_b, names_b = _write_layer(years, disp, os.path.join(tmp, "b"))
        stamp = {"month_start": month_start_iso, "built_ts": now_ts, "built_at": cldb.now(), "meta": meta_b, "names": names_b,
                 "cov": _cov_text(cov)}
        with open(os.path.join(tmp, "stamp.json"), "w", encoding="utf-8") as f:
            json.dump(stamp, f)
        shutil.rmtree(cache, ignore_errors=True)
        os.rename(tmp, cache)
    os.makedirs(os.path.join(out_dir, "b"), exist_ok=True)
    for n in stamp["names"]:
        shutil.copyfile(os.path.join(cache, "b", n), os.path.join(out_dir, "b", n))
    years, disp = _index_rows(con, "date >= ?", (month_start_iso,))
    meta_d, names_d = _write_layer(years, disp, os.path.join(out_dir, "d"))
    cur = _cov_text(cov)
    # what an "all dates" search can truthfully claim: the base layer as it was built, or today's
    # coverage when the base is current
    return ({"b": stamp["meta"], "d": meta_d, "base_as_of": stamp["built_at"],
             "cov": cur if not stamp["names"] or not fresh else _merge_cov(stamp["cov"], cur)},
            ["b/" + n for n in stamp["names"]] + ["d/" + n for n in names_d])


def _merge_cov(base, cur):
    """Per seat/list the LATER (more cautious) 'from' date of the cached base and of today."""
    def parse(t):
        return {" ".join(x.split(" from ")[0].split()): x.split(" from ")[1] for x in t.split("; ") if " from " in x}
    b, c = parse(base), parse(cur)
    out = []
    for k, v in c.items():
        vb = b.get(k, v)
        later = max(v, vb, key=lambda z: dt.datetime.strptime(z, "%d-%b-%Y"))
        out.append(f"{k} from {later}")
    return "; ".join(out)


# ---------------------------------------------------------------- static pages
def court_summary(day, seat):
    """Court-wise rows for a landing page: court no., bench as printed, sitting, item count, and the list it
    belongs to - DAILY / SUPPLEMENTARY and PART-A / PART-B as the court's own heading states (22-Nov-2026:
    item numbers restart in Part B, so the parts are separate rows). NO party or counsel field."""
    rows, order = {}, []
    for c in (day.get("seats", {}).get(seat) or {}).get("courts", []):
        bench = c["j"] or c.get("b") or ""
        t, p = c.get("t") or ("S" if c["r"] and all(r[6] == 1 for r in c["r"]) else "D"), c.get("p", "")
        key = (c["n"], bench, c.get("s", ""), t, p)
        if key not in rows:
            rows[key] = {"n": 0, "ds": False}
            order.append(key)
        rows[key]["n"] += len(c["r"])
        rows[key]["ds"] = rows[key]["ds"] or any(r[6] == 2 for r in c["r"])
    out = []
    for k in order:
        lab = ("Daily" if k[3] == "D" else "Supplementary") + (f" Part-{k[4]}" if k[4] else "")
        if rows[k]["ds"]:
            lab += " (some also in Suppl.)"
        out.append({"court": k[0], "bench": k[1], "sitting": k[2], "items": rows[k]["n"], "types": lab})
    return out


# ---------------------------------------------------------------- CNR index
CN_BUCKET = 5000               # CNR serials per shard file: fixed buckets, so an old shard never changes


def _cnr_layer(con, d, where, args):
    """Write one CNR layer into d. -> (count, [file names])"""
    os.makedirs(d, exist_ok=True)
    groups, n = {}, 0
    for r in con.execute(f"SELECT cnr, case_no, case_num, case_year FROM cnr WHERE case_num IS NOT NULL AND {where} ORDER BY cnr", args):
        c = r["cnr"]
        serial = int(c[6:12])
        label = re.sub(r"\s*\d+\s*/\s*\d{4}\s*$", "", r["case_no"]).strip()
        groups.setdefault(f"{c[:6]}-{c[12:16]}-{serial // CN_BUCKET}", []).append((serial, label, r["case_num"], r["case_year"]))
        n += 1
    names = []
    for key in sorted(groups):
        types, rows = [], []
        for serial, label, num, cy in groups[key]:
            if label not in types:
                types.append(label)
            rows.append([serial, types.index(label), num, cy])
        with open(os.path.join(d, key + ".json"), "w", encoding="utf-8") as f:
            json.dump({"attribution": ATTRIBUTION, "t": types, "r": rows}, f, ensure_ascii=False, separators=(",", ":"))
        names.append(key + ".json")
    return n, names


def build_cnr_index(con, out_dir, month_start_iso, now_ts=None):
    """CNR -> case, learnt from the cino attribute of the court's own list HTML (cldb.record_cnr). No names, no dates.
    data/cn/<b|d>/<RJHC01|RJHC02>-<cnr year>-<serial // CN_BUCKET>.json = {"t": [type labels], "r": [[serial, t, num, year]]}
      b = CNRs first seen before the current month: rebuilt at most once a day (cached in data/cn_base_cache),
          so the firm repo history does not grow at every publish
      d = CNRs first seen this month: rebuilt at every publish (few, small files)
    -> ({"bk", "b": count, "d": count}, [relative names])"""
    import shutil
    import time
    now_ts = time.time() if now_ts is None else now_ts
    cache = os.path.join(cldb.DATA, "cn_base_cache")
    stamp = None
    try:
        stamp = json.load(open(os.path.join(cache, "stamp.json"), encoding="utf-8"))
    except Exception:
        pass
    fresh = (stamp and stamp.get("month_start") == month_start_iso and now_ts - stamp.get("built_ts", 0) < BASE_TTL_S
             and all(os.path.exists(os.path.join(cache, "b", n)) for n in stamp.get("names", [])))
    if not fresh:
        tmp = cache + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        nb, names_b = _cnr_layer(con, os.path.join(tmp, "b"), "first_seen < ?", (month_start_iso,))
        stamp = {"month_start": month_start_iso, "built_ts": now_ts, "n": nb, "names": names_b}
        with open(os.path.join(tmp, "stamp.json"), "w", encoding="utf-8") as f:
            json.dump(stamp, f)
        shutil.rmtree(cache, ignore_errors=True)
        os.rename(tmp, cache)
    os.makedirs(os.path.join(out_dir, "b"), exist_ok=True)
    for n in stamp["names"]:
        shutil.copyfile(os.path.join(cache, "b", n), os.path.join(out_dir, "b", n))
    nd, names_d = _cnr_layer(con, os.path.join(out_dir, "d"), "first_seen >= ?", (month_start_iso,))
    return ({"bk": CN_BUCKET, "b": stamp["n"], "d": nd, "dk": [n[:-5] for n in names_d]},
            ["b/" + n for n in stamp["names"]] + ["d/" + n for n in names_d])


FAQ = [
    ("When is my case listed in the Rajasthan High Court?",
     "Type your case number (for example CW 1234/2026) or the 16-character CNR in the search box. If the case is in "
     "any cause list we hold for today or a coming date, the answer shows the date, the seat (Jodhpur or Jaipur Bench), "
     "the court number, the item number and the list. If it is not in those lists yet, the answer says so and gives the "
     "last date on which it appeared. This is the listing in the published cause lists, not the case status; confirm on hcraj.nic.in."),
    ("When is the Rajasthan High Court cause list published?",
     "The Daily cause list for a hearing day is normally published on the court’s website the evening before. "
     "Supplementary lists can follow later, including on the hearing day itself. The timing varies from day to day, "
     "so this page shows the time at which each list was last fetched."),
    ("What is a supplementary cause list?",
     "A Supplementary cause list is an additional list published after the Daily list has been prepared, with matters "
     "added later for the same hearing day. A case can appear in the Daily list, in a Supplementary list, or in both. "
     "On this site the badges D, S and D+S show which list an entry comes from."),
    ("What does item number mean in a cause list?",
     "The item number is the serial position of a case on a court’s board for that day. Courts generally take up "
     "matters in item order, but the order can change and a matter may not be reached. “With” before a number "
     "means the case is connected to that item."),
    ("How do I find my case in the cause list?",
     "Type the case number (for example CW 4006/2011 or 4006/2011), the advocate’s name or a party’s name in the "
     "search box. Results show the seat, court number, bench, item number and list type. The search covers only the "
     "lists held here; a case that does not appear may still be on the court’s board, so confirm on hcraj.nic.in."),
    ("Jodhpur or Jaipur — which bench hears my case?",
     "The Rajasthan High Court sits at its Principal Seat in Jodhpur and at a Bench in Jaipur, and each hears matters "
     "from the districts assigned to it. Each seat publishes its own cause list. The search here covers both seats "
     "together and shows the seat for every result."),
    ("Does this show the tentative or next date of my case?",
     "No. Only actual listings are shown: a date appears here only when the case is printed in the Daily or Supplementary "
     "cause list published by the court for that date. Tentative or next dates shown in the court\u2019s case status are not "
     "shown here. For the case status, use hcraj.nic.in."),
    ("Can I search the Rajasthan High Court cause list by CNR number?",
     "Yes. Type the 16-character CNR (for example RJHC010541342024; Jodhpur numbers begin RJHC01, Jaipur Bench numbers "
     "RJHC02). If the case has appeared in any cause list we hold, the search shows its case number and every listing "
     "date in our database. For the case status and orders, use the Case Status page of hcraj.nic.in."),
    ("राजस्थान हाईकोर्ट की आज की कॉज़ लिस्ट में अपना केस कैसे देखें?",
     "खोज बॉक्स में केस नंबर (जैसे CW 4006/2011), अधिवक्ता का नाम या पक्षकार का नाम लिखें। परिणाम में पीठ "
     "(जोधपुर / जयपुर), कोर्ट नंबर, आइटम नंबर और सूची का प्रकार दिखता है। यह सार्वजनिक न्यायालयीन जानकारी है; "
     "कृपया आधिकारिक वेबसाइट hcraj.nic.in पर पुष्टि अवश्य करें।"),
]
ROMAN_HI = ("Rajasthan High Court cause list aaj ki — Jodhpur aur Jaipur Bench ki daily aur supplementary list mein apna case "
            "number, advocate ya party ka naam likh kar khojein. Kripya hcraj.nic.in par pushti zaroor karein.")
HI_ATTR = ' lang="hi"'
ORG = {"@type": "Organization", "name": "Shubham Ojha & Associates", "url": "https://shubhamojhaandassociates.com/"}


def _jsonld(*objs):
    return "\n".join(f'<script type="application/ld+json">{json.dumps(o, ensure_ascii=False)}</script>' for o in objs)


def _webapp(url, name, desc, modified=None):
    out = {"@context": "https://schema.org", "@type": "WebApplication", "name": name, "url": url,
           "applicationCategory": "ReferenceApplication", "operatingSystem": "Any", "description": desc,
           "inLanguage": ["en-IN", "hi-IN"], "provider": ORG, "copyrightHolder": ORG, "copyrightYear": 2026,
           "isBasedOn": "https://hcraj.nic.in"}
    if modified:
        out["dateModified"] = modified
    return out


def _webpage(url, name, desc, modified):
    return {"@context": "https://schema.org", "@type": "WebPage", "name": name, "url": url, "description": desc,
            "dateModified": modified, "inLanguage": "en-IN", "copyrightHolder": ORG, "copyrightYear": 2026, "isBasedOn": "https://hcraj.nic.in", "isPartOf": {"@type": "WebSite", "name": "Rajasthan High Court cause list search", "url": BASE_URL},
            "publisher": ORG}


def _stamp(ts):
    """'21 Nov 2026, 20:19 IST' from an ISO time."""
    try:
        return dt.datetime.fromisoformat(ts).strftime("%d %b %Y, %H:%M IST")
    except Exception:
        return "time unknown"


def day_line(day, seat, d, today_iso):
    """'List for Mon 23 Nov 2026: 30 courts · 2,465 matters (Daily) · Supplementary: ...' - changes daily."""
    lists = day["lists"].get(seat, {})
    courts = {c["n"] for c in (day.get("seats", {}).get(seat) or {}).get("courts", []) if any(r[6] in (0, 2) for r in c["r"])}
    dv, sv = lists.get("D"), lists.get("S")
    if dv and dv[0] == "list":
        a = f"{len(courts)} courts · {dv[1]:,} matters (Daily, fetched {_when(dv[2], today_iso)})"
    else:
        a = "Daily not yet available to us"
    if sv and sv[0] == "list":
        b = f"Supplementary: {sv[1]:,} matters (fetched {_when(sv[2], today_iso)})"
    elif sv and sv[0] == "no_record":
        b = f"Supplementary: none published when we checked ({_when(sv[2], today_iso)})"
    else:
        b = "Supplementary not yet available to us"
    return f"List for {dt.date.fromisoformat(d):%a %d %b %Y}: {a} · {b}"


def _crumbs_ld(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": u} for i, (n, u) in enumerate(items)]}


def _faq_ld():
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]}


def landing_html(seat, pub, days, today_iso, cov, ver, modified, date_pages=()):
    city = SHORT[seat]
    hi_city = "\u091c\u094b\u0927\u092a\u0941\u0930" if seat == "jdp" else "\u091c\u092f\u092a\u0941\u0930 \u092a\u0940\u0920"
    full = "Jodhpur (Principal Seat)" if seat == "jdp" else "Jaipur Bench"
    slug, other = ("jodhpur", "jaipur") if seat == "jdp" else ("jaipur", "jodhpur")
    title = (f"Rajasthan High Court Cause List Today – {'Jodhpur' if seat == 'jdp' else 'Jaipur Bench'} | "
             + ("Case Search" if seat == "jdp" else "Search"))
    h1 = f"Rajasthan High Court Cause List Today — {city}"
    url = BASE_URL + slug + "/"
    first = (cov["seats"].get(seat) or {}).get("from")
    desc = (f"Rajasthan High Court cause list today, {city}: court-wise Daily and Supplementary lists. "
            f"Search by case number, advocate or party. Unofficial.")
    # the hearing days to summarise: today (if a list is held) and the next hearing day
    want = [d for d in sorted(days) if d >= today_iso][:2] or sorted(days)[-1:]
    answer = (f"This page shows the Rajasthan High Court cause list for {full}, court by court, for today and the next hearing "
              f"day, from the Daily and Supplementary lists published by the court. Last updated {_stamp(modified)}. "
              f"To find a case, search by case number, advocate name or party name"
              + (f"; our database covers lists from {_nice(first)}." if first else "."))
    body = [f'<div class="card"><h1>{e(h1)}</h1>{UNOFF_HTML}<p class="ans">{e(answer)}</p>'
            + "".join(f'<p class="sum"><b>{e(day_line(days[d], seat, d, today_iso))}</b></p>' for d in want)
            + f'<p><a class="cta" href="../">Search the cause list by case number, advocate or party</a></p>'
            f'<p class="hi" lang="hi">राजस्थान उच्च न्यायालय, {hi_city} की आज की वाद सूची '
            f'(कॉज़ लिस्ट) का न्यायालय-वार सारांश। केस खोजने के लिए <a href="../">यहाँ खोजें</a>; कृपया hcraj.nic.in पर पुष्टि अवश्य करें।</p>'
            f'<p class="hint">{e(ROMAN_HI)}</p></div>', "<!--summary-->"]
    for d in want:
        day = days[d]
        rows = court_summary(day, seat)
        body.append(f'<div class="card"><h2>{city} cause list, {dt.date.fromisoformat(d):%a %d %b %Y}<span data-day="{d}"></span></h2>'
                    f'<p class="hint">{e(day_line(day, seat, d, today_iso))} — {CONFIRM}</p>')
        if rows:
            body.append(_list_buttons("../", d, seat) + _court_rows_html(rows, "../", d, seat, True))
        else:
            body.append(f"<p>The list for this date is not yet available to us — {CONFIRM}.</p>")
        body.append("</div>")
    if not want:
        body.append(f'<div class="card"><p>No list is available to us yet — {CONFIRM}.</p></div>')
    body.append("<!--/summary-->")
    if date_pages:
        body.append(f'<div class="card"><h2>Rajasthan High Court {e(city)} cause list by date</h2><ul class="dlist">'
                    + "".join(f'<li><a href="{d}/">{dt.date.fromisoformat(d):%a %d %b %Y}</a></li>' for d in sorted(date_pages, reverse=True))
                    + f'</ul><p class="hint">Court-wise summary for each date of the last {DATE_PAGES_DAYS} days and the coming dates we hold.</p></div>')
    body.append(f'<div class="card"><h2>Last updated</h2><ul class="upd" id="upd">{_updated_html(pub, today_iso)}</ul>'
                f'<p class="hint">{_banner_html(cov, "../")}</p></div>')
    body.append('<div class="card"><h2>Questions about the cause list</h2>'
                + "".join(f"<h3{HI_ATTR if i == len(FAQ) - 1 else ''}>{e(q)}</h3><p{HI_ATTR if i == len(FAQ) - 1 else ''}>{e(a)}</p>" for i, (q, a) in enumerate(FAQ))
                + f'<p>See also: <a href="../{other}/">Rajasthan High Court cause list today — {SHORT["jp" if seat == "jdp" else "jdp"]}</a> · '
                  f'<a href="../">Cause list search (both seats)</a> · <a href="../when-is-my-case-listed/">When is my case listed?</a> · '
                  f'<a href="../hi/case-kab-lagega/" hreflang="hi" lang="hi">मेरा केस कब लगेगा?</a> · <a href="../status/">Data status</a></p></div>')
    head = (f'<link rel="canonical" href="{url}">\n{META_TAGS}\n' + _jsonld(
        _webpage(url, h1, desc, modified),
        _webapp(BASE_URL, "Rajasthan High Court cause list search", desc, modified),
        _crumbs_ld([("Rajasthan High Court cause list search", BASE_URL), (f"Cause list today — {city}", url)]), _faq_ld()))
    return _fill("page.html", title=e(title), desc=e(desc), head=head, root="../",
                 page="landing", brand="Rajasthan High Court cause list search", sub=e(f"{full} · court-wise summary"),
                 crumbs=f'<nav class="crumbs"><a href="../">Cause list search</a> › Cause list today — {e(city)}</nav>',
                 body="\n".join(body), footer=_footer("../"), cssv=ver["css"], jsv=ver["js"], upd=f"Updated {_stamp(modified)}")


# ---------------------------------------------------------------- date pages (court-wise, NO party names)
def _seat_summary(day, seat):
    """What a date page needs for one seat, from a day file. No party or counsel field is read."""
    lists = day["lists"].get(seat, {})
    courts = {c["n"] for c in (day.get("seats", {}).get(seat) or {}).get("courts", []) if any(r[6] in (0, 2) for r in c["r"])}
    return {"rows": court_summary(day, seat), "ncourts": len(courts), "lists": {t: v[:2] for t, v in lists.items()}}


def date_summaries(con, day_files, today_iso):
    """-> {date: {seat: {"rows", "ncourts", "lists", "mod"}}} for every date with a held list in the last
    DATE_PAGES_DAYS days and every coming date. Dates on the site come from their day file; older ones from
    data/datepage_cache (rebuilt from the DB whenever the stored rows of that date changed). No names anywhere."""
    lo = (dt.date.fromisoformat(today_iso) - dt.timedelta(days=DATE_PAGES_DAYS)).isoformat()
    sig, mod = {}, {}
    for r in con.execute("SELECT seat, date, list_type, state, rows, changed_at FROM lists WHERE date >= ? AND state IN ('list','no_record') "
                         "ORDER BY date, seat, list_type", (lo,)):
        sig.setdefault(r["date"], []).append([r["seat"], r["list_type"], r["state"], r["rows"], r["changed_at"]])
        if r["state"] == "list":
            k = (r["date"], r["seat"])
            mod[k] = max(mod.get(k, ""), r["changed_at"] or "")
    cache = os.path.join(cldb.DATA, "datepage_cache")
    os.makedirs(cache, exist_ok=True)
    out = {}
    for d in sorted(sig):
        seats = sorted({x[0] for x in sig[d] if x[2] == "list"})
        if not seats:
            continue
        if d in day_files:
            summ = {s_: _seat_summary(day_files[d], s_) for s_ in seats}
        else:
            p = os.path.join(cache, d + ".json")
            try:
                j = json.load(open(p, encoding="utf-8"))
                summ = j["seats"] if j.get("sig") == sig[d] and j.get("v") == 2 else None
            except Exception:
                summ = None
            if summ is None:
                day = export_static.build(con, d, redact=True)
                summ = {s_: _seat_summary(day, s_) for s_ in seats}
                with open(p + ".tmp", "w", encoding="utf-8") as f:
                    json.dump({"v": 2, "sig": sig[d], "seats": summ}, f, ensure_ascii=False, separators=(",", ":"))
                os.replace(p + ".tmp", p)
        out[d] = {s_: dict(summ[s_], mod=mod[(d, s_)]) for s_ in seats if s_ in summ and mod.get((d, s_))}
    for n in os.listdir(cache):                       # cache files of dates that left the window
        if n.endswith(".json") and n[:10] not in out:
            os.remove(os.path.join(cache, n))
    return out


def _court_rows_html(rows, root, d, seat, linked):
    """The court-wise table of a summary page. With `linked`, every row opens that court's full board on the
    search page (rendered there in the browser from the day file); the page itself carries NO party name."""
    from urllib.parse import quote
    out = ['<div class="tw"><table><tr><th>Court</th><th>Bench (as printed in the list)</th><th class="n">Items</th><th class="n">List</th>' + ("<th></th>" if linked else "") + "</tr>"]
    for r in rows:
        court = f"Court {r['court']}" if r["court"].isdigit() else r["court"]
        bench = e(r["bench"]) + (f"<br><small>{e(r['sitting'])}</small>" if r["sitting"] else "")
        if linked:
            href = f'{root}#b={d}.{seat}.{quote(r["court"], safe="")}'
            out.append(f'<tr class="cl"><td><a class="rl" href="{href}"><b>{e(court)}</b></a></td><td><a class="rl" href="{href}" tabindex="-1">{bench}</a></td>'
                       f'<td class="n">{r["items"]}</td><td class="n">{e(r["types"])}</td><td class="n"><a class="rl" href="{href}" tabindex="-1" aria-label="Full board of {e(court)}"><b>Full board ›</b></a></td></tr>')
        else:
            out.append(f'<tr><td>{e(court)}</td><td>{bench}</td><td class="n">{r["items"]}</td><td class="n">{e(r["types"])}</td></tr>')
    out.append("</table></div>")
    return "".join(out)


def _list_buttons(root, d, seat):
    """'View full list' + 'Download PDF' for one seat and date. The PDF is written in the visitor's browser
    from the day file - no PDF file is ever stored in the site or the repo."""
    return (f'<p class="lb"><a class="btn" href="{root}#l={d}.{seat}">View full list <span lang="hi">· पूरी सूची देखें</span></a>'
            f'<a class="btn pdf" href="{root}#l={d}.{seat}&amp;p=1">⬇ Download PDF <span lang="hi">· PDF डाउनलोड करें</span></a></p>'
            f'<p class="hint">Tap a court to open its full board (every item in order). <span lang="hi">पूरा बोर्ड देखने के लिए कोर्ट पर टैप करें।</span></p>')


def _date_line(x):
    """'30 courts · 2,465 matters in the Daily list · Supplementary list: 137 matters' - no clock time."""
    dv, sv = x["lists"].get("D"), x["lists"].get("S")
    a = f"{x['ncourts']} courts · {dv[1]:,} matters in the Daily list" if dv and dv[0] == "list" else "Daily list not yet available to us"
    if sv and sv[0] == "list":
        b = f"Supplementary list: {sv[1]:,} matters"
    elif sv and sv[0] == "no_record":
        b = "Supplementary list: none published when we checked"
    else:
        b = "Supplementary list not yet available to us"
    return f"{a} · {b}"


def _long(d):
    D = dt.date.fromisoformat(d)
    return f"{D.day} {D:%B %Y}"


def date_page_html(seat, d, x, prev_d, next_d, other, in_window, ver):
    """/jodhpur/<date>/ or /jaipur/<date>/ : court, bench as printed, number of items, list type. NO party names."""
    city, D = SHORT[seat], dt.date.fromisoformat(d)
    name = "Jodhpur" if seat == "jdp" else "Jaipur Bench"
    full = "Jodhpur (Principal Seat)" if seat == "jdp" else "Jaipur Bench"
    hi_city = "जोधपुर" if seat == "jdp" else "जयपुर पीठ"
    slug, oslug = ("jodhpur", "jaipur") if seat == "jdp" else ("jaipur", "jodhpur")
    url = f"{BASE_URL}{slug}/{d}/"
    title = f"Rajasthan High Court {name} Cause List {_long(d)} – Court-wise"
    h1 = f"Rajasthan High Court {name} Cause List — {D:%A}, {_long(d)}"
    line = _date_line(x)
    desc = (f"Rajasthan High Court {name} cause list for {_long(d)}, court-wise: courts, benches and number of matters in the "
            f"Daily and Supplementary lists. Unofficial compilation.")
    answer = (f"Court-wise summary of the Rajasthan High Court cause list for {full} on {D:%A}, {_long(d)}: {line}. "
              f"It is compiled from the Daily and Supplementary lists published by the court. Updated {_stamp(x['mod'])}.")
    cta = (f'<a class="cta" href="../../#d={d}&amp;seat={seat}">Search this list by case number, advocate or party</a>' if in_window else
           f'<a class="cta" href="../../">Search by case number (all dates)</a></p><p class="hint">The full list of this date is in our database; '
           f'full lists on this site cover the last {SITE_DAYS} days, so a case number search with “All dates” shows the court and item.')
    nav = []
    if prev_d:
        nav.append(f'<a href="../{prev_d}/" rel="prev">← {dt.date.fromisoformat(prev_d):%a %d %b %Y}</a>')
    if next_d:
        nav.append(f'<a href="../{next_d}/" rel="next">{dt.date.fromisoformat(next_d):%a %d %b %Y} →</a>')
    if other:
        nav.append(f'<a href="../../{oslug}/{d}/">{SHORT["jp" if seat == "jdp" else "jdp"]} cause list, {D:%d %b %Y}</a>')
    nav.append(f'<a href="../">Rajasthan High Court cause list today — {city}</a>')
    body = [f'<div class="card"><h1>{e(h1)}<span data-day="{d}"></span></h1>{UNOFF_HTML}<p class="ans">{e(answer)}</p><p>{cta}</p>'
            f'<p class="hi" lang="hi">राजस्थान हाईकोर्ट {hi_city} कॉज़ लिस्ट (वाद सूची) {D.day} {HI_MONTH[D.month]} {D.year} — कोर्ट-वार सारांश। '
            f'अपना केस खोजने के लिए <a href="../../hi/">यहाँ खोजें</a>; कृपया hcraj.nic.in पर पुष्टि अवश्य करें।</p>'
            f'<nav class="dnav" aria-label="Other dates">{" ".join(nav)}</nav></div>', "<!--summary-->",
            f'<div class="card"><h2>{city} cause list, {D:%a %d %b %Y} — court by court</h2><p class="hint">{e(line)} — {CONFIRM}</p>']
    if x["rows"]:
        body.append((_list_buttons("../../", d, seat) if in_window else "") + _court_rows_html(x["rows"], "../../", d, seat, in_window))
    else:
        body.append(f"<p>The list for this date is not yet available to us — {CONFIRM}.</p>")
    body.append('<p class="hint">Items = entries on that court’s board in the lists we hold. D = Daily list, S = Supplementary list. '
                'Party and counsel names are not shown on this page; use the search.</p></div>')
    body.append("<!--/summary-->")
    head = (f'<link rel="canonical" href="{url}">\n{META_TAGS}\n' + _jsonld(
        _webpage(url, h1, desc, x["mod"]),
        _crumbs_ld([("Rajasthan High Court cause list search", BASE_URL), (f"Cause list today — {city}", f"{BASE_URL}{slug}/"), (f"{D:%d %b %Y}", url)])))
    return _fill("page.html", title=e(title), desc=e(desc), head=head, root="../../", page="date",
                 brand="Rajasthan High Court cause list search", sub=e(f"{full} · court-wise summary · {D:%d %b %Y}"),
                 crumbs=f'<nav class="crumbs"><a href="../../">Cause list search</a> › <a href="../">Cause list today — {e(city)}</a> › {D:%d %b %Y}</nav>',
                 body="\n".join(body), footer=_footer("../../"), cssv=ver["css"], jsv=ver["js"], upd=f"Updated {_stamp(x['mod'])}")


def status_html(pub, cov, today_iso, health, ver):
    word = {"GREEN": "Up to date", "AMBER": "Waiting for a list", "RED": "A list is overdue"}
    body = [f'<div class="card"><h1>Data status — Rajasthan High Court cause list search</h1>{UNOFF_HTML}<p><span class="v {pub["verdict"]}">{word[pub["verdict"]]}</span> '
            f'&nbsp;as of {_nice(pub["written_at"])} {pub["written_at"][11:16]} IST. Next hearing day: {_dmy(pub["next_hearing_day"])}.</p>'
            + (f'<p class="hint">Automatic system check: {e(health["verdict"])} at {e(_when(health["ts"], today_iso))} (GREEN = all checks pass, AMBER = something is waiting, RED = a fault we are working on).</p>' if health else "")
            + "</div>"]
    for seat in ("jdp", "jp"):
        v = pub["seats"][seat]
        body.append(f'<div class="card"><h2>{e(v["name"])} <span class="v {v["verdict"]}">{word[v["verdict"]]}</span></h2>'
                    '<div class="tw"><table><tr><th>List</th><th>Hearing date</th><th>What we hold</th><th>Last successful fetch</th></tr>')
        for x in v["lists"]:
            hold = (f"held, {x['rows']} entries" if x["state"] == "list" else
                    "none published when we checked" if x["state"] == "no_record" and x["type"] == "S" else "not yet available to us")
            body.append(f"<tr><td>{TN[x['type']]}</td><td class=\"n\">{_dmy(x['hearing_date'])}</td><td>{hold}"
                        + (" (retrying)" if x["retrying"] else "") + f"</td><td>{e(_when(x['last_success_at'], today_iso)) if x['last_success_at'] else '-'}</td></tr>")
        body.append("</table></div>" + "".join(f'<p class="hint">{e(r)}</p>' for r in v["reasons"])
                    + (f'<p class="hint">Last successful Daily-list fetch: {e(_when(v["last_daily_success_at"], today_iso))}</p>' if v["last_daily_success_at"] else "") + "</div>")
    filling = cov.get("backfill_in_progress")
    body.append('<div class="card"><h2>Coverage</h2><p>' + _banner_html(cov, "../") + "</p><div class=\"tw\"><table><tr><th>Seat</th><th>List</th><th>From</th><th>To</th><th>Lists held</th></tr>")
    gaps = []
    for seat in ("jdp", "jp"):
        for t, x in sorted((cov["seats"].get(seat, {}).get("lists") or {}).items()):
            first = x["block_first"] if filling else x["first"]
            body.append(f"<tr><td>{SHORT[seat]}</td><td>{TN[t]}</td><td class=\"n\">{_nice(first) if first else '-'}</td>"
                        f"<td class=\"n\">{_nice(x['last']) if x['last'] else '-'}</td><td>{x['count']}</td></tr>")
            if t == "D" and x["missing_court_days"]:
                gaps.append(f"<p><b>{SHORT[seat]}</b> \u2014 Daily list not available from the court\u2019s site for: "
                            + ", ".join(_dmy(d) for d in x["missing_court_days"]) + ".</p>")
    body.append("</table></div>")
    body.append('<h3 id="gaps">Court days without a list</h3>' + ("".join(gaps) if gaps else "<p>None in the range above.</p>")
                + ("<p class=\"hint\">Older lists back to January 2025 are still being added; days in that older range are not counted here yet.</p>" if filling else "")
                + "<p class=\"hint\">We check the court website every 10 minutes during court hours and the evening before "
                f"a hearing day, hourly otherwise. Searching by name covers the last {SITE_DAYS} days and coming dates; searching by case number covers every date we hold.</p></div>")
    return _fill("page.html", title="Data status | Rajasthan High Court cause list search", root="../", page="status",
                 desc="When each Rajasthan High Court cause list was last fetched, and which dates the database covers.",
                 head=f'<link rel="canonical" href="{BASE_URL}status/">\n<meta name="robots" content="noindex">\n{META_TAGS}\n'
                      + _jsonld(_webpage(BASE_URL + "status/", "Data status", "When each cause list was last fetched.", pub["written_at"])),
                 brand="Rajasthan High Court cause list search", sub="Data status",
                 crumbs='<nav class="crumbs"><a href="../">Cause list search</a> › Data status</nav>',
                 body="\n".join(body), footer=_footer("../"), cssv=ver["css"], jsv=ver["js"], upd=f"Updated {_stamp(pub['written_at'])}")


def _last_health():
    try:
        with open(os.path.join(cldb.DATA, "health_history.jsonl"), encoding="utf-8") as f:
            last = f.readlines()[-1]
        j = json.loads(last)
        return {"verdict": j["verdict"], "ts": j["ts"]}
    except Exception:
        return None


# ---------------------------------------------------------------- search pages: English + Hindi
FAQ_HI = [
    ("मेरा केस कब लगेगा? राजस्थान हाईकोर्ट में केस की लिस्टिंग तारीख कैसे देखें?",
     "खोज-बॉक्स में केस नंबर (जैसे CW 1234/2026) या 16 अक्षरों का CNR लिखें। यदि केस आज या आगे की किसी उपलब्ध वाद सूची में है तो "
     "तारीख, पीठ (जोधपुर / जयपुर), कोर्ट नंबर, आइटम नंबर और सूची तुरंत दिखती है। यदि अभी किसी सूची में नहीं है तो वही बताया जाता है "
     "और पिछली लिस्टिंग की तारीख दिखती है। यह प्रकाशित वाद सूची की जानकारी है, केस स्टेटस नहीं; hcraj.nic.in पर पुष्टि अवश्य करें।"),
    ("क्या यहाँ केस की संभावित (टेंटेटिव) या अगली तारीख दिखती है?",
     "नहीं। यहाँ केवल वास्तविक लिस्टिंग दिखती है — कोई तारीख तभी दिखेगी जब केस उस दिन की न्यायालय द्वारा प्रकाशित डेली या "
     "सप्लीमेंट्री वाद सूची में छपा हो। केस स्टेटस में दिखने वाली संभावित (टेंटेटिव) या अगली तारीख यहाँ नहीं दिखाई जाती; "
     "उसके लिए hcraj.nic.in देखें।"),
    ("राजस्थान हाईकोर्ट जोधपुर की आज की कॉज़ लिस्ट (वाद सूची) कैसे देखें?",
     "ऊपर के खोज-बॉक्स में अपना केस नंबर (जैसे CW 1234/2026), वकील का नाम या पक्षकार का नाम लिखें। आज की और अगली सुनवाई की "
     "तारीख की डेली व सप्लीमेंट्री लिस्ट में आपका केस किस कोर्ट में, किस आइटम नंबर पर है — यह तुरंत दिख जाता है। "
     "कोर्ट-वार सारांश जोधपुर और जयपुर पीठ के अलग पेज पर है।"),
    ("केस नंबर से कॉज़ लिस्ट में केस कैसे खोजें?",
     "केस का प्रकार, नंबर और वर्ष लिखें — CW 1234/2026, 1234/2026 या CRLMB 567 of 2025, तीनों तरह से खोज चलती है। "
     "पुरानी तारीखों की लिस्टिंग देखने के लिए तारीख वाले खाने में “All dates” चुनें।"),
    ("वकील (अधिवक्ता) के नाम से कॉज़ लिस्ट कैसे देखें?",
     "वकील का नाम अंग्रेज़ी में वैसे ही लिखें जैसा वाद सूची में छपता है, जैसे M.S. PUROHIT या PUROHIT। उस दिन की सूची में "
     "उस नाम वाले सभी केस कोर्ट-वार दिखेंगे। नाम से खोज पिछले 14 दिनों और आने वाली तारीखों की सूचियों में होती है।"),
    ("CNR नंबर से केस कैसे खोजें?",
     "16 अक्षरों का CNR नंबर सीधे लिखें, जैसे RJHC010541342024। जोधपुर (मुख्य पीठ) के CNR RJHC01 से और जयपुर पीठ के "
     "RJHC02 से शुरू होते हैं। जो केस हमारे पास उपलब्ध किसी वाद सूची में आ चुका है, उसका केस नंबर और सभी लिस्टिंग "
     "तारीखें दिख जाती हैं।"),
    ("डेली लिस्ट और सप्लीमेंट्री लिस्ट में क्या अंतर है?",
     "डेली कॉज़ लिस्ट सामान्यतः सुनवाई से पहली शाम जारी होती है। सप्लीमेंट्री लिस्ट बाद में जोड़े गए मामलों की अतिरिक्त सूची है, "
     "जो कभी-कभी सुनवाई के दिन भी आती है। परिणाम में D, S और D+S का निशान बताता है कि केस किस सूची में है।"),
    ("आइटम नंबर का क्या मतलब है?",
     "आइटम नंबर उस दिन उस कोर्ट की सूची में केस का क्रमांक है। कोर्ट सामान्यतः इसी क्रम में मामले लेती है, पर क्रम बदल सकता है। "
     "“with” लिखा हो तो केस उस आइटम के साथ जुड़ा है।"),
    ("केस की स्थिति (केस स्टेटस) और आदेश कहाँ देखें?",
     "यह खोज केवल वाद सूची में लिस्टिंग — तारीख, कोर्ट और आइटम — दिखाती है। केस की वर्तमान स्थिति और आदेश न्यायालय की "
     "वेबसाइट hcraj.nic.in के Case Status में CNR या केस नंबर से देखें।"),
    ("क्या यह राजस्थान हाईकोर्ट की आधिकारिक वेबसाइट है?",
     "नहीं। यह राजस्थान उच्च न्यायालय द्वारा प्रकाशित वाद सूचियों का अनौपचारिक संकलन है, जिसे शुभम ओझा एंड एसोसिएट्स, "
     "अधिवक्ता, जोधपुर ने तैयार किया है। आधिकारिक रिकॉर्ड केवल hcraj.nic.in है; कृपया वहाँ पुष्टि अवश्य करें।"),
]


def howto_html(lang):
    """The 3-step picture guide. Sits inside #res: seen until the first search, and read by search engines."""
    hi = lang == "hi"
    steps = [
        ('<div class="mock"><span class="mi">CW 1234/2026<i class="cur"></i></span><span class="mb">' + ("खोजें" if hi else "Search") + '</span></div>',
         "केस नंबर, वकील का नाम या CNR लिखें", "Type a case number, advocate or party name, or CNR"),
        ('<div class="mock"><span class="ms">' + ("आज + अगली सुनवाई" if hi else "Today + next hearing day") + ' ▾</span>'
         '<span class="ms">All dates ▾</span></div>',
         "तारीख चुनें — पुराने केस के लिए “All dates”", "Choose the dates — “All dates” for a case’s history"),
        ('<div class="mock res"><span class="mc">Court No. 8</span><span class="mt"><small>item</small>54</span><span class="bd">D</span></div>',
         "कोर्ट नंबर और आइटम नंबर देखें", "See the court number, item number and list"),
    ]
    lis = "".join(f'<li><span class="sn">{i + 1}</span>{m}<b lang="hi">{h}</b><small lang="en">{en}</small></li>'
                  for i, (m, h, en) in enumerate(steps))
    head = ('<h2><span lang="hi">कॉज़ लिस्ट में केस कैसे खोजें — 3 आसान कदम</span>'
            '<small lang="en">How to search the cause list</small></h2>')
    ex = ('<p class="ex"><span lang="hi">उदाहरण</span> / Examples: <code>CW 1234/2026</code> <code>1234/2026</code> '
          '<code>CRLMB 567 of 2025</code> <code>M.S. PUROHIT</code> <code>RJHC010541342024</code></p>')
    return f'<section class="howto" aria-label="How to search">{head}<ol class="steps">{lis}</ol>{ex}</section>'


def _faq_ld_from(pairs, lang):
    return {"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": lang, "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs]}


def home_texts(lang, answer, modified_stamp, latest=None):
    """Every string that differs between /rhc-causelist/ (English) and /rhc-causelist/hi/ (Hindi)."""
    latest = latest or {}
    lat_en = "".join(f' · <a href="{slug}/{d}/">{city} cause list, {dt.date.fromisoformat(d):%d %b %Y} (court-wise)</a>'
                     for slug, city, d in (("jodhpur", "Jodhpur", latest.get("jdp")), ("jaipur", "Jaipur Bench", latest.get("jp"))) if d)
    lat_hi = "".join(f' · <a href="../{slug}/{d}/">{city} कॉज़ लिस्ट {dt.date.fromisoformat(d).day} {HI_MONTH[dt.date.fromisoformat(d).month]} (कोर्ट-वार)</a>'
                     for slug, city, d in (("jodhpur", "जोधपुर", latest.get("jdp")), ("jaipur", "जयपुर पीठ", latest.get("jp"))) if d)
    if lang == "hi":
        about = ('<div class="card"><h2>राजस्थान हाईकोर्ट कॉज़ लिस्ट — जोधपुर व जयपुर पीठ</h2>' + UNOFF_HTML
                 + '<p class="ans">राजस्थान उच्च न्यायालय, जोधपुर (मुख्य पीठ) और जयपुर पीठ की डेली व सप्लीमेंट्री वाद सूची (कॉज़ लिस्ट) में '
                 'केस नंबर, वकील के नाम, पक्षकार के नाम या CNR नंबर से खोजें। परिणाम में पीठ, कोर्ट नंबर, बेंच, आइटम नंबर और सूची का '
                 f'प्रकार दिखता है। अंतिम अपडेट: {modified_stamp}।</p>'
                 + "".join(f"<h3>{e(q)}</h3><p>{e(a)}</p>" for q, a in FAQ_HI)
                 + '<p><a href="case-kab-lagega/">मेरा केस कब लगेगा? — लिस्टिंग तारीख देखें</a> · <a href="../jodhpur/">राजस्थान हाईकोर्ट जोधपुर — आज की कॉज़ लिस्ट (कोर्ट-वार)</a> · '
                 '<a href="../jaipur/">जयपुर पीठ — आज की कॉज़ लिस्ट</a>' + lat_hi + ' · <a href="../" hreflang="en" lang="en">English</a> · '
                 '<a href="../status/">Data status</a> · <a href="../terms/">Terms of use</a></p></div>')
        return dict(
            lang="hi-IN", ogloc="hi_IN",
            title="राजस्थान हाईकोर्ट कॉज़ लिस्ट (वाद सूची) – जोधपुर व जयपुर | केस नंबर, वकील, CNR से खोजें",
            desc=("राजस्थान हाईकोर्ट जोधपुर व जयपुर पीठ की आज की कॉज़ लिस्ट (वाद सूची) — केस नंबर, वकील, पक्षकार या CNR से खोजें; "
                  "कोर्ट नंबर व आइटम नंबर तुरंत। डेली व सप्लीमेंट्री लिस्ट। अनौपचारिक संकलन।"),
            h1="राजस्थान हाईकोर्ट कॉज़ लिस्ट खोज — जोधपुर व जयपुर",
            sub="वाद सूची · डेली व सप्लीमेंट्री लिस्ट · Rajasthan High Court cause list search · अनौपचारिक",
            switch_href="../", switch_lang="en", switch_text="English",
            label="केस नंबर, वकील, पक्षकार का नाम या CNR", label2_lang="en", label2="Case number, advocate, party name or CNR",
            ph="जैसे CW 1234/2026, नाम या CNR", go="खोजें",
            a_when="कौन-सी तारीखें", a_date="सूची की तारीख", a_seat="पीठ",
            o_def="आज + अगली सुनवाई", o_date="एक तारीख…", o_7="पिछले 7 दिन", o_n=f"पिछले {SITE_DAYS} दिन",
            o_all="All dates — सभी तारीखें (केस नंबर / CNR)",
            s_both="दोनों पीठ", s_jdp="केवल जोधपुर", s_jp="केवल जयपुर",
            loading="आज की सूची लोड हो रही है…", h_upd="अंतिम अपडेट (Last updated)", about=about,
            a_my="मेरी सूची", upd=f"अपडेट: {modified_stamp} — जब हमारे पास उपलब्ध कोई सूची पिछली बार बदली (Updated)")
    about = ('<div class="card"><h2>About this search</h2>' + UNOFF_HTML + f'<p class="ans">{e(answer)}</p>'
             + '<h2 class="faqh">Questions about the Rajasthan High Court cause list</h2>'
             + "".join(f"<h3>{e(q)}</h3><p>{e(a)}</p>" for q, a in FAQ[:-1])
             + '<p class="hint">Rajasthan High Court cause list aaj ki — case number, advocate, party ya CNR se khojein. '
               '<a href="hi/" hreflang="hi" lang="hi">हिंदी में देखें</a></p>'
             + '<p><a href="when-is-my-case-listed/">When is my case listed? — check the listing date</a> · <a href="jodhpur/">Rajasthan High Court Jodhpur cause list today</a> · <a href="jaipur/">Rajasthan High Court Jaipur Bench cause list today</a>' + lat_en + ' · '
               '<a href="hi/" hreflang="hi" lang="hi">राजस्थान हाईकोर्ट कॉज़ लिस्ट (हिंदी)</a> · <a href="status/">Data status</a> · <a href="terms/">Terms of use</a></p>'
             + '<p class="hi" lang="hi">राजस्थान उच्च न्यायालय (जोधपुर एवं जयपुर पीठ) की वाद सूची — केस नंबर, अधिवक्ता, पक्षकार के नाम या CNR से खोजें। '
               'कृपया आधिकारिक वेबसाइट hcraj.nic.in पर पुष्टि अवश्य करें।</p></div>')
    return dict(
        lang="en-IN", ogloc="en_IN",
        title="Rajasthan High Court Cause List Search – Jodhpur & Jaipur | Case No., Advocate, CNR",
        desc=("Rajasthan High Court cause list today, Jodhpur and Jaipur Bench: search by case number, advocate, party "
              "name or CNR and see the court and item number. Daily and Supplementary lists. Unofficial."),
        h1="Rajasthan High Court Cause List — Jodhpur &amp; Jaipur",
        sub='Case number, advocate, party or CNR search · Daily and Supplementary lists · unofficial · <span lang="hi">राजस्थान हाईकोर्ट वाद सूची</span>',
        switch_href="hi/", switch_lang="hi", switch_text="हिंदी",
        label="Case number, advocate, party name or CNR", label2_lang="hi", label2="केस नंबर, वकील, पक्षकार का नाम या CNR",
        ph="e.g. CW 1234/2026, a name or a CNR", go="Search",
        a_when="Which dates", a_date="Date of the list", a_seat="Seat",
        o_def="Today + next hearing day", o_date="One date…", o_7="Last 7 days", o_n=f"Last {SITE_DAYS} days",
        o_all="All dates (case number / CNR)",
        s_both="Both seats", s_jdp="Jodhpur only", s_jp="Jaipur only",
        loading="Loading today’s list…", h_upd="Last updated", about=about,
        a_my="My list", upd=f"Updated {modified_stamp} — the last time a list we hold changed")



# ---------------------------------------------------------------- "When is my case listed?" pages (EN + HI)
WHEN_EN, WHEN_HI = "when-is-my-case-listed/", "hi/case-kab-lagega/"
WHEN_Q = ("When is my case listed in the Rajasthan High Court?",
          "Type your case number (for example CW 1234/2026) or the 16-character CNR in the search box. If the case is in "
          "any cause list we hold for today or a coming date, the answer shows the date, the seat (Jodhpur or Jaipur Bench), "
          "the court number, the item number and the list. If it is not in those lists yet, the answer says so and gives the "
          "last date on which it appeared. This is the listing in the published cause lists, not the case status; confirm on hcraj.nic.in.")
WHEN_Q_HI = ("मेरा केस कब लगेगा? राजस्थान हाईकोर्ट में केस की लिस्टिंग तारीख कैसे देखें?",
             "खोज-बॉक्स में केस नंबर (जैसे CW 1234/2026) या 16 अक्षरों का CNR लिखें। यदि केस आज या आगे की किसी उपलब्ध वाद सूची में है तो "
             "तारीख, पीठ (जोधपुर / जयपुर), कोर्ट नंबर, आइटम नंबर और सूची तुरंत दिखती है। यदि अभी किसी सूची में नहीं है तो वही बताया जाता है "
             "और पिछली लिस्टिंग की तारीख दिखती है। यह प्रकाशित वाद सूची की जानकारी है, केस स्टेटस नहीं; hcraj.nic.in पर पुष्टि अवश्य करें।")
WHEN_TEXT_EN = [
    "“When is my case listed?” means: on which date, in which court and at which serial number will the matter be "
    "called. The Rajasthan High Court answers that through its cause list. For every sitting day each "
    "seat — the Principal Seat at Jodhpur and the Bench at Jaipur — publishes a Daily cause list, normally on the evening "
    "before the hearing. Matters added later appear in a Supplementary list, which can come out on the hearing day itself.",
    "A case therefore becomes visible only when the court has published the list for that date. Until then this page does not guess. Type the case number (the type, number and year, such as "
    "CW 1234/2026), the CNR, or the name of the advocate or a party in the box above. If the case is in a list we hold for "
    "today or a coming date, the answer card shows the date, the seat, the court number, the item number and whether it is "
    "in the Daily or the Supplementary list. The item number is the serial position on that court’s board; courts generally "
    "take matters in that order, though the order can change.",
    "If the case is not in any list we hold yet, the card says so plainly, tells you up to which date lists are held, and "
    "shows the last date on which the case appeared. Tap ★ Save and the case stays in “My list” on your device; each time you "
    "open the page it is checked again against the newest lists. The date fixed by the court in its order sheet is part of "
    "the case status, which only the court’s own website shows — use Case Status on hcraj.nic.in for that, and always "
    "confirm a listing there.",
]
WHEN_FAQ_EN = [
    ("When is my case listed in Rajasthan High Court Jodhpur?",
     "Search your case number or CNR above. If the case is in a Daily or Supplementary cause list of the Principal Seat at "
     "Jodhpur that we hold for today or a coming date, you see the date, court number and item number. The Daily list for a "
     "day is normally published the evening before, so a case usually becomes visible one evening ahead."),
    ("How do I know the next date of my case?",
     "The next date fixed by the court is recorded in the case status and the order sheet on hcraj.nic.in (Case Status). "
     "This page shows something different: the dates on which the case actually appears in a published cause list. Save the "
     "case with ★ and it shows in “My list” as soon as a list containing it is held here."),
    ("Mera case kab lagega? Rajasthan High Court mein case ki listing kaise dekhein?",
     "Upar search box mein apna case number (jaise CW 1234/2026) ya CNR likhiye. Agar case aaj ya aane wali tareekh ki kisi "
     "cause list mein hai to tareekh, court number aur item number turant dikhega. Cause list aam taur par sunwai se ek "
     "shaam pehle jaari hoti hai. Kripya hcraj.nic.in par pushti zaroor karein."),
    ("Does this show the tentative or next date of my case?",
     "No. Only actual listings are shown: a date appears here only when the case is printed in the Daily or Supplementary "
     "cause list published by the court for that date. Tentative or next dates shown in the court\u2019s case status are not "
     "shown here. For the case status, use hcraj.nic.in."),
    ("How do I check a case listing date at the Jaipur Bench?",
     "The same search covers the Jaipur Bench. Type the case number; if the same number exists at both seats the answer "
     "shows each separately, marked Jodhpur or Jaipur Bench. You can also choose “Jaipur only” under the search box."),
    ("My case does not appear in the cause list — what does that mean?",
     "It means the case is not in the lists held here for the dates searched. The list for a later date may not have been "
     "published yet, a Supplementary list may still follow, or the matter may not be fixed for those days. A case that does "
     "not appear here may still be on the court’s board, so confirm on hcraj.nic.in."),
    ("Can I find my case by advocate name instead of case number?",
     "Yes. Type the advocate’s name as it is printed in the cause list, for example M.S. PUROHIT. All matters with that "
     "name in the lists for today and the next hearing day are shown court by court, and the name can be saved with ★."),
]
WHEN_TEXT_HI = [
    "“मेरा केस कब लगेगा?” — इस सवाल का मतलब है: किस तारीख को, किस कोर्ट में और किस नंबर पर मेरा मामला पुकारा जाएगा। राजस्थान हाईकोर्ट "
    "यह जानकारी अपनी वाद सूची (कॉज़ लिस्ट) से देता है। हर कार्य-दिवस के लिए दोनों पीठ — जोधपुर की मुख्य पीठ और जयपुर पीठ — अपनी डेली कॉज़ "
    "लिस्ट जारी करती हैं, सामान्यतः सुनवाई से एक शाम पहले। उसके बाद जोड़े गए मामले सप्लीमेंट्री लिस्ट में आते हैं, जो देर शाम या सुनवाई के दिन भी "
    "आ सकती है।",
    "इसलिए कोई केस तभी दिखता है जब कोर्ट उस तारीख की सूची जारी कर दे। उससे पहले यह पेज अंदाज़ा नहीं लगाता। ऊपर के बॉक्स में केस नंबर "
    "(प्रकार, नंबर और वर्ष — जैसे CW 1234/2026), CNR नंबर, या वकील अथवा पक्षकार का नाम लिखें। यदि केस आज या आगे की किसी उपलब्ध सूची में "
    "है तो उत्तर-कार्ड में तारीख, पीठ, कोर्ट नंबर, आइटम नंबर और सूची का प्रकार (डेली / सप्लीमेंट्री) दिखता है। आइटम नंबर उस कोर्ट के बोर्ड पर केस "
    "का क्रमांक है; कोर्ट सामान्यतः इसी क्रम में मामले लेती है, पर क्रम बदल सकता है।",
    "यदि केस अभी किसी उपलब्ध सूची में नहीं है तो कार्ड साफ़ बताता है कि सूचियाँ किस तारीख तक उपलब्ध हैं और केस पिछली बार कब लगा था। "
    "★ सेव करें — केस आपके फ़ोन की “मेरी सूची” में रहेगा और पेज खोलते ही नई सूचियों में फिर देखा जाएगा। कोर्ट द्वारा आदेश में दी गई अगली तारीख "
    "केस स्टेटस का हिस्सा है, जो केवल न्यायालय की वेबसाइट hcraj.nic.in (Case Status) पर मिलती है; लिस्टिंग की पुष्टि भी वहीं अवश्य करें।",
]
WHEN_FAQ_HI = [
    ("राजस्थान हाईकोर्ट जोधपुर में मेरा केस कब लगेगा?",
     "ऊपर केस नंबर या CNR से खोजें। यदि केस जोधपुर मुख्य पीठ की आज या आगे की किसी उपलब्ध डेली या सप्लीमेंट्री वाद सूची में है तो तारीख, "
     "कोर्ट नंबर और आइटम नंबर दिखेगा। डेली लिस्ट सामान्यतः एक शाम पहले जारी होती है, इसलिए केस प्रायः एक शाम पहले दिखने लगता है।"),
    ("केस की अगली तारीख कैसे पता करें?",
     "कोर्ट की दी हुई अगली तारीख केस स्टेटस और आदेश-पत्र में दर्ज होती है — उसे hcraj.nic.in के Case Status में देखें। यह पेज वे तारीखें "
     "दिखाता है जिन पर केस वास्तव में प्रकाशित वाद सूची में आया है। ★ से सेव करें; जिस सूची में केस होगा वह हमारे पास आते ही “मेरी सूची” में दिखेगा।"),
    ("क्या यहाँ केस की संभावित (टेंटेटिव) या अगली तारीख दिखती है?",
     "नहीं। यहाँ केवल वास्तविक लिस्टिंग दिखती है — कोई तारीख तभी दिखेगी जब केस उस दिन की न्यायालय द्वारा प्रकाशित डेली या "
     "सप्लीमेंट्री वाद सूची में छपा हो। केस स्टेटस में दिखने वाली संभावित (टेंटेटिव) या अगली तारीख यहाँ नहीं दिखाई जाती; "
     "उसके लिए hcraj.nic.in देखें।"),
    ("जयपुर पीठ में केस की लिस्टिंग तारीख कैसे देखें?",
     "यही खोज जयपुर पीठ के लिए भी है। केस नंबर लिखें; यदि वही नंबर दोनों पीठ में है तो उत्तर दोनों के लिए अलग-अलग दिखता है। खोज-बॉक्स के नीचे "
     "“केवल जयपुर” भी चुन सकते हैं।"),
    ("मेरा केस कॉज़ लिस्ट में नहीं दिख रहा — इसका क्या मतलब है?",
     "इसका मतलब है कि केस खोजी गई तारीखों की हमारे पास उपलब्ध सूचियों में नहीं है। आगे की तारीख की सूची अभी जारी न हुई हो, सप्लीमेंट्री लिस्ट "
     "बाद में आ सकती है, या मामला उन दिनों के लिए नियत न हो। जो केस यहाँ नहीं दिखता वह भी कोर्ट के बोर्ड पर हो सकता है; hcraj.nic.in पर पुष्टि करें।"),
    ("क्या वकील के नाम से भी केस देख सकते हैं?",
     "हाँ। वकील का नाम अंग्रेज़ी में वैसे लिखें जैसे वाद सूची में छपता है, जैसे M.S. PUROHIT। आज और अगली सुनवाई की तारीख की सूचियों में उस नाम "
     "वाले सभी मामले कोर्ट-वार दिखते हैं; नाम को ★ से सेव भी कर सकते हैं।"),
]


def when_texts(lang, modified_stamp):
    """The 'When is my case listed?' page = the home search page with its own title, H1 and explanation."""
    tx = home_texts(lang, "", modified_stamp)
    if lang == "hi":
        about = ('<div class="card"><h2>राजस्थान हाईकोर्ट में केस की लिस्टिंग कैसे होती है</h2>' + UNOFF_HTML
                 + "".join(f'<p class="ans">{e(p)}</p>' for p in WHEN_TEXT_HI) + f'<p class="hint">अंतिम अपडेट: {modified_stamp}</p>'
                 + '<h2 class="faqh">सवाल-जवाब: केस कब लगेगा</h2>' + "".join(f"<h3>{e(q)}</h3><p>{e(a)}</p>" for q, a in WHEN_FAQ_HI)
                 + '<p><a href="../">राजस्थान हाईकोर्ट कॉज़ लिस्ट खोज (हिंदी)</a> · <a href="../../jodhpur/">जोधपुर — आज की कॉज़ लिस्ट</a> · '
                   '<a href="../../jaipur/">जयपुर पीठ — आज की कॉज़ लिस्ट</a> · <a href="../../when-is-my-case-listed/" hreflang="en" lang="en">When is my case listed? (English)</a></p></div>')
        tx.update(title="मेरा केस कब लगेगा? राजस्थान हाईकोर्ट जोधपुर व जयपुर – लिस्टिंग तारीख देखें",
                  desc=("मेरा केस कब लगेगा? राजस्थान हाईकोर्ट जोधपुर व जयपुर पीठ की प्रकाशित कॉज़ लिस्ट में केस नंबर या CNR से देखें — तारीख, कोर्ट नंबर, "
                        "आइटम नंबर। केस की अगली लिस्टिंग, अनौपचारिक संकलन।"),
                  h1="मेरा केस कब लगेगा? — राजस्थान हाईकोर्ट, जोधपुर व जयपुर",
                  sub="केस की लिस्टिंग तारीख, कोर्ट नंबर और आइटम नंबर · प्रकाशित वाद सूची से · केस स्टेटस नहीं · अनौपचारिक",
                  switch_href="../../" + WHEN_EN, about=about)
    else:
        about = ('<div class="card"><h2>How listing works at the Rajasthan High Court</h2>' + UNOFF_HTML
                 + "".join(f'<p class="ans">{e(p)}</p>' for p in WHEN_TEXT_EN) + f'<p class="hint">Last updated {modified_stamp}</p>'
                 + '<h2 class="faqh">Questions: when is my case listed?</h2>' + "".join(f"<h3>{e(q)}</h3><p>{e(a)}</p>" for q, a in WHEN_FAQ_EN)
                 + '<p><a href="../">Rajasthan High Court cause list search</a> · <a href="../jodhpur/">Jodhpur cause list today</a> · '
                   '<a href="../jaipur/">Jaipur Bench cause list today</a> · <a href="../' + WHEN_HI + '" hreflang="hi" lang="hi">मेरा केस कब लगेगा? (हिंदी)</a></p></div>')
        tx.update(title="When Is My Case Listed? Rajasthan High Court Jodhpur & Jaipur – Check Listing Date",
                  desc=("When is my case listed? Check the listing date of a Rajasthan High Court case, Jodhpur or Jaipur Bench, in the "
                        "published cause lists by case number or CNR: date, court and item number. Unofficial."),
                  h1="When Is My Case Listed? — Rajasthan High Court, Jodhpur &amp; Jaipur",
                  sub='Listing date, court number and item number from the published cause lists · not the case status · unofficial · <span lang="hi">मेरा केस कब लगेगा</span>',
                  switch_href="../" + WHEN_HI, about=about)
    return tx


def _when_ld(lang, url, tx, modified):
    page = dict(_webpage(url, re.sub(r"&amp;", "&", tx["h1"]), tx["desc"], modified), inLanguage=tx["lang"])
    crumbs = [("Rajasthan High Court cause list search", BASE_URL)] + ([("हिंदी", BASE_URL + "hi/")] if lang == "hi" else []) + [
        ("मेरा केस कब लगेगा?" if lang == "hi" else "When is my case listed?", url)]
    return _jsonld(page, _crumbs_ld(crumbs), _faq_ld_from(WHEN_FAQ_HI if lang == "hi" else WHEN_FAQ_EN, tx["lang"]))


# ---------------------------------------------------------------- entry point
def build(con, vdir, day_files: dict, cov: dict, now: dt.datetime):
    """Write <vdir>/site. day_files = {iso date: parsed day dict already written at <vdir>/cl_<date>.json}.
    The caller holds ONE read transaction on `con`. -> list of file names relative to vdir."""
    site = os.path.join(vdir, "site")
    for sub in ("data/d", "data/cx", "data/cn", "jodhpur", "jaipur", "status", "terms", "hi", WHEN_EN, WHEN_HI):
        os.makedirs(os.path.join(site, sub), exist_ok=True)
    today_iso = now.date().isoformat()
    names = []

    def put(rel, text):
        with open(os.path.join(site, rel), "w", encoding="utf-8") as f:
            f.write(text)
        names.append("site/" + rel)

    ver = {}
    with open(os.path.join(SRC, "soa.svg"), "rb") as f_in, open(os.path.join(site, "soa.svg"), "wb") as f_out:
        f_out.write(f_in.read())                      # the firm seal (a copy of the logo-kit master)
    names.append("site/soa.svg")
    for k, fn in (("css", "app.css"), ("js", "app.js")):
        raw = open(os.path.join(SRC, fn), "rb").read()
        with open(os.path.join(site, fn), "wb") as f:
            f.write(raw)
        names.append("site/" + fn)
        ver[k] = hashlib.sha256(raw).hexdigest()[:10]
    # installable app: icons + manifest are copied as they are; the service worker carries the asset
    # version, so its bytes change exactly when app.js / app.css / an icon changes
    hv = hashlib.sha256((ver["css"] + ver["js"]).encode())
    for fn in ICONS + ("app.webmanifest",):
        raw = open(os.path.join(SRC, fn), "rb").read()
        with open(os.path.join(site, fn), "wb") as f:
            f.write(raw)
        names.append("site/" + fn)
        hv.update(raw)
    sw = open(os.path.join(SRC, "sw.js"), encoding="utf-8").read()
    hv.update(sw.encode())
    put("sw.js", sw.replace("{{ver}}", hv.hexdigest()[:12]).replace(
        "{{assets}}", json.dumps([f"app.js?v={ver['js']}", f"app.css?v={ver['css']}", "icon-192.png"])))

    lists = {}
    for d, data in day_files.items():
        # The site copy of a day file carries NO fetch times (they change at every poll and would
        # rewrite the big file each time); the times live in the small meta.json instead.
        lists[d] = data["lists"]
        slim = dict({"attribution": ATTRIBUTION}, **data)
        slim["lists"] = {s_: {t: v[:2] for t, v in m.items()} for s_, m in data["lists"].items()}
        with open(os.path.join(site, "data", "d", f"cl_{d}.json"), "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
        names.append(f"site/data/d/cl_{d}.json")
    cx, cx_names = build_case_index(con, os.path.join(site, "data", "cx"), today_iso[:8] + "01", cov)
    names += ["site/data/cx/" + n for n in cx_names]
    cn, cn_names = build_cnr_index(con, os.path.join(site, "data", "cn"), today_iso[:8] + "01")
    names += ["site/data/cn/" + n for n in cn_names]

    pub = public_status(con, now)
    all_dates = [r[0] for r in con.execute("SELECT DISTINCT date FROM lists WHERE state='list' ORDER BY date")]
    put("status.json", json.dumps(dict({"attribution": ATTRIBUTION}, **pub), ensure_ascii=False, separators=(",", ":")))
    put("data/coverage.json", json.dumps(dict({"attribution": ATTRIBUTION}, **cov), ensure_ascii=False, separators=(",", ":")))
    put("data/meta.json", json.dumps({
        "attribution": ATTRIBUTION, "v": 1, "published_at": now.isoformat(timespec="seconds"), "site_days": SITE_DAYS,
        "site_dates": sorted(day_files), "dates": all_dates, "cx": cx, "cn": cn, "lists": lists,
        "ct": {k: list(v) for k, v in CASE_TYPES.items()},        # full forms of case-type labels (added key)
        "days": {d: hashlib.sha256(open(os.path.join(site, "data", "d", f"cl_{d}.json"), "rb").read()).hexdigest()[:10] for d in day_files},
    }, ensure_ascii=False, separators=(",", ":")))

    desc = ("Search the Rajasthan High Court cause list by case number, advocate or party: Jodhpur and Jaipur, "
            "Daily and Supplementary lists. Unofficial compilation.")
    # freshness = the last time a list we hold really changed (not the clock of this build)
    lo = min(day_files) if day_files else "0000"          # only the lists shown on the site (a backfill of old dates is not "fresh")
    modified = con.execute("SELECT max(changed_at) FROM lists WHERE state='list' AND date>=?", (lo,)).fetchone()[0] or now.isoformat(timespec="seconds")
    seat_mod = {r[0]: r[1] for r in con.execute("SELECT seat, max(changed_at) FROM lists WHERE state='list' AND date>=? GROUP BY seat", (lo,))}
    answer = (f"Search the Rajasthan High Court cause list for Jodhpur (Principal Seat) and the Jaipur Bench by case number, "
              f"advocate name, party name or CNR. Results show the court number, bench, item number and whether the "
              f"matter is in the Daily or the Supplementary list. Last updated {_stamp(modified)}.")
    dsum = date_summaries(con, day_files, today_iso)
    by_seat = {s_: [d for d in sorted(dsum) if s_ in dsum[d]] for s_ in ("jdp", "jp")}
    latest = {s_: next((d for d in reversed(v) if d <= today_iso), v[-1] if v else None) for s_, v in by_seat.items()}
    for lang, rel, root, canon in (("en", "index.html", "", BASE_URL), ("hi", "hi/index.html", "../", BASE_URL + "hi/")):
        tx = home_texts(lang, answer, _stamp(modified), latest)
        crumbs = _crumbs_ld([("Rajasthan High Court cause list search", BASE_URL)] + ([("हिंदी", canon)] if lang == "hi" else []))
        ld = _jsonld(_webapp(canon, "Rajasthan High Court cause list search" if lang == "en" else "राजस्थान हाईकोर्ट कॉज़ लिस्ट खोज",
                             tx["desc"], modified), crumbs,
                     _faq_ld_from(FAQ_HI if lang == "hi" else FAQ[:-1], tx["lang"]))
        put(rel, _fill("home.html", alt_en=BASE_URL, alt_hi=BASE_URL + "hi/", canon=canon, root=root, cssv=ver["css"], jsv=ver["js"],
                       coverage=_banner_html(cov, root), updated=_updated_html(pub, today_iso), footer=_footer(root),
                       days=str(SITE_DAYS), meta=META_TAGS, howto=howto_html(lang), jsonld=ld,
                       **{k: (v if k in ("about", "h1", "sub") else e(v)) for k, v in tx.items()}))
    for lang, rel, root in (("en", WHEN_EN, "../"), ("hi", WHEN_HI, "../../")):
        tx = when_texts(lang, _stamp(modified))
        put(rel + "index.html", _fill("home.html", alt_en=BASE_URL + WHEN_EN, alt_hi=BASE_URL + WHEN_HI, canon=BASE_URL + rel, root=root,
                                      cssv=ver["css"], jsv=ver["js"], coverage=_banner_html(cov, root), updated=_updated_html(pub, today_iso),
                                      footer=_footer(root), days=str(SITE_DAYS), meta=META_TAGS, howto=howto_html(lang),
                                      jsonld=_when_ld(lang, BASE_URL + rel, tx, modified),
                                      **{k: (v if k in ("about", "h1", "sub") else e(v)) for k, v in tx.items()}))
    put("jodhpur/index.html", landing_html("jdp", pub, day_files, today_iso, cov, ver, seat_mod.get("jdp", modified), by_seat["jdp"]))
    put("jaipur/index.html", landing_html("jp", pub, day_files, today_iso, cov, ver, seat_mod.get("jp", modified), by_seat["jp"]))
    date_urls = []
    for s_, slug in (("jdp", "jodhpur"), ("jp", "jaipur")):
        ds = by_seat[s_]
        for i, d in enumerate(ds):
            os.makedirs(os.path.join(site, slug, d), exist_ok=True)
            put(f"{slug}/{d}/index.html", date_page_html(s_, d, dsum[d][s_], ds[i - 1] if i else None, ds[i + 1] if i + 1 < len(ds) else None,
                                                        ("jp" if s_ == "jdp" else "jdp") in dsum[d], d in day_files, ver))
            date_urls.append((f"{slug}/{d}/", dsum[d][s_]["mod"]))
    put("status/index.html", status_html(pub, cov, today_iso, _last_health(), ver))
    put("404.html", _fill("page.html", title="Page not found | Rajasthan High Court cause list search", root=BASE_URL,
                          desc="Page not found.", head='<meta name="robots" content="noindex">\n' + META_TAGS, page="404",
                          brand="Rajasthan High Court cause list search", sub="Page not found", crumbs="",
                          body=f'<div class="card"><h1>This page does not exist</h1>{UNOFF_HTML}<p><a href="{BASE_URL}">Go to the cause list search</a>.</p></div>',
                          footer=_footer(BASE_URL), cssv=ver["css"], jsv=ver["js"], upd=f"Updated {_stamp(modified)}"))
    put("terms/index.html", _fill(
        "page.html", title="Terms of use | Rajasthan High Court cause list search", root="../", page="terms",
        desc="Terms of use of the Rajasthan High Court cause list search: source, permitted use, attribution, no warranty.",
        head=f'<link rel="canonical" href="{BASE_URL}terms/">\n{META_TAGS}\n' + _jsonld(_webpage(BASE_URL + "terms/", "Terms of use", "Terms of use of the Rajasthan High Court cause list search.", "2026-11-21T00:00:00+05:30")),
        brand="Rajasthan High Court cause list search", sub="Terms of use",
        crumbs='<nav class="crumbs"><a href="../">Cause list search</a> \u203a Terms of use</nav>',
        body='<div class="card"><h1>Terms of use</h1>' + UNOFF_HTML + "".join(f"<h3>{e(h)}</h3><p>{e(t)}</p>" for h, t in TERMS)
             + f'<p class="hi" lang="hi">{MAINT_HI} \u0915\u0943\u092a\u092f\u093e \u0906\u0927\u093f\u0915\u093e\u0930\u093f\u0915 \u0935\u0947\u092c\u0938\u093e\u0907\u091f hcraj.nic.in \u092a\u0930 \u092a\u0941\u0937\u094d\u091f\u093f \u0905\u0935\u0936\u094d\u092f \u0915\u0930\u0947\u0902\u0964</p>'
             + f"<p class=\"hint\">\u00a9 2026 Shubham Ojha &amp; Associates, Advocates, Jodhpur. Last revised 21-Nov-2026.</p></div>",
        footer=_footer("../"), cssv=ver["css"], jsv=ver["js"], upd="Updated 21 Nov 2026 (terms last revised)"))
    # 22-Nov-2026: court calendars (HC + civil/trial courts), EN + HI
    import calendar_page
    for lang, rel, root, url, alt in (("en", "calendar/index.html", "../", BASE_URL + "calendar/", BASE_URL + "hi/calendar/"),
                                      ("hi", "hi/calendar/index.html", "../../", BASE_URL + "hi/calendar/", BASE_URL + "calendar/")):
        os.makedirs(os.path.dirname(os.path.join(site, rel)), exist_ok=True)
        c_title, c_desc, c_h1, c_body, c_faq = calendar_page.build(con, lang, today_iso)
        c_head = (f'<link rel="canonical" href="{url}">\n<link rel="alternate" hreflang="en-IN" href="{BASE_URL}calendar/">\n'
                  f'<link rel="alternate" hreflang="hi-IN" href="{BASE_URL}hi/calendar/">\n<link rel="alternate" hreflang="x-default" href="{BASE_URL}calendar/">\n'
                  f'{META_TAGS}\n' + _jsonld(_webpage(url, c_h1, c_desc, modified),
                                                _crumbs_ld([("Rajasthan High Court cause list search", BASE_URL), (c_h1, url)]),
                                                {"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": "hi-IN" if lang == "hi" else "en-IN",
                                                 "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a_}} for q, a_ in c_faq]}))
        put(rel, _fill("page.html", title=e(c_title), desc=e(c_desc), head=c_head, root=root, page="calendar",
                       brand="Rajasthan High Court cause list search", sub=e("Court calendar 2026 · कोर्ट कैलेंडर 2026"),
                       crumbs=f'<nav class="crumbs"><a href="{root}">Cause list search</a> › {e(c_h1)} · '
                              f'<a href="{alt}" hreflang="{"hi" if lang == "en" else "en"}">{"हिंदी" if lang == "en" else "English"}</a></nav>',
                       body=c_body, footer=_footer(root), cssv=ver["css"], jsv=ver["js"],
                       upd="Calendar source: Rajasthan High Court calendars 2026 (checked 22 Nov 2026)"))
    put("robots.txt", f"User-agent: *\nDisallow: /rhc-causelist/data/\nDisallow: /data/\nSitemap: {BASE_URL}sitemap.xml\n")   # honoured only when served from a domain root; the firm robots.txt must carry these lines
    put("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{BASE_URL}{p}</loc><lastmod>{m}</lastmod><changefreq>hourly</changefreq></url>\n"
                  for p, m in (("", modified), ("hi/", modified), (WHEN_EN, modified), (WHEN_HI, modified), ("jodhpur/", seat_mod.get("jdp", modified)), ("jaipur/", seat_mod.get("jp", modified)),
                               ("calendar/", modified), ("hi/calendar/", modified),
                               ("terms/", "2026-11-21T00:00:00+05:30")))
        + "".join(f"<url><loc>{BASE_URL}{p}</loc><lastmod>{m}</lastmod></url>\n" for p, m in sorted(date_urls, key=lambda z: (z[0][-11:], z[0]), reverse=True))
        + "</urlset>\n")
    put(".nojekyll", "")
    return names, status_essence(pub)
