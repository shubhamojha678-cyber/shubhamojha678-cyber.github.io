"""courtcal.py - the Rajasthan High Court's OWN 2026 calendars, transcribed by eye on 22-Nov-2026 from
  HC  https://hcraj.nic.in/hcraj/img/HC_Cal_2026_revised.pdf   (Rajasthan High Court, both benches)
  CC  https://hcraj.nic.in/hcraj/img/CC_Cal_2026.pdf           (Civil Courts calendar = subordinate / trial courts)
Checked against the lists the court actually published (Jodhpur, 2026): 199 of 203 held court days agree;
the 4 others (21-Feb, 11-Mar, 29-Jul, 11-Sep) were extra closures with no list at all (court's quick list
confirms 0 lists) - those are kept separately as OBSERVED, never mixed into the official calendar.
kind: holiday | saturday | vacation | nonworking (court + office closed) | office_only (court does not sit, office works)
"""
import datetime as dt

D = dt.date
SRC = {"hc": "https://hcraj.nic.in/hcraj/img/HC_Cal_2026_revised.pdf", "cc": "https://hcraj.nic.in/hcraj/img/CC_Cal_2026.pdf"}
YEARS = {2026}

# (date, kind, english, hindi, moon, applies_to)  applies_to: "both" | "hc" | "cc"
_OO = ("office_only", "Court not sitting (office working)", "न्यायालय की बैठक नहीं (कार्यालय खुला)")
_NW = ("nonworking", "Non-working day (Court and Office)", "अकार्य दिवस (न्यायालय व कार्यालय)")
_S2 = ("saturday", "Second Saturday", "द्वितीय शनिवार")
_S4 = ("saturday", "Fourth Saturday", "चतुर्थ शनिवार")


def _h(d, en, hi, moon=False, to="both"):
    return (d, "holiday", en, hi, moon, to)


def _k(d, k, to):
    return (d, k[0], k[1], k[2], False, to)


DAYS = [
    _h(D(2026, 1, 1), "New Year's Day", "नव वर्ष"),
    _k(D(2026, 1, 2), _NW, "hc"), _k(D(2026, 1, 3), _OO, "hc"),
    _k(D(2026, 1, 10), _S2, "both"), _k(D(2026, 1, 17), _OO, "hc"), _k(D(2026, 1, 24), _S4, "cc"),
    _h(D(2026, 1, 26), "Republic Day", "गणतंत्र दिवस"),
    _k(D(2026, 2, 14), _S2, "both"), _h(D(2026, 2, 15), "Maha Shivratri", "महाशिवरात्रि"), _k(D(2026, 2, 28), _S4, "both"),
    _h(D(2026, 3, 2), "Holi", "होली"), _h(D(2026, 3, 3), "Holi", "होली (धुलंडी)"),
    _k(D(2026, 3, 14), _S2, "both"), _h(D(2026, 3, 20), "Chetichand", "चेटीचंड"),
    _h(D(2026, 3, 21), "Idul-Fitr", "ईदुल फ़ितर", True), _h(D(2026, 3, 26), "Ram Navmi", "राम नवमी"),
    _h(D(2026, 3, 31), "Mahaveer Jayanti", "महावीर जयंती"),
    _h(D(2026, 4, 3), "Good Friday", "गुड फ्राइडे"), _k(D(2026, 4, 4), _OO, "hc"),
    _k(D(2026, 4, 11), _S2, "both"), _h(D(2026, 4, 14), "Ambedkar Jayanti", "डॉ. अम्बेडकर जयंती"),
    _k(D(2026, 5, 9), _S2, "both"), _k(D(2026, 5, 23), _S4, "both"),
    _h(D(2026, 5, 28), "Idul-Azha", "ईदुल अज़हा", True), _k(D(2026, 5, 29), _NW, "hc"), _k(D(2026, 5, 30), _OO, "hc"),
    _k(D(2026, 6, 13), _S2, "hc"), _h(D(2026, 6, 17), "Pratap Jayanti", "महाराणा प्रताप जयंती"),
    _h(D(2026, 6, 26), "Moharram", "मोहर्रम", True), _k(D(2026, 6, 27), _S4, "both"),
    _k(D(2026, 7, 11), _S2, "both"), _k(D(2026, 7, 25), _S4, "both"),
    _k(D(2026, 8, 8), _S2, "both"), _h(D(2026, 8, 9), "World Tribal Day", "विश्व आदिवासी दिवस"),
    _h(D(2026, 8, 15), "Independence Day", "स्वतंत्रता दिवस"), _k(D(2026, 8, 22), _S4, "cc"),
    _h(D(2026, 8, 26), "Barawafat (Id-Miladun Nabi)", "बारावफ़ात (ईद-मिलादुन्नबी)", True),
    _k(D(2026, 8, 27), _NW, "hc"), _h(D(2026, 8, 28), "Raksha Bandhan", "रक्षाबंधन"), _k(D(2026, 8, 29), _OO, "hc"),
    _h(D(2026, 9, 4), "Janmashtami", "जन्माष्टमी"), _k(D(2026, 9, 12), _S2, "both"), _k(D(2026, 9, 19), _OO, "hc"),
    _h(D(2026, 9, 21), "Baba Ramdev Jayanti", "बाबा रामदेव जयंती"), _k(D(2026, 9, 26), _S4, "cc"),
    _h(D(2026, 10, 2), "Birthday of Mahatma Gandhi", "गांधी जयंती"), _k(D(2026, 10, 10), _S2, "both"),
    _h(D(2026, 10, 11), "Sthapna Navratra", "नवरात्र स्थापना"), _k(D(2026, 10, 17), _OO, "hc"),
] + [_h(D(2026, 10, x), "Dashera Holidays", "दशहरा अवकाश") for x in range(19, 24)] + [
    _k(D(2026, 10, 24), _S4, "both"),
] + [_h(D(2026, 11, x), "Deepawali Holidays", "दीपावली अवकाश") for x in range(7, 14)] + [
    _k(D(2026, 11, 14), _S2, "both"), _h(D(2026, 11, 24), "Guru Nanak Deo Jayanti", "गुरु नानक देव जयंती"),
    _k(D(2026, 12, 12), _S2, "both"), _h(D(2026, 12, 25), "Christmas Day", "क्रिसमस"), _k(D(2026, 12, 26), _S4, "both"),
]
VACATIONS = {
    "hc": [(D(2026, 6, 1), D(2026, 6, 28), "Summer Vacation", "ग्रीष्मकालीन अवकाश"),
           (D(2026, 12, 24), D(2026, 12, 31), "Winter Holidays", "शीतकालीन अवकाश")],
    "cc": [(D(2026, 6, 1), D(2026, 6, 28), "Summer Vacation", "ग्रीष्मकालीन अवकाश"),
           (D(2026, 12, 25), D(2026, 12, 31), "Winter Holidays", "शीतकालीन अवकाश")],
}
# CC note 6: these Saturdays are WORKING days of the subordinate courts (boxed in the calendar)
CC_WORKING_SATURDAYS = {D(2026, 3, 28), D(2026, 4, 25), D(2026, 6, 13), D(2026, 11, 28)}
CC_NOTES = [
    ("In Kota District the day following Janmashtami (5 September) is observed as the holiday instead of Janmashtami itself.",
     "कोटा ज़िले में जन्माष्टमी के स्थान पर उसके अगले दिन (5 सितम्बर) अवकाश रहता है।"),
    ("Summer Vacation 1–28 June and Winter Holidays 25–31 December are subject to general or specific orders of the High Court.",
     "ग्रीष्मकालीन अवकाश (1–28 जून) व शीतकालीन अवकाश (25–31 दिसम्बर) उच्च न्यायालय के सामान्य या विशेष आदेशों के अधीन हैं।"),
    ("Saturday 28 March, 25 April, 13 June and 28 November are working days of the subordinate courts.",
     "शनिवार 28 मार्च, 25 अप्रैल, 13 जून और 28 नवम्बर अधीनस्थ न्यायालयों के कार्य दिवस हैं।"),
]
RESTRICTED = [  # restricted holidays: staff may take 2; courts SIT
    (D(2026, 1, 25), "Devnarayan Jayanti"), (D(2026, 1, 31), "Vishwakarma Jayanti"), (D(2026, 1, 31), "Swami Ramcharan Jayanti"),
    (D(2026, 2, 1), "Guru Ravidas Jayanti"), (D(2026, 2, 3), "Shab-E-Barat *"), (D(2026, 2, 12), "Maharshi Dayanand Saraswati Jayanti"),
    (D(2026, 2, 23), "Gadge Maharaj Jayanti"), (D(2026, 3, 20), "Jumatul Vida *"), (D(2026, 4, 4), "Easter Saturday"),
    (D(2026, 4, 11), "Mahatma Jyotiba Phule Jayanti"), (D(2026, 4, 14), "Vaishakhi"), (D(2026, 4, 14), "Sain Jayanti"),
    (D(2026, 4, 19), "Parshuram Jayanti"), (D(2026, 4, 20), "Akha Teej"), (D(2026, 5, 1), "Budh Poornima"),
    (D(2026, 7, 29), "Guru Poornima"), (D(2026, 9, 3), "Thadri"), (D(2026, 9, 14), "Ganesh Chaturthi"),
    (D(2026, 9, 16), "Samvatsari"), (D(2026, 9, 25), "Anant Chaturdashi"), (D(2026, 10, 29), "Karwa Chouth"),
]


def closed(court: str, d: dt.date):
    """None when the court sits on d by its published calendar; else (kind, english, hindi, moon).
    court = 'hc' | 'cc'. Only for YEARS; raises KeyError otherwise (callers decide what to do)."""
    if d.year not in YEARS:
        raise KeyError(d.year)
    if d.weekday() == 6:
        return ("sunday", "Sunday", "रविवार", False)
    for x in DAYS:
        if x[0] == d and x[5] in ("both", court):
            if court == "cc" and d in CC_WORKING_SATURDAYS:
                break
            return x[1:5]
    for a, b, en, hi in VACATIONS[court]:
        if a <= d <= b and not (court == "cc" and d in CC_WORKING_SATURDAYS):
            return ("vacation", en, hi, False)
    return None


def sits(court: str, d: dt.date) -> bool:
    return closed(court, d) is None
