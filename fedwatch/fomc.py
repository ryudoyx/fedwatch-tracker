"""FOMC 会议日历和月份工具。

日期一律用"决议公布日"（两天会议的第二天）：新的目标区间从次日生效，
所以在 FedWatch 的算法里，决议日当天仍按旧利率计入当月均值。
"""
from __future__ import annotations

import calendar
import datetime as dt
import re

Month = tuple[int, int]

# 美联储官网 fomccalendars.htm 核对过（2026-09-17）。官网每年年中公布下一年的日程，
# 每天的抓取任务会尝试从官网补新年份，这里只是兜底。
BUILTIN_MEETINGS = [
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
]

MONTH_CODES = "FGHJKMNQUVXZ"


def month_of(d: dt.date) -> Month:
    return (d.year, d.month)


def add_months(m: Month, k: int) -> Month:
    idx = m[0] * 12 + (m[1] - 1) + k
    return (idx // 12, idx % 12 + 1)


def days_in(m: Month) -> int:
    return calendar.monthrange(*m)[1]


def month_str(m: Month) -> str:
    return f"{m[0]:04d}-{m[1]:02d}"


def parse_month(s: str) -> Month:
    y, mo = s.split("-")[:2]
    return (int(y), int(mo))


def contract_code(m: Month) -> str:
    """(2027, 3) -> 'ZQH27'"""
    return f"ZQ{MONTH_CODES[m[1] - 1]}{m[0] % 100:02d}"


def contract_month(code: str) -> Month:
    """'ZQH27' -> (2027, 3)"""
    return (2000 + int(code[3:5]), MONTH_CODES.index(code[2]) + 1)


class Calendar:
    """已知的 FOMC 决议日。只有"整年日程已公布"的年份，没会议的月份才能当锚月用。"""

    def __init__(self, dates):
        self.dates = sorted({d if isinstance(d, dt.date) else dt.date.fromisoformat(d)
                             for d in dates})
        self.years = {d.year for d in self.dates}
        self.months = {month_of(d) for d in self.dates}

    def covers(self, m: Month) -> bool:
        return m[0] in self.years

    def has_meeting(self, m: Month) -> bool:
        return m in self.months

    def upcoming(self, asof: dt.date) -> list[dt.date]:
        return [d for d in self.dates if d > asof]

    def last_decision(self, asof: dt.date) -> dt.date | None:
        past = [d for d in self.dates if d <= asof]
        return past[-1] if past else None


_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})


def parse_fed_calendar(html: str) -> list[dt.date]:
    """解析 federalreserve.gov/monetarypolicy/fomccalendars.htm。

    每年一个面板，行结构是 <div class="fomc-meeting__month"><strong>April/May</strong></div>
    <div class="fomc-meeting__date">30-1*</div>。跨月会议取后一个月、后一天。
    """
    out: list[dt.date] = []
    heads = list(re.finditer(r"(\d{4}) FOMC Meetings", html))
    for i, h in enumerate(heads):
        year = int(h.group(1))
        seg = html[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(html)]
        rows = re.findall(
            r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?'
            r'fomc-meeting__date[^>]*>([^<]+)<', seg, flags=re.S)
        for mon_txt, day_txt in rows:
            low = day_txt.lower()
            if any(w in low for w in ("unscheduled", "notation", "cancel")):
                continue
            mon_name = mon_txt.strip().split("/")[-1].strip().lower()
            days = re.findall(r"\d+", day_txt)
            if mon_name not in _MONTHS or not days:
                continue
            try:
                out.append(dt.date(year, _MONTHS[mon_name], int(days[-1])))
            except ValueError:
                continue
    return sorted(set(out))
