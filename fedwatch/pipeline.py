"""抓数 → 算概率 → 导入官方文件，每日任务和网页上的「抓最新数据」都走这里。"""
from __future__ import annotations

import datetime as dt
import json
import traceback

from . import calc, cme_import, fomc, sources
from .config import Config
from .store import Store, now_str


class Tracker:
    def __init__(self, cfg: Config, store: Store, log=print):
        self.cfg = cfg
        self.store = store
        self.log = log

    # ---------- 日历 ----------
    def calendar(self) -> fomc.Calendar:
        return fomc.Calendar(set(fomc.BUILTIN_MEETINGS) | set(self.store.fomc_dates()))

    def refresh_calendar(self, s) -> str | None:
        try:
            dates = sources.fetch_fomc_dates(s)
        except sources.SourceError as exc:
            return f"FOMC 日程：{exc}（先用内置日程）"
        if len(dates) < 16:
            return f"FOMC 日程：官网页面只解析出 {len(dates)} 个日期，格式可能变了（先用内置日程）"
        self.store.set_fomc(dates)
        return None

    # ---------- 基准区间 ----------
    def base_range(self, asof: dt.date, avg: dict, cal: fomc.Calendar) -> tuple[float | None, str]:
        """asof 收盘时的现行目标区间下限。

        纽约联储的数据晚一天：会议当天和第二天还查不到新区间，这时先看 config 里的
        target_override，没有就用当月期货反推这次会议加/减了几步。
        """
        row = self.store.effr_on_or_before(asof)
        if row is None:
            return None, "缺 EFFR 数据"
        last = cal.last_decision(asof)
        if last is None or row["date"] > last.isoformat():
            return row["lower"], "nyfed"
        after = self.store.effr_after(last)
        if after is not None:
            return after["lower"], "nyfed"
        latest = cal.last_decision(dt.date.today())
        if self.cfg.target_override and last == latest:
            return self.cfg.target_override[0], "config"

        m = fomc.month_of(last)
        if m not in avg:
            return row["lower"], "nyfed(会议结果未确认)"
        pre_avg = self._effr_month_avg(m, last)
        steps = calc.infer_decision_steps(avg[m], pre_avg, last, avg.get(fomc.add_months(m, 1)))
        if steps is None:
            return row["lower"], "nyfed(会议结果未确认)"
        return round(row["lower"] + steps * calc.STEP, 4), f"期货反推({steps:+d}步)"

    def _effr_month_avg(self, m: fomc.Month, through: dt.date) -> float:
        """当月 1 号到决议日（含）每个自然日的 EFFR 均值，周末沿用前一个工作日。"""
        vals = []
        day = dt.date(m[0], m[1], 1)
        while day <= through:
            r = self.store.effr_on_or_before(day)
            if r is not None and r["rate"] is not None:
                vals.append(r["rate"])
            day += dt.timedelta(days=1)
        return sum(vals) / len(vals) if vals else self.store.effr_on_or_before(through)["rate"]

    # ---------- 计算 ----------
    def compute_dates(self, dates: list[dt.date]) -> int:
        cal = self.calendar()
        n = 0
        for asof in dates:
            avg = self.store.avg_rates(asof)
            if not avg:
                continue
            base, base_src = self.base_range(asof, avg, cal)
            if base is None:
                self.store.save_result("calc", asof, 0, base_src, [], [base_src])
                continue
            res = calc.compute(asof, avg, cal, base)
            meetings = []
            for mc in res.meetings:
                dist = {int(round(res.lower_of(k) * 100)): p for k, p in mc.dist.items()}
                meetings.append({
                    "meeting": mc.meeting.isoformat(),
                    "start": round(mc.start, 6), "end": round(mc.end, 6),
                    "method": mc.method, "delta_bp": round(mc.delta_bp, 4),
                    "exp_lower": round(base + mc.exp_steps() * calc.STEP, 6),
                    "dist": dist,
                })
            self.store.save_result("calc", asof, base, base_src, meetings, res.problems)
            n += bool(meetings)
        return n

    # ---------- 官方文件导入 ----------
    def import_cme(self) -> dict:
        folder = self.cfg.cme_download_dir
        done = {r["file"]: (r["size"], r["mtime"]) for r in self.store.q("SELECT * FROM imports")}
        files = cme_import.pending_files(folder, done)
        cal = self.calendar()
        fomc_dates = set(cal.dates)
        summary = {"files": 0, "rows": 0, "errors": []}
        for path in files:
            st = path.stat()
            try:
                tables = cme_import.parse_file(path, fomc_dates)
            except Exception as exc:  # 文件格式千奇百怪，一个坏文件不能拖垮整个任务
                summary["errors"].append(f"{path.name}: {exc}")
                self.log(f"  [x] 导入 {path.name} 失败：{exc}")
                with self.store.lock, self.store.conn:
                    self.store.conn.execute(
                        "INSERT OR REPLACE INTO imports VALUES (?,?,?,NULL,0,?,?)",
                        (path.name, st.st_size, round(st.st_mtime, 3), now_str(), str(exc)))
                continue
            rows = 0
            for meeting, series in tables:
                for asof, dist in series.items():
                    avg = self.store.avg_rates(asof)
                    base, base_src = self.base_range(asof, avg, cal)
                    if base is None:
                        continue
                    exp_lower = sum(bp / 100 * p for bp, p in dist.items()) / (sum(dist.values()) or 1)
                    self._save_cme_meeting(asof, meeting, base, base_src, dist, exp_lower)
                    rows += 1
            with self.store.lock, self.store.conn:
                self.store.conn.execute(
                    "INSERT OR REPLACE INTO imports VALUES (?,?,?,?,?,?,NULL)",
                    (path.name, st.st_size, round(st.st_mtime, 3),
                     ",".join(m.isoformat() for m, _ in tables), rows, now_str()))
            summary["files"] += 1
            summary["rows"] += rows
            self.log(f"  导入 {path.name}：{', '.join(m.isoformat() for m, _ in tables)} 共 {rows} 行")
        return summary

    def _save_cme_meeting(self, asof, meeting, base, base_src, dist, exp_lower):
        a, m = asof.isoformat(), meeting.isoformat()
        st = self.store
        with st.lock, st.conn:
            st.conn.execute(
                "INSERT INTO runs VALUES ('cme',?,?,?,NULL,?) ON CONFLICT(source, asof) DO UPDATE "
                "SET base_lower=excluded.base_lower, base_source=excluded.base_source, "
                "computed_at=excluded.computed_at", (a, base, base_src, now_str()))
            st.conn.execute("DELETE FROM probs WHERE source='cme' AND asof=? AND meeting=?", (a, m))
            st.conn.execute(
                "INSERT OR REPLACE INTO meeting_calc VALUES ('cme',?,?,NULL,NULL,NULL,NULL,?)",
                (a, m, round(exp_lower, 6)))
            st.conn.executemany("INSERT INTO probs VALUES ('cme',?,?,?,?)",
                                [(a, m, bp, p) for bp, p in dist.items() if p >= 5e-5])

    # ---------- 一次完整更新 ----------
    def update(self, bootstrap: bool = False, price_source: str | None = None,
               recompute_all: bool = False) -> dict:
        """price_source: yahoo（本机，能回溯）/ tradingview（云端，只有最近两天）"""
        price_source = price_source or self.cfg.price_source
        started = now_str()
        errors: list[str] = []      # 会让每日任务弹通知的
        warnings: list[str] = []    # 只记日志的（比如官网日程没抓到，有内置日程兜底）
        s = sources.session()
        today = dt.date.today()
        self.log(f"[{started}] {'初始化回算' if bootstrap else '每日更新'}开始")

        err = self.refresh_calendar(s)
        if err:
            warnings.append(err)
            self.log("  [!] " + err)
        cal = self.calendar()
        if not cal.upcoming(today + dt.timedelta(days=270)):
            warnings.append("内置和官网日程都只覆盖到 9 个月内，远期会议算不出来；请检查美联储官网是否已公布新一年日程")

        effr_rows = self.store.q("SELECT MAX(date) FROM effr")[0][0]
        start = (dt.date.fromisoformat(effr_rows) - dt.timedelta(days=14)) if effr_rows and not bootstrap \
            else today - dt.timedelta(days=800)
        try:
            n = self.store.upsert_effr(sources.fetch_effr(s, start, today))
            self.log(f"  EFFR/目标区间：{n} 行（纽约联储）")
        except sources.SourceError as exc:
            errors.append(f"EFFR：{exc}")
            self.log(f"  [x] EFFR：{exc}")

        if price_source == "tradingview":
            got, failed = sources.fetch_zq_tradingview(s, today, log=self.log)
            label = "TradingView"
        else:
            rng = self.cfg.bootstrap_range if bootstrap else self.cfg.daily_range
            got, failed = sources.fetch_zq(s, today, rng, self.cfg.months_ahead,
                                           pause=float(self.cfg.request.get("pause", 0.3)), log=self.log)
            label = "Yahoo"
        errors += failed
        n = self.store.upsert_prices(got, price_source)
        span = sorted(got, key=fomc.contract_month)
        days = sorted({d for series in got.values() for d in series})
        self.log(f"  ZQ 期货：{len(got)} 个合约 {span[0] if span else ''}~{span[-1] if span else ''}，"
                 f"{n} 行，交易日 {days[0] if days else ''}~{days[-1] if days else ''}（{label}）")
        if not got and not failed:
            errors.append("ZQ 期货一个合约都没抓到")

        if bootstrap or recompute_all:
            dates = self.store.trade_dates()
        else:
            since = (today - dt.timedelta(days=100)).isoformat()
            dates = self.store.trade_dates(since)
        n = self.compute_dates(dates)
        self.log(f"  计算：{n} 个观察日有结果")

        imp = self.import_cme()
        errors += imp["errors"]

        latest = self.store.q("SELECT MAX(asof) FROM runs WHERE source='calc'")[0][0]
        if latest and (today - dt.date.fromisoformat(latest)).days > 6:
            errors.append(f"最新观察日停在 {latest}，已经超过 6 天没有新结果")
        result = {"started": started, "finished": now_str(), "ok": not errors, "errors": errors,
                  "warnings": warnings, "latest_asof": latest, "imported_files": imp["files"]}
        self.store.set_meta("last_update", result)
        self.log(f"[{result['finished']}] 完成，最新观察日 {latest}" +
                 (f"，{len(errors)} 个问题" if errors else ""))
        return result


def safe_update(tracker: Tracker, bootstrap: bool = False, **kw) -> dict:
    try:
        return tracker.update(bootstrap, **kw)
    except Exception as exc:
        traceback.print_exc()
        result = {"finished": now_str(), "ok": False, "errors": [f"程序出错：{exc}"]}
        try:
            tracker.store.set_meta("last_update", result)
        except Exception:
            pass
        return result


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
