"""calendar_page.py - /rhc-causelist/calendar/ (English) and /rhc-causelist/hi/calendar/ (Hindi):
Rajasthan High Court calendar 2026 + Civil (trial) Courts calendar 2026, month grids, full holiday list,
differences between the two, restricted holidays, observed extra closures, "is the court open today?".
Data: courtcal.py (the court's own calendars). Observed closures: from the lists the court actually
published (a calendar working day with NO Daily and NO Supplementary list while a later list exists).
Self-contained styles + a tiny inline script; called from site_build.build()."""
import datetime as dt
import json

import courtcal as C

MON_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MON_HI = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितम्बर", "अक्टूबर", "नवम्बर", "दिसम्बर"]
WD_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WD_HI = ["सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि"]
WDF_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WDF_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
YEAR = 2026

CSS = """<style>
.calw{--n:#0B1A33;--m:#112246;--g:#C9A24D;--c:#F0E8D6;--o:#f8f6f1}
.calw .today{background:var(--n);color:var(--c);border-radius:12px;padding:16px;margin:0 0 14px;border-bottom:4px solid var(--g)}
.calw .today h2{color:var(--g);margin:0 0 8px;font-size:1.15rem}
.calw .tgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.calw .tbox{background:var(--m);border-radius:8px;padding:10px 12px}
.calw .tbox b{display:block;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--g)}
.calw .tbox .st{font-size:1.2rem;font-weight:700;margin:2px 0}
.calw .tbox small{color:var(--c);opacity:.9}
.calw .tabs input{position:absolute;opacity:0}
.calw .tabs label{display:inline-block;padding:10px 16px;border:1px solid var(--n);border-radius:8px 8px 0 0;margin-right:4px;cursor:pointer;font-weight:600;background:#fff}
.calw .tabs input:checked+label{background:var(--n);color:var(--c)}
.calw .pane{display:none;border-top:3px solid var(--n);padding-top:12px}
#t-hc:checked~.pane-hc,#t-cc:checked~.pane-cc{display:block}
.calw .months{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.calw .mo{background:#fff;border:1px solid #ddd5c2;border-radius:10px;padding:10px}
.calw .mo h3{margin:0 0 6px;font-size:1rem;display:flex;justify-content:space-between}
.calw .mo h3 small{font-weight:400;color:#3d4a63;font-family:inherit}
.calw table.g{width:100%;border-collapse:separate;border-spacing:2px;font-size:.84rem;table-layout:fixed}
.calw table.g th{background:none;font-size:.7rem;color:#3d4a63;text-align:center;padding:2px 0;font-weight:600}
.calw table.g td{text-align:center;padding:5px 0;border-radius:6px;border:1px solid transparent;cursor:default}
.calw td.w{background:var(--o)}
.calw td.su,.calw td.ho,.calw td.nw{background:var(--n);color:var(--c);font-weight:700}
.calw td.sa{background:var(--c);color:var(--n);border-color:var(--n);font-weight:700}
.calw td.va{background:#e7d3a3;color:var(--n);font-weight:600}
.calw td.oo{background:repeating-linear-gradient(135deg,#fff 0 4px,var(--c) 4px 8px);border:1px dashed var(--n);font-weight:700}
.calw td.ob{border:2px dashed var(--g);font-weight:700}
.calw td.tw{box-shadow:inset 0 0 0 2px var(--g)}
.calw td.now{outline:3px solid var(--g);outline-offset:1px}
.calw td[data-r]{cursor:pointer}
.calw .ml{list-style:none;margin:8px 0 0;padding:0;font-size:.8rem;line-height:1.45}
.calw .ml li b{display:inline-block;min-width:3.2em;color:var(--n)}
.calw .moon{color:#6b5210}
.calw .lg{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:.8rem;margin:6px 0 12px}
.calw .lg span{display:inline-flex;align-items:center;gap:6px}
.calw .lg i{display:inline-block;width:18px;height:18px;border-radius:4px;border:1px solid transparent}
.calw .why{min-height:1.4em;font-size:.88rem;background:var(--c);border-left:4px solid var(--n);padding:6px 10px;border-radius:0 6px 6px 0;margin:0 0 10px}
.calw table.hl{font-size:.86rem}
.calw table.hl td.x{font-weight:700}
.calw .dif td:nth-child(3),.calw .dif td:nth-child(4){white-space:nowrap}
@media(max-width:560px){.calw .tgrid{grid-template-columns:1fr}.calw .tabs label{padding:9px 10px;font-size:.9rem}}
@media print{.calw .tabs label,.calw .why{display:none}.calw .pane{display:block!important}.calw .mo{break-inside:avoid}}
</style>"""

JS = """<script>(function(){var C=JSON.parse(document.getElementById('caldata').textContent),L=document.documentElement.lang.indexOf('hi')===0;
function iso(d){return d.toISOString().slice(0,10)}var now=new Date(Date.now()+19800000),t=iso(now);
function st(court,d){var x=C[court][d];if(x)return x;var w=new Date(d+'T00:00:00Z').getUTCDay();return w===0?(L?['रविवार']:['Sunday']):null}
function nxt(court,d){var x=new Date(d+'T00:00:00Z');for(var i=0;i<40;i++){x=new Date(x.getTime()+864e5);var s=iso(x);if(s.slice(0,4)!==String(C.year))return null;if(!st(court,s))return s}return null}
function fmt(d){var x=new Date(d+'T00:00:00Z');return (L?C.wdh:C.wde)[(x.getUTCDay()+6)%7]+', '+x.getUTCDate()+' '+(L?C.mh:C.me)[x.getUTCMonth()]+' '+x.getUTCFullYear()}
if(t.slice(0,4)===String(C.year)){['hc','cc'].forEach(function(k){var b=document.getElementById('now-'+k);if(!b)return;var s=st(k,t),n=nxt(k,t);
b.querySelector('.st').textContent=s?(L?'बंद':'Closed'):(L?'खुला — बैठक का दिन':'Open — working day');
b.querySelector('small').textContent=(s?s[0]+'. ':'')+(n?(L?'अगला कार्य दिवस: ':'Next working day: ')+fmt(n):'');});
var h=document.getElementById('now-date');if(h)h.textContent=fmt(t);
Array.prototype.forEach.call(document.querySelectorAll('td[data-d="'+t+'"]'),function(td){td.classList.add('now')})}
document.addEventListener('click',function(e){var td=e.target.closest&&e.target.closest('td[data-r]');if(!td)return;var w=td.closest('.pane').querySelector('.why');
w.textContent=fmt(td.getAttribute('data-d'))+' — '+td.getAttribute('data-r')})})();</script>"""


def _esc(s):
    import html
    return html.escape(str(s), quote=True)


def observed_closures(con, seat="jdp"):
    """Calendar working days (HC) of YEAR on which the court published NO Daily and NO Supplementary list
    while a later date's list exists (the four-part test in the HC calendar note)."""
    rows = {}
    for d, t, s in con.execute("SELECT date, list_type, state FROM lists WHERE seat=? AND date LIKE ?", (seat, f"{YEAR}-%")):
        rows.setdefault(d, {})[t] = s
    held = sorted(d for d, m in rows.items() if m.get("D") == "list")
    last = held[-1] if held else ""
    out = []
    for d, m in sorted(rows.items()):
        x = dt.date.fromisoformat(d)
        if C.sits("hc", x) and m.get("D") == "no_record" and m.get("S") == "no_record" and d < last:
            out.append(x)
    return out


def _state(court, d, observed):
    c = C.closed(court, d)
    if c is None:
        if court == "hc" and d in observed:
            return "ob", ("Court did not sit — no cause list was published", "न्यायालय की बैठक नहीं हुई — कोई वाद सूची प्रकाशित नहीं हुई")
        if d.weekday() == 5 and ((court == "cc" and d in C.CC_WORKING_SATURDAYS) or (court == "hc" and (d.day - 1) // 7 + 1 in (2, 4))):
            return "tw", ("Working Saturday", "कार्य दिवस (शनिवार)")
        return "w", None
    kind, en, hi, moon = c
    cls = {"sunday": "su", "holiday": "ho", "saturday": "sa", "vacation": "va", "nonworking": "nw", "office_only": "oo"}[kind]
    return cls, (en + (" *" if moon else ""), hi + (" *" if moon else ""))


def _month(court, m, lang, observed):
    hi = lang == "hi"
    first = dt.date(YEAR, m, 1)
    days = (dt.date(YEAR + (m == 12), m % 12 + 1, 1) - first).days
    cells = [""] * first.weekday()
    items = []
    for i in range(days):
        d = first + dt.timedelta(i)
        cls, why = _state(court, d, observed)
        attr = f' class="{cls}" data-d="{d.isoformat()}"'
        if why:
            attr += f' data-r="{_esc(why[1] if hi else why[0])}" title="{_esc(why[1] if hi else why[0])}"'
            if cls not in ("su",) and not (cls == "va" and items and items[-1][2] == why):
                items.append((d, cls, why))
        cells.append(f"<td{attr}>{d.day}</td>")
    while len(cells) % 7:
        cells.append("<td></td>")
    wd = WD_HI if hi else WD_EN
    body = "".join("<tr>" + "".join(c if c else "<td></td>" for c in cells[r:r + 7]) + "</tr>" for r in range(0, len(cells), 7))
    # month list: vacation as one range line
    lis, seen_vac = [], set()
    for d, cls, why in items:
        if cls == "va":
            v = next(v for v in C.VACATIONS[court] if v[0] <= d <= v[1])
            if v in seen_vac:
                continue
            seen_vac.add(v)
            a, b = max(v[0], first), min(v[1], first + dt.timedelta(days - 1))
            lis.append(f"<li><b>{a.day:02d}–{b.day:02d}</b> {_esc(v[3] if hi else v[2])}</li>")
            continue
        lab = why[1] if hi else why[0]
        moon = ' class="moon"' if lab.endswith("*") else ""
        lis.append(f"<li{moon}><b>{d.day:02d}</b> {_esc(lab)}</li>")
    n_work = sum(1 for i in range(days) if _state(court, first + dt.timedelta(i), observed)[0] in ("w", "tw"))
    mname = (MON_HI if hi else MON_EN)[m - 1]
    wk = f"{n_work} कार्य दिवस" if hi else f"{n_work} working days"
    return (f'<div class="mo"><h3>{mname} {YEAR}<small>{wk}</small></h3><table class="g"><tr>'
            + "".join(f"<th>{w}</th>" for w in wd) + f"</tr>{body}</table>"
            + (f'<ul class="ml">{"".join(lis)}</ul>' if lis else "") + "</div>")


def _legend(court, hi):
    it = [("w", "#f8f6f1", "Court working", "कार्य दिवस"), ("su", "#0B1A33", "Sunday / holiday", "रविवार / अवकाश"),
          ("sa", "#F0E8D6", "Second / Fourth Saturday", "द्वितीय / चतुर्थ शनिवार"), ("va", "#e7d3a3", "Vacation", "अवकाश काल (वेकेशन)")]
    if court == "hc":
        it += [("oo", "repeating-linear-gradient(135deg,#fff 0 4px,#F0E8D6 4px 8px)", "Office working only — court not sitting", "केवल कार्यालय — न्यायालय की बैठक नहीं"),
               ("ob", "#fff", "Court did not sit (no cause list published)", "बैठक नहीं हुई (वाद सूची प्रकाशित नहीं)")]
    it += [("tw", "#fff", "Working Saturday", "कार्य दिवस शनिवार")]
    bd = {"sa": "1px solid #0B1A33", "oo": "1px dashed #0B1A33", "ob": "2px dashed #C9A24D", "tw": "2px solid #C9A24D"}
    return '<div class="lg">' + "".join(
        f'<span><i style="background:{bg};border:{bd.get(k, "1px solid #ddd5c2")}"></i>{h if hi else e}</span>' for k, bg, e, h in it) + "</div>"


def _holiday_rows(hi):
    """One row per closed date (not Sundays-only, not plain vacation days): HC and CC status side by side."""
    rows, dates = [], sorted({x[0] for x in C.DAYS} | set(C.CC_WORKING_SATURDAYS))
    for d in dates:
        hc, cc = C.closed("hc", d), C.closed("cc", d)
        name = next((x for x in (hc, cc) if x and x[0] != "sunday"), None)
        if not name and not (d in C.CC_WORKING_SATURDAYS):
            continue
        lab = (name[2] if hi else name[1]) + (" *" if name and name[3] else "") if name else ("कार्य दिवस शनिवार" if hi else "Working Saturday")

        def mark(c):
            if c is None:
                return "खुला" if hi else "Open"
            return "बंद" if hi else "Closed"
        wd = (WDF_HI if hi else WDF_EN)[d.weekday()]
        rows.append(f'<tr><td class="n">{d.day:02d} {(MON_HI if hi else MON_EN)[d.month - 1]}</td><td>{wd}</td><td>{_esc(lab)}</td>'
                    f'<td class="x">{mark(hc)}</td><td class="x">{mark(cc)}</td></tr>')
    return rows


def _diffs(hi):
    out = []
    d = dt.date(YEAR, 1, 1)
    while d.year == YEAR:
        a, b = C.closed("hc", d), C.closed("cc", d)
        if (a is None) != (b is None):
            why = (a or b)
            out.append((d, "hc" if a else "cc", why[2] if hi else why[1]))
        d += dt.timedelta(1)
    o = "<tr><th>{}</th><th>{}</th><th>{}</th><th>{}</th></tr>".format(*(["तारीख", "कारण", "हाईकोर्ट", "अधीनस्थ न्यायालय"] if hi else ["Date", "Reason", "High Court", "Trial courts"]))
    for d, closed_in, why in out:
        cl, op = ("बंद", "खुला") if hi else ("Closed", "Open")
        o += (f'<tr><td class="n">{d.day:02d} {(MON_HI if hi else MON_EN)[d.month - 1]} ({(WD_HI if hi else WD_EN)[d.weekday()]})</td><td>{_esc(why)}</td>'
              f'<td>{cl if closed_in == "hc" else op}</td><td>{cl if closed_in == "cc" else op}</td></tr>')
    return o


FAQ_EN = [
    ("Is the Rajasthan High Court open on Saturday?",
     "Not on every Saturday. In 2026 the High Court is closed on the Saturdays its calendar names (mostly the second Saturday and many fourth Saturdays) "
     "and on a few Saturdays marked 'office working only'. Other Saturdays are working days — for example 24 January, 28 March, 25 April, 22 August, "
     "26 September and 28 November 2026 are working Saturdays of the High Court. The month grids above show every Saturday."),
    ("When is the summer vacation of the Rajasthan High Court in 2026?",
     "From Monday 1 June 2026 to Sunday 28 June 2026, both days inclusive. The court reopens on Monday 29 June 2026."),
    ("When are the winter holidays of the Rajasthan High Court in 2026?",
     "From Thursday 24 December 2026 to Thursday 31 December 2026 for the High Court. For the civil (trial) courts the winter holidays are 25 to 31 December 2026."),
    ("Are the High Court and the district (trial) courts closed on the same days?",
     "Mostly, but not always. The two calendars differ on some Saturdays and on a few High-Court-only non-working days; the table of differences on this page lists every such date."),
    ("What does the asterisk (*) on a holiday mean?",
     "The holiday depends on the visibility of the moon, so the actual date can shift by a day. The court notifies the final date."),
    ("Is the court open on a restricted holiday?",
     "Yes. Restricted holidays are for staff, who may choose two of them; the courts sit on those days."),
]
FAQ_HI = [
    ("क्या राजस्थान हाईकोर्ट शनिवार को खुला रहता है?",
     "हर शनिवार नहीं। 2026 में हाईकोर्ट उन्हीं शनिवारों को बंद रहता है जो उसके कैलेंडर में दिए हैं (अधिकतर द्वितीय शनिवार और कई चतुर्थ शनिवार) "
     "तथा कुछ शनिवार 'केवल कार्यालय खुला' हैं। बाकी शनिवार कार्य दिवस हैं — जैसे 24 जनवरी, 28 मार्च, 25 अप्रैल, 22 अगस्त, 26 सितम्बर और 28 नवम्बर 2026।"),
    ("राजस्थान हाईकोर्ट का 2026 का ग्रीष्मकालीन अवकाश कब है?",
     "सोमवार 1 जून 2026 से रविवार 28 जून 2026 तक (दोनों दिन सम्मिलित)। न्यायालय सोमवार 29 जून 2026 को खुलेगा।"),
    ("राजस्थान हाईकोर्ट का 2026 का शीतकालीन अवकाश कब है?",
     "हाईकोर्ट के लिए गुरुवार 24 दिसम्बर से गुरुवार 31 दिसम्बर 2026 तक; अधीनस्थ (दीवानी) न्यायालयों के लिए 25 से 31 दिसम्बर 2026 तक।"),
    ("क्या हाईकोर्ट और ज़िला (अधीनस्थ) न्यायालय एक ही दिन बंद रहते हैं?",
     "अधिकतर हाँ, पर हमेशा नहीं। कुछ शनिवारों और हाईकोर्ट के कुछ अकार्य दिवसों पर दोनों कैलेंडर अलग हैं; इस पेज की 'अंतर' तालिका में हर ऐसी तारीख दी गई है।"),
    ("अवकाश के आगे तारांकन (*) का क्या अर्थ है?",
     "वह अवकाश चन्द्र-दर्शन पर निर्भर है, इसलिए तारीख एक दिन आगे-पीछे हो सकती है। अंतिम तारीख न्यायालय अधिसूचित करता है।"),
    ("क्या ऐच्छिक (प्रतिबंधित) अवकाश पर न्यायालय खुला रहता है?",
     "हाँ। ऐच्छिक अवकाश कर्मचारियों के लिए हैं (वे दो चुन सकते हैं); उन दिनों न्यायालय बैठते हैं।"),
]


def build(con, lang, today_iso):
    """-> (title, desc, head_extra, body_html, faq_pairs). today_iso only for the build-time fallback text."""
    from site_build import UNOFF_HTML, e  # noqa: F401 (late import: site_build imports this module)
    hi = lang == "hi"
    obs = observed_closures(con, "jdp")
    # compact closed-map for the client: {"hc": {iso: [label]}, "cc": {...}}
    data = {"year": YEAR, "me": MON_EN, "mh": MON_HI, "wde": WDF_EN, "wdh": WDF_HI, "hc": {}, "cc": {}}
    d = dt.date(YEAR, 1, 1)
    while d.year == YEAR:
        for k in ("hc", "cc"):
            cls, why = _state(k, d, obs)
            if cls not in ("w", "tw", "su"):
                data[k][d.isoformat()] = [why[1] if hi else why[0]]
        d += dt.timedelta(1)
    t = dt.date.fromisoformat(today_iso)

    def now_box(k, name):
        s = C.closed(k, t) if t.year == YEAR else None
        st = ("बंद" if hi else "Closed") if s else ("खुला — बैठक का दिन" if hi else "Open — working day")
        return (f'<div class="tbox" id="now-{k}"><b>{name}</b><div class="st">{st}</div>'
                f'<small>{_esc((s[2] if hi else s[1]) if s else "")}</small></div>')
    hc_name, cc_name = (("राजस्थान हाईकोर्ट (जोधपुर व जयपुर)", "अधीनस्थ / ज़िला न्यायालय") if hi
                        else ("Rajasthan High Court (Jodhpur & Jaipur)", "Trial / district courts"))
    title = ("राजस्थान हाईकोर्ट कैलेंडर 2026 व अवकाश सूची | अधीनस्थ न्यायालय कैलेंडर — आज कोर्ट खुला है?" if hi else
             "Rajasthan High Court Calendar 2026 & Holiday List | Trial Court Calendar — Is the Court Open Today?")
    desc = ("राजस्थान हाईकोर्ट (जोधपुर व जयपुर) और अधीनस्थ न्यायालयों का 2026 कैलेंडर: अवकाश सूची, शनिवार, ग्रीष्म व शीतकालीन अवकाश, आज कोर्ट खुला है या नहीं।" if hi else
            "Rajasthan High Court calendar 2026 (Jodhpur & Jaipur) and the civil / trial courts calendar: full holiday list, Saturdays, "
            "summer and winter vacation, and whether the court is open today.")
    h1 = ("राजस्थान हाईकोर्ट व अधीनस्थ न्यायालय कैलेंडर 2026" if hi else "Rajasthan High Court & Trial Court Calendar 2026")
    intro = ("राजस्थान उच्च न्यायालय और अधीनस्थ (दीवानी / ज़िला) न्यायालयों का 2026 का कैलेंडर — उच्च न्यायालय द्वारा प्रकाशित कैलेंडर से। "
             "किसी तारीख पर टैप करें, कारण नीचे दिखेगा।" if hi else
             "The 2026 calendars of the Rajasthan High Court and of the civil (trial / district) courts, taken from the calendars "
             "published by the High Court. Tap any marked date to see why the court is closed.")
    body = [f'<div class="calw">{CSS}',
            f'<div class="card"><h1>{h1}</h1>{UNOFF_HTML}<p class="ans">{_esc(intro)}</p></div>',
            f'<div class="today"><h2>{"आज" if hi else "Today"} — <span id="now-date">{t.strftime("%A, %d %B %Y")}</span></h2>'
            f'<div class="tgrid">{now_box("hc", hc_name)}{now_box("cc", cc_name)}</div></div>',
            '<div class="card tabs">'
            f'<input type="radio" name="ct" id="t-hc" checked><label for="t-hc">{"हाईकोर्ट" if hi else "High Court"}</label>'
            f'<input type="radio" name="ct" id="t-cc"><label for="t-cc">{"अधीनस्थ न्यायालय" if hi else "Trial courts"}</label>']
    for k, nm in (("hc", hc_name), ("cc", ("अधीनस्थ (दीवानी) न्यायालय — कैलेंडर 2026" if hi else "Civil (trial) courts calendar 2026"))):
        body.append(f'<div class="pane pane-{k}"><h2>{nm}</h2>{_legend(k, hi)}<p class="why" aria-live="polite">'
                    f'{"किसी चिह्नित तारीख पर टैप करें।" if hi else "Tap a marked date to see the reason."}</p>'
                    '<div class="months">' + "".join(_month(k, m, lang, obs) for m in range(1, 13)) + "</div>")
        if k == "cc":
            body.append("<ul>" + "".join(f"<li>{_esc(h if hi else en)}</li>" for en, h in C.CC_NOTES) + "</ul>")
        body.append("</div>")
    body.append("</div>")
    # full list
    th = ["तारीख", "दिन", "अवकाश", "हाईकोर्ट", "अधीनस्थ न्यायालय"] if hi else ["Date", "Day", "Holiday", "High Court", "Trial courts"]
    body.append(f'<div class="card"><h2>{"अवकाश सूची 2026 — हाईकोर्ट व अधीनस्थ न्यायालय" if hi else "Holiday list 2026 — High Court and trial courts"}</h2>'
                f'<p class="hint">{"रविवार अलग से नहीं दिखाए गए; * = चन्द्र-दर्शन पर निर्भर।" if hi else "Sundays are left out of this table; * = depends on the visibility of the moon."}</p>'
                '<div class="tw"><table class="hl"><tr>' + "".join(f"<th>{x}</th>" for x in th) + "</tr>" + "".join(_holiday_rows(hi)) + "</table></div>"
                + "<p class=\"hint\">" + ("ग्रीष्मकालीन अवकाश: 1–28 जून 2026 (दोनों)। शीतकालीन अवकाश: हाईकोर्ट 24–31 दिसम्बर, अधीनस्थ न्यायालय 25–31 दिसम्बर 2026।" if hi else
                                         "Summer vacation: 1–28 June 2026 (both). Winter holidays: High Court 24–31 December, trial courts 25–31 December 2026.") + "</p></div>")
    body.append(f'<div class="card dif"><h2>{"जहाँ हाईकोर्ट और अधीनस्थ न्यायालय अलग हैं" if hi else "Where the High Court and the trial courts differ"}</h2>'
                f'<div class="tw"><table>{_diffs(hi)}</table></div></div>')
    if obs:
        body.append(f'<div class="card"><h2>{"कैलेंडर के कार्य दिवस जिन पर बैठक नहीं हुई" if hi else "Calendar working days on which the court did not sit"}</h2>'
                    f'<p>{"इन तारीखों पर कैलेंडर के अनुसार कार्य दिवस था, पर जोधपुर पीठ की कोई डेली या सप्लीमेंट्री वाद सूची प्रकाशित नहीं हुई:" if hi else "The calendar shows these as working days, but the Principal Seat at Jodhpur published no Daily and no Supplementary cause list for them:"} '
                    + ", ".join(f"<b>{x.day:02d} {(MON_HI if hi else MON_EN)[x.month - 1]}</b>" for x in obs) + "</p>"
                    f'<p class="hint">{"स्रोत: न्यायालय द्वारा प्रकाशित वाद सूचियाँ। आगे की ऐसी तारीखें पहले से नहीं बताई जा सकतीं — न्यायालय की अधिसूचना देखें।" if hi else "Source: the cause lists published by the court. Such closures cannot be known in advance — watch the court’s notices."}</p></div>')
    body.append(f'<div class="card"><h2>{"ऐच्छिक (प्रतिबंधित) अवकाश 2026 — न्यायालय खुले रहते हैं" if hi else "Restricted holidays 2026 — courts sit"}</h2>'
                f'<p class="hint">{"कर्मचारी इनमें से दो ले सकते हैं; न्यायालय की बैठक होती है।" if hi else "Staff may take two of these; the courts sit on these days."}</p><ul class="ml" style="columns:2">'
                + "".join(f"<li><b>{d.day:02d} {(MON_HI if hi else MON_EN)[d.month - 1][:3]}</b> {_esc(n)}</li>" for d, n in C.RESTRICTED) + "</ul></div>")
    faq = FAQ_HI if hi else FAQ_EN
    body.append(f'<div class="card"><h2>{"सवाल-जवाब" if hi else "Questions"}</h2>' + "".join(f"<h3>{_esc(q)}</h3><p>{_esc(a)}</p>" for q, a in faq)
                + f'<p class="hint">{"स्रोत" if hi else "Source"}: {C.SRC["hc"].split("//")[1]} · '
                  f'{C.SRC["cc"].split("//")[1]}. '
                  + ("न्यायालय किसी भी दिन अतिरिक्त अवकाश घोषित कर सकता है — hcraj.nic.in पर पुष्टि करें।" if hi else
                     "The court may declare additional holidays at any time — always confirm on hcraj.nic.in.") + "</p></div>")
    body.append(f'<script type="application/json" id="caldata">{json.dumps(data, ensure_ascii=False)}</script>{JS}')
    return title, desc, h1, "\n".join(body), faq
