"""HC Rajasthan (Jodhpur) sitting calendar — 2026.

Sources of non-sitting days:
  * Sundays
  * 2nd and 4th Saturdays of each month
  * Summer vacation: 1–28 June 2026
  * Winter vacation:  24–31 December 2026
  * Gazetted holidays (manual list — refresh annually from hcraj.nic.in)

The lists below are authoritative for the 2026 calendar year and match
memory/hc_rajasthan_calendar.md.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional


HOLIDAYS_2026: dict[dt.date, str] = {
    # Public/court holidays observed by Rajasthan HC.
    # Refresh annually from the HC calendar PDF.
    dt.date(2026, 1, 2):   "New Year holiday observance",
    dt.date(2026, 1, 14):  "Makar Sankranti",
    dt.date(2026, 1, 26):  "Republic Day",
    dt.date(2026, 3, 4):   "Holi",
    dt.date(2026, 3, 21):  "Eid-ul-Fitr (tentative)",
    dt.date(2026, 4, 14):  "Dr. Ambedkar Jayanti",
    dt.date(2026, 4, 18):  "Mahavir Jayanti",
    dt.date(2026, 5, 1):   "May Day",
    dt.date(2026, 5, 29):  "Court holiday (per HC calendar)",
    dt.date(2026, 7, 9):   "World Tribal Day",
    dt.date(2026, 8, 15):  "Independence Day",
    dt.date(2026, 8, 26):  "Barawafat / Id-e-Milad-un-Nabi (tentative)",
    dt.date(2026, 8, 27):  "Court holiday (per HC calendar)",
    dt.date(2026, 8, 28):  "Raksha Bandhan",
    # 03-Sep-2026: synced with the HC 2026 calendar as held in
    # hc-sentinel/sentinel.py COURT_CLOSED_2026 — this table lacked every
    # closure after 27-Aug, so on Janmashtami (04-Sep) the hourly run would
    # have probed the portal all day and reported "nothing published"
    # instead of "court not sitting".
    dt.date(2026, 9, 4):   "Janmashtami",
    dt.date(2026, 9, 21):  "Baba Ramdev Jayanti",
    dt.date(2026, 10, 2):  "Gandhi Jayanti",
    dt.date(2026, 10, 19): "Dashehra / Navratri holidays",
    dt.date(2026, 10, 20): "Dashehra / Navratri holidays",
    dt.date(2026, 10, 21): "Dashehra / Navratri holidays",
    dt.date(2026, 10, 22): "Dashehra / Navratri holidays",
    dt.date(2026, 10, 23): "Dashehra / Navratri holidays",
    dt.date(2026, 11, 7):  "Deepawali holidays",
    dt.date(2026, 11, 9):  "Deepawali holidays (Diwali, tentative)",
    dt.date(2026, 11, 10): "Deepawali holidays",
    dt.date(2026, 11, 11): "Deepawali holidays",
    dt.date(2026, 11, 12): "Deepawali holidays",
    dt.date(2026, 11, 13): "Deepawali holidays",
    dt.date(2026, 11, 24): "Guru Nanak Dev Jayanti",
}

SUMMER_VAC_2026 = (dt.date(2026, 6, 1),  dt.date(2026, 6, 28))
WINTER_VAC_2026 = (dt.date(2026, 12, 24), dt.date(2026, 12, 31))


def _nth_weekday_of_month(d: dt.date) -> int:
    """1-indexed nth occurrence of d's weekday in d's month."""
    return (d.day - 1) // 7 + 1


def is_sitting_day(d: dt.date) -> tuple[bool, Optional[str]]:
    """Return (True, None) if court is sitting on date d,
    else (False, reason)."""
    if d.weekday() == 6:
        return False, "Sunday"
    if d.weekday() == 5 and _nth_weekday_of_month(d) in (2, 4):
        return False, f"{['2nd','4th'][_nth_weekday_of_month(d)==4]} Saturday"
    if SUMMER_VAC_2026[0] <= d <= SUMMER_VAC_2026[1]:
        return False, "Summer vacation"
    if WINTER_VAC_2026[0] <= d <= WINTER_VAC_2026[1]:
        return False, "Winter vacation"
    if d in HOLIDAYS_2026:
        return False, HOLIDAYS_2026[d]
    return True, None


# How far ahead the cause-list pipeline looks for the next sitting day — used
# by run.py (supplementary sweep, case watch, evening digest) and by
# precourt-chase.sh, so the two can never disagree. 4 days covers every
# ordinary gap (Fri -> Mon over a 2nd/4th Saturday, Sat -> Tue over a Monday
# holiday, Thu -> Mon over a Friday holiday + off Saturday); across a long
# closure (vacation, Dashehra, Deepawali) the reopening day is picked up 4 days
# ahead instead of being probed every hour for two weeks.
NEXT_SITTING_HORIZON_DAYS = 4


def next_sitting_day(after: dt.date, limit: int = 14) -> Optional[dt.date]:
    """First date strictly after `after` that is_sitting_day() calls a sitting
    day, looking at most `limit` days ahead; None if there is none that close.

    Unlike hc-sentinel's next_sitting_day this knows the 2nd/4th Saturdays, so
    from Friday 11-Sep-2026 it answers Monday 14-Sep, not Saturday 12-Sep.
    It never decides what gets PROBED — a vacation, tentative-holiday or
    Saturday date is still fetched by the caller when it is tomorrow."""
    for i in range(1, limit + 1):
        cand = after + dt.timedelta(days=i)
        if is_sitting_day(cand)[0]:
            return cand
    return None


def day_name(d: dt.date) -> str:
    return d.strftime("%A")
