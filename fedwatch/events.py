"""官方宏观事件日历。美国发布日期与 ZQ 交易日对齐，只作事件前后对照。"""
from __future__ import annotations

import csv
import datetime as dt
import re
from urllib.parse import urlparse

from . import sources
from .store import now_str

FEEDS = {
    "bls": "https://www.bls.gov/schedule/news_release/bls.ics",
    "bea": "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics",
}


def parse_ics(text: str, url: str) -> list[dict]:
    text = re.sub(r"\r?\n[ \t]", "", text)
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        title = re.search(r"(?m)^SUMMARY[^:]*:(.*)", block)
        date = re.search(r"(?m)^DTSTART[^:]*:(\d{8})", block)
        if not title or not date or re.search(r"(?m)^STATUS:CANCELLED", block):
            continue
        title = title[1].strip().replace(r"\,", ",")
        kind = ("CPI" if "Consumer Price Index" in title else
                "NFP" if "Employment Situation" in title else
                "PCE" if "Personal Income and Outlays" in title else None)
        if kind:
            day = dt.datetime.strptime(date[1], "%Y%m%d").date().isoformat()
            out.append({"date": day, "kind": kind,
                        "label": {"CPI": "CPI", "NFP": "非农", "PCE": "PCE"}[kind], "url": url})
    return out


def refresh(store, session) -> list[str]:
    cached = store.get_meta("macro_events", [])
    status = store.get_meta("event_status", {})
    warnings = []
    for name, url in FEEDS.items():
        try:
            response = sources._get(session, url, retries=1, timeout=12)
            parsed = parse_ics(response.text, url)
            if not parsed:
                raise sources.SourceError("日历没有解析出相关事件")
            # 保留已过日期用于研究；对本次日历覆盖的未来日期采用最新安排。
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
            kinds = {"CPI", "NFP"} if name == "bls" else {"PCE"}
            cached = [e for e in cached if e["kind"] not in kinds or e["date"] < today]
            cached += parsed
            status[name] = {"updated_at": now_str(), "error": None}
        except (sources.SourceError, ValueError) as exc:
            warnings.append(f"事件日历 {name}：{exc}（保留已有日历）")
            status[name] = {**status.get(name, {}), "error": str(exc), "attempted_at": now_str()}
    store.set_meta("macro_events", list({(e["date"], e["kind"]): e for e in cached}.values()))
    store.set_meta("event_status", status)
    return warnings


def calendar(store, cfg) -> list[dict]:
    out = list(store.get_meta("macro_events", []))
    out += [{"date": d, "kind": "FOMC", "label": "FOMC", "url": sources.FED_CALENDAR_URL}
            for d in sorted(set(store.fomc_dates()) | set(sources.fomc.BUILTIN_MEETINGS))]
    # 可补讲话或历史发布日，文件本地和云端通用；显式日期，不按月份猜测。
    path = cfg.archive_dir.parent / "events.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                day = dt.date.fromisoformat(row["event_date"]).isoformat()
                url = row.get("url", "")
                if url and urlparse(url).scheme not in ("http", "https"):
                    continue
                out.append({"date": day, "kind": row["kind"], "label": row["label"], "url": url})
    return sorted({(e["date"], e["kind"], e["label"]): e for e in out}.values(),
                  key=lambda e: (e["date"], e["kind"]))
