"""数据源。

- Yahoo Finance：每个 ZQ 合约的日线收盘价（ZQH27.CBT 这种代码），能回溯约 2 年，
  但只能查到还没到期的合约（大约未来 18 个月）。本机用；GitHub 的服务器上普通请求会被 429。
- TradingView 行情筛选接口：一次请求拿到全部 ZQ 合约最近两根完整日线的收盘，
  合约列得更远（约 3 年），没有更早的历史。云端每日任务用它。
- 纽约联储 Markets API：每日 EFFR 和目标区间上下限（官方、免费、无需 key）。
- 美联储官网：FOMC 日程。

CME 自己的结算价接口有反爬，这里不用；也不做任何浏览器指纹伪装。
"""
from __future__ import annotations

import datetime as dt
import re
import time

import requests

from . import fomc

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) fedwatch-tracker"}
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{code}.CBT"
TV_SCAN_URL = "https://scanner.tradingview.com/futures/scan"
NYFED_URL = "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json"
FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# CME 的 ZQ 交易日 16:00 CT 收盘。夏令时是 21:00 UTC、冬令时 22:00 UTC，
# 统一按 22:30 UTC 之后才算这根日线收完，没收完的不落库，免得把盘中价当收盘价。
BAR_COMPLETE_UTC = dt.time(22, 30)


class SourceError(RuntimeError):
    pass


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    return s


def _get(s: requests.Session, url: str, *, params=None, json=None, timeout=20, retries=3,
         sleep=1.5) -> requests.Response:
    last: Exception | None = None
    for i in range(retries):
        try:
            if json is None:
                r = s.get(url, params=params, timeout=timeout)
            else:
                r = s.post(url, json=json, timeout=timeout)
            if r.status_code == 404:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                raise SourceError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.RequestException, SourceError) as exc:
            last = exc
            time.sleep(sleep * (i + 1))
    raise SourceError(f"{url} 请求失败：{last}")


def bar_complete(day: dt.date, now_utc: dt.datetime | None = None) -> bool:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    close = dt.datetime.combine(day, BAR_COMPLETE_UTC, tzinfo=dt.timezone.utc)
    return now_utc >= close


def yahoo_closes(s: requests.Session, code: str, range_: str = "1mo") -> dict[dt.date, float] | None:
    """某个合约的日线收盘价 {交易日: 价格}；合约不存在（已到期/太远）返回 None。"""
    r = _get(s, YAHOO_URL.format(code=code), params={"range": range_, "interval": "1d"})
    if r.status_code == 404:
        return None
    try:
        res = r.json()["chart"]["result"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SourceError(f"{code} 返回格式不对：{exc}") from exc
    ts = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    offset = int(res.get("meta", {}).get("gmtoffset") or 0)
    out: dict[dt.date, float] = {}
    for t, c in zip(ts, closes):
        if c is None:
            continue
        # 日线时间戳是交易所时区的当天零点附近，加上交易所时差再取日期
        day = dt.datetime.fromtimestamp(t + offset, dt.timezone.utc).date()
        if day.weekday() >= 5:
            continue
        out[day] = round(float(c), 4)
    return out


def listed_contracts(today: dt.date, months_ahead: int = 30) -> list[str]:
    m0 = fomc.month_of(today)
    return [fomc.contract_code(fomc.add_months(m0, k)) for k in range(0, months_ahead + 1)]


def fetch_zq(s: requests.Session, today: dt.date, range_: str = "1mo",
             months_ahead: int = 30, pause: float = 0.3, log=print):
    """抓所有在市合约。返回 ({合约: {日期: 价格}}, 失败列表)。连续 3 个 404 就认为到头了。"""
    got: dict[str, dict[dt.date, float]] = {}
    failed: list[str] = []
    misses = 0
    for code in listed_contracts(today, months_ahead):
        try:
            data = yahoo_closes(s, code, range_)
        except SourceError as exc:
            failed.append(f"{code}: {exc}")
            log(f"  [x] {code} {exc}")
            continue
        if data is None:
            misses += 1
            if got and misses >= 3:
                break
            continue
        misses = 0
        got[code] = {d: p for d, p in data.items() if bar_complete(d)}
        time.sleep(pause)
    return got, failed


def _tv_trade_date(bar_open_ts: int) -> dt.date:
    """TradingView 的期货日线从上一个自然日 17:00 CT（Globex 开盘）算起，加 7 小时落到交易日当天。"""
    return (dt.datetime.fromtimestamp(bar_open_ts, dt.timezone.utc) + dt.timedelta(hours=7)).date()


def fetch_zq_tradingview(s: requests.Session, today: dt.date, months_ahead: int = 40,
                         log=print) -> tuple[dict[str, dict[dt.date, float]], list[str]]:
    """一次请求拿全部 ZQ 合约最近两根完整日线的收盘价。返回 ({合约: {交易日: 价格}}, 失败列表)。"""
    m0 = fomc.month_of(today)
    tickers = [f"CBOT:ZQ{fomc.MONTH_CODES[m[1] - 1]}{m[0]}"
               for m in (fomc.add_months(m0, k) for k in range(months_ahead + 1))]
    body = {"symbols": {"tickers": tickers, "query": {"types": []}},
            "columns": ["name", "description", "close", "time", "close[1]", "time[1]", "close[2]", "time[2]"]}
    try:
        r = _get(s, TV_SCAN_URL, json=body, timeout=30)
        rows = r.json().get("data") or []
    except (SourceError, ValueError) as exc:
        log(f"  [x] TradingView：{exc}")
        return {}, [f"TradingView：{exc}"]
    got: dict[str, dict[dt.date, float]] = {}
    for row in rows:
        name, desc, *vals = row.get("d") or [None, None]
        m = re.fullmatch(r"ZQ([FGHJKMNQUVXZ])(\d{4})", name or "")
        if not m or "Federal Funds" not in (desc or ""):
            continue
        code = f"ZQ{m.group(1)}{m.group(2)[2:]}"
        series = {}
        for px, ts in zip(vals[0::2], vals[1::2]):
            if px is None or ts is None:
                continue
            day = _tv_trade_date(int(ts))
            if day.weekday() < 5 and bar_complete(day):
                series[day] = round(float(px), 4)
        if series:
            got[code] = series
    if not got:
        return {}, ["TradingView 没返回任何 ZQ 合约"]
    return got, []


def fetch_effr(s: requests.Session, start: dt.date, end: dt.date) -> list[dict]:
    """[{date, rate, lower, upper}]，纽约联储每个工作日早上 8 点（美东）发布前一天的数。"""
    r = _get(s, NYFED_URL, params={"startDate": start.isoformat(), "endDate": end.isoformat()},
             timeout=30)
    if r.status_code == 404:
        return []
    rows = []
    for x in r.json().get("refRates", []):
        try:
            rows.append({
                "date": x["effectiveDate"],
                "rate": float(x["percentRate"]),
                "lower": float(x["targetRateFrom"]),
                "upper": float(x["targetRateTo"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def fetch_fomc_dates(s: requests.Session) -> list[dt.date]:
    r = _get(s, FED_CALENDAR_URL, timeout=30)
    if r.status_code == 404:
        return []
    return fomc.parse_fed_calendar(r.text)
