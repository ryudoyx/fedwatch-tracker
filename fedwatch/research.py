"""政策账本、官方 SEP 与宏观月度序列。所有估计保留期限与原始来源。"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import statistics
from html.parser import HTMLParser
from urllib.parse import urljoin

from . import analysis, fomc, sources
from .store import BEIJING, now_str

SERIES = {
    "CPIAUCNS": {"owner": "BLS", "label": "CPI（未季调指数）"},
    "CPIAUCSL": {"owner": "BLS", "label": "CPI（季调指数）"},
    "PCEPILFE": {"owner": "BEA", "label": "核心 PCE（季调指数）"},
    "PAYEMS": {"owner": "BLS", "label": "非农就业人数（季调，千人）"},
    "UNRATE": {"owner": "BLS", "label": "失业率（季调，%）"},
}


class Tables(HTMLParser):
    """只读表格文本；不依赖页面样式或图像识别。"""
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tables, self.rows, self.cells, self.parts = [], None, None, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.rows = []
        elif tag == "tr" and self.rows is not None:
            self.cells = []
        elif tag in ("td", "th") and self.cells is not None:
            self.parts = []

    def handle_data(self, text):
        if self.parts is not None:
            self.parts.append(text)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.parts is not None:
            self.cells.append(" ".join("".join(self.parts).split()))
            self.parts = None
        elif tag == "tr" and self.cells is not None:
            self.rows.append(self.cells)
            self.cells = None
        elif tag == "table" and self.rows is not None:
            self.tables.append(self.rows)
            self.rows = None


def parse_sep(html: str, release_date: str, url: str) -> dict:
    for table in Tables(html).tables:
        if not table or not table[0] or not re.search(r"midpoint of target range", table[0][0], re.I):
            continue
        labels = table[0][1:]
        if not labels or not all(re.fullmatch(r"20\d{2}|Longer run", x, re.I) for x in labels):
            continue
        by_year = {year: [] for year in labels}
        for row in table[1:]:
            if len(row) != len(labels) + 1:
                raise ValueError("SEP 点位表列数变化")
            point = float(row[0])
            if not math.isfinite(point) or not 0 <= point <= 30:
                raise ValueError("SEP 点位超出合理范围")
            for year, raw in zip(labels, row[1:]):
                count = int(raw) if raw else 0  # 官方空白单元格表示该档无人。
                if not 0 <= count <= 30:
                    raise ValueError("SEP 人数格式不符")
                if count:
                    by_year[year].append({"rate": point, "count": count})
        years = []
        for year, dots in by_year.items():
            expanded = [d["rate"] for d in dots for _ in range(d["count"])]
            if not 5 <= len(expanded) <= 30:
                raise ValueError(f"SEP {year} 人数不足或异常")
            years.append({"year": year, "dots": dots, "participants": len(expanded),
                          "median": statistics.median(expanded), "min": min(expanded), "max": max(expanded)})
        return {"release_date": release_date, "url": url, "years": years, "fetched_at": now_str()}
    raise ValueError("没有找到可核验的 SEP 点位人数表")


def parse_series(text: str, series: str, today: dt.date) -> list[dict]:
    result = {}
    for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        raw_date = row.get("observation_date", row.get("DATE"))
        if raw_date is None or series not in row:
            raise ValueError("FRED CSV 列名不符")
        date = dt.date.fromisoformat(raw_date)
        if date.day != 1 or date >= today.replace(day=1):
            continue  # 月度指标只展示已结束的月份。
        raw = row[series]
        if raw in ("", "."):
            continue
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError("FRED 序列出现无效值")
        if raw_date in result:
            raise ValueError("FRED 序列出现重复月份")
        result[raw_date] = value
    if len(result) < 14:
        raise ValueError("宏观序列不足 14 个月，不能可靠计算同比与前值")
    return [{"date": k, "value": v} for k, v in sorted(result.items())]


def refresh(store, session) -> list[str]:
    today = dt.datetime.now(BEIJING).date()
    data = store.get_meta("research_data", {})
    macro, status = data.setdefault("macro", {}), data.setdefault("status", {})
    revisions = data.setdefault("revisions", [])
    warnings = []
    for series, spec in SERIES.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={today.year-4}-01-01"
        try:
            response = sources._get(session, url, retries=1, timeout=15)
            observations = parse_series(response.content.decode("utf-8-sig"), series, today)
            old = {r["date"]: r["value"] for r in macro.get(series, {}).get("observations", [])}
            if old and observations[-1]["date"] < max(old):
                raise ValueError("返回的数据期早于已保存版本，不覆盖已有序列")
            captured = now_str()
            for point in observations:
                before = old.get(point["date"])
                if before is not None and abs(before - point["value"]) > 1e-9:
                    revisions.append({"series": series, "period": point["date"], "before": before,
                                      "after": point["value"], "captured_at": captured})
            macro[series] = {**spec, "series": series, "observations": observations,
                             "url": f"https://fred.stlouisfed.org/series/{series}",
                             "fetched_at": captured, "first_captured_at": macro.get(series, {}).get("first_captured_at", captured)}
            status[series] = {"updated_at": captured, "error": None}
        except (sources.SourceError, UnicodeError, ValueError, KeyError) as exc:
            status[series] = {**status.get(series, {}), "error": str(exc), "attempted_at": now_str()}
            warnings.append(f"宏观 {series}：{exc}（保留缓存）")
    try:
        response = sources._get(session, sources.FED_CALENDAR_URL, retries=1, timeout=15)
        html = response.content.decode("utf-8-sig")
        candidates = sorted(set(re.findall(r"/monetarypolicy/fomcprojtabl(\d{8})\.htm", html)), reverse=True)
        candidates = [d for d in candidates if d <= today.strftime("%Y%m%d")]
        if not candidates:
            raise ValueError("FOMC 日历没有可用的 SEP 链接")
        # 一并保留上一版，支持比较点阵图的变化；失败时不退回旧版冒充最新。
        snapshots = {s["release_date"]: s for s in data.get("sep", [])}
        for date in candidates[:2]:
            release = dt.datetime.strptime(date, "%Y%m%d").date().isoformat()
            url = urljoin(sources.FED_CALENDAR_URL, f"/monetarypolicy/fomcprojtabl{date}.htm")
            response = sources._get(session, url, retries=1, timeout=15)
            snapshots[release] = parse_sep(response.content.decode("utf-8-sig"), release, url)
            data["sep"] = sorted(snapshots.values(), key=lambda s: s["release_date"])
        status["sep"] = {"updated_at": now_str(), "error": None, "latest_release": candidates[0]}
    except (sources.SourceError, UnicodeError, ValueError, KeyError) as exc:
        status["sep"] = {**status.get("sep", {}), "error": str(exc), "attempted_at": now_str()}
        warnings.append(f"SEP：{exc}（保留已有官方版本）")
    data["revisions"] = revisions[-1000:]
    store.set_meta("research_data", data)
    return warnings


def year_end_meeting(store, year: int) -> str | None:
    dates = sorted(d for d in set(fomc.BUILTIN_MEETINGS) | set(store.fomc_dates()) if d.startswith(str(year)))
    return dates[-1] if len(dates) >= 6 and dates[-1][5:7] == "12" else None


def policy_book(store, source: str) -> dict:
    snap = analysis.snapshot(store, source)
    if not snap["asof"]:
        return {"asof": None, "years": [], "dates": [], "history": []}
    current_year = int(snap["asof"][:4])
    latest = {m["meeting"]: m for m in snap["meetings"]}
    current_end = year_end_meeting(store, current_year)
    if current_end and current_end <= snap["asof"]:
        latest[current_end] = {"meeting": current_end, "exp_lower": snap["base_lower"],
                               "dist": {str(round(snap["base_lower"]*100)): 1.0}}
    years = []
    for year in sorted({int(m[:4]) for m in latest}):
        meeting = year_end_meeting(store, year)
        ending = latest.get(meeting)
        if not ending:
            continue
        end_mid = ending["exp_lower"] + 0.125
        opening = store.effr_on_or_before(dt.date(year, 1, 1)) if year == current_year else None
        if opening and (dt.date(year, 1, 1)-dt.date.fromisoformat(opening["date"])).days>7:
            opening = None
        prior_end = latest.get(year_end_meeting(store, year - 1))
        start_mid = (opening["lower"] + 0.125 if opening else
                     prior_end["exp_lower"] + 0.125 if prior_end else None)
        current_mid = snap["base_lower"] + 0.125
        changes = {}
        for key in ("1d", "1w", "1m"):
            prev = snap["compare"][key]
            old = next((m for m in prev["meetings"] if m["meeting"] == meeting), None) if prev else None
            if old is None and prev and meeting <= prev["asof"]:
                old = {"exp_lower": prev["base_lower"]}
            changes[key] = {"asof": prev["asof"] if prev else None,
                            "bp": round((ending["exp_lower"]-old["exp_lower"])*100, 4) if old else None}
        years.append({"year": year, "meeting": meeting, "midpoint": end_mid,
                      "current_midpoint": current_mid, "start_midpoint": start_mid,
                      "start_basis": "年初官方区间" if opening else "上年末市场定价" if prior_end else "缺失",
                      "realized_bp": round((current_mid-start_mid)*100, 4) if opening else None,
                      "remaining_bp": round((end_mid-current_mid)*100, 4),
                      "net_steps": (end_mid-current_mid)*4,
                      "calendar_year_bp": round((end_mid-start_mid)*100, 4) if start_mid is not None else None,
                      "changes": changes, "dist": ending["dist"]})
    rows = store.q("SELECT asof,meeting,exp_lower FROM meeting_calc WHERE source=? ORDER BY asof", (source,))
    by = {(r["asof"],r["meeting"]): r["exp_lower"]+0.125 for r in rows}
    bases = {r["asof"]: r["base_lower"]+0.125 for r in
             store.q("SELECT asof,base_lower FROM runs WHERE source=?", (source,))}
    for row in years:
        for date in snap["dates"]:
            if date >= row["meeting"]:
                by[(date,row["meeting"])] = bases[date]
    history = [{"year": r["year"], "meeting": r["meeting"],
                "values": [by.get((d, r["meeting"])) for d in snap["dates"]]} for r in years]
    return {"asof": snap["asof"], "base_source": snap["base_source"], "years": years,
            "dates": snap["dates"], "history": history}


def sep_comparison(store, source: str) -> dict:
    book = policy_book(store, source)
    raw = store.get_meta("research_data", {})
    today = dt.datetime.now(BEIJING).date().isoformat()
    versions = [s for s in raw.get("sep", []) if s["release_date"] <= today]
    if not versions:
        return {"asof": book["asof"], "release_date": None, "years": [], "status": raw.get("status", {}).get("sep")}
    sep = versions[-1]
    previous = {y["year"]: y for y in versions[-2]["years"]} if len(versions) > 1 else {}
    years = []
    market = {str(y["year"]): y for y in book["years"]}
    for point in sep["years"]:
        m = market.get(point["year"])
        aligned = bool(book["asof"] and book["asof"] >= sep["release_date"])
        comparable = m if aligned else None
        old = previous.get(point["year"])
        years.append({**point, "market_midpoint": comparable["midpoint"] if comparable else None,
                      "meeting": comparable["meeting"] if comparable else None,
                      "gap_bp": (comparable["midpoint"]-point["median"])*100 if comparable else None,
                      "market_dist": comparable["dist"] if comparable else {},
                      "previous_median": old["median"] if old else None})
    return {"asof": book["asof"], "release_date": sep["release_date"], "url": sep["url"],
            "fetched_at": sep["fetched_at"], "previous_release": versions[-2]["release_date"] if len(versions)>1 else None,
            "status": raw.get("status", {}).get("sep"), "years": years}


def macro_scorecard(store) -> dict:
    raw = store.get_meta("research_data", {})
    macro = raw.get("macro", {})
    metrics = []
    specs = [("CPIAUCNS", "CPI", "%", "yoy", "未季调同比"),
             ("PCEPILFE", "核心 PCE", "%", "yoy", "季调指数同比"),
             ("PAYEMS", "新增非农", "千人", "diff", "季调就业人数月增量"),
             ("UNRATE", "失业率", "%", "level", "季调失业率")]
    for series, label, unit, mode, definition in specs:
        source = macro.get(series, {})
        values = {r["date"]: r["value"] for r in source.get("observations", [])}
        trend = []
        dates = []
        if values:
            month, last = fomc.parse_month(min(values)), fomc.parse_month(max(values))
            while month <= last:
                dates.append(fomc.month_str(month)+"-01")
                month = fomc.add_months(month, 1)
        for date in dates:
            value = values.get(date)
            month = fomc.parse_month(date)
            prior = values.get(fomc.month_str(fomc.add_months(month, -1)) + "-01")
            year_ago = values.get(fomc.month_str(fomc.add_months(month, -12)) + "-01")
            result = None if value is None else (
                ((value/year_ago-1)*100 if year_ago else None) if mode == "yoy" else (
                    value-prior if prior is not None else None) if mode == "diff" else value)
            trend.append({"date": date, "value": result})
        latest = trend[-1] if trend else {"date": None, "value": None}
        prior_date = (fomc.month_str(fomc.add_months(fomc.parse_month(latest["date"]), -1))+"-01") if latest["date"] else None
        before = next((r for r in trend if r["date"]==prior_date), {"date": prior_date, "value": None})
        secondary, secondary_label = None, None
        if latest["date"]:
            if series in ("CPIAUCNS", "PCEPILFE"):
                monthly = macro.get("CPIAUCSL", {}) if series == "CPIAUCNS" else source
                mv = {r["date"]: r["value"] for r in monthly.get("observations", [])}
                p = fomc.month_str(fomc.add_months(fomc.parse_month(latest["date"]), -1))+"-01"
                if latest["date"] in mv and mv.get(p):
                    secondary = (mv[latest["date"]]/mv[p]-1)*100
                if series=="CPIAUCNS" and raw.get("status", {}).get("CPIAUCSL", {}).get("error"):
                    secondary = None
                secondary_label = "季调环比（%）"
            elif series == "PAYEMS":
                wanted = {fomc.month_str(fomc.add_months(fomc.parse_month(latest["date"]), -k))+"-01" for k in range(3)}
                last3 = [r for r in trend if r["date"] in wanted]
                if len(last3)==3 and all(x["value"] is not None for x in last3):
                    secondary = sum(x["value"] for x in last3)/3
                secondary_label = "近三个月平均新增（千人）"
            else:
                secondary = latest["value"]-before["value"] if before["value"] is not None else None
                secondary_label = "较前月（百分点）"
        metrics.append({"series": series, "label": label, "unit": unit, "definition": definition,
                        "period": latest["date"], "value": latest["value"],
                        "previous_period": before["date"], "previous": before["value"],
                        "secondary": secondary, "secondary_label": secondary_label,
                        "trend": trend[-36:], "source": {k:v for k,v in source.items() if k!="observations"},
                        "secondary_source": macro.get("CPIAUCSL", {}).get("url") if series=="CPIAUCNS" else None,
                        "stale": bool(latest["date"] and (dt.datetime.now(BEIJING).date()-dt.date.fromisoformat(latest["date"])).days>100),
                        "status": raw.get("status", {}).get(series)})
    return {"metrics": metrics, "revisions": list(reversed(raw.get("revisions", [])))[:100],
            "series": {s:{k:v for k,v in d.items() if k!="observations"} for s,d in macro.items()},
            "status": raw.get("status", {})}
