"""给网页用的查询：单次会议的概率演变、某天的全表、利率路径、加息/降息终点。"""
from __future__ import annotations

import bisect
import datetime as dt
from collections import defaultdict

from . import calc, fomc
from .store import Store

SOURCES = ("calc", "cme")


def _dates(store: Store, source: str) -> list[str]:
    return [r[0] for r in store.q("SELECT asof FROM runs WHERE source=? ORDER BY asof", (source,))]


def nearest_on_or_before(dates: list[str], target: str) -> str | None:
    i = bisect.bisect_right(dates, target)
    return dates[i - 1] if i else None


def state(store: Store, cfg) -> dict:
    out = {"sources": {}, "cme_dir": str(cfg.cme_download_dir)}
    for s in SOURCES:
        d = _dates(store, s)
        out["sources"][s] = {"first": d[0] if d else None, "last": d[-1] if d else None, "n": len(d)}
    last = out["sources"]["calc"]["last"]
    if last:
        r = store.q("SELECT base_lower, base_source, problems FROM runs WHERE source='calc' AND asof=?",
                    (last,))[0]
        out["base"] = {"lower": r["base_lower"], "source": r["base_source"]}
        out["latest_asof"] = last
    px = store.q("SELECT MAX(trade_date), COUNT(DISTINCT contract) FROM prices")[0]
    out["prices"] = {"last": px[0], "contracts": px[1]}
    effr = store.q("SELECT * FROM effr ORDER BY date DESC LIMIT 1")
    if effr:
        out["effr"] = dict(effr[0])
    out["meetings"] = meetings_list(store)
    out["fomc"] = sorted(set(fomc.BUILTIN_MEETINGS) | set(store.fomc_dates()))
    out["last_update"] = store.get_meta("last_update")
    out["imports"] = [dict(r) for r in store.q("SELECT * FROM imports ORDER BY imported_at DESC LIMIT 20")]
    return out


def meetings_list(store: Store) -> list[dict]:
    rows = store.q(
        "SELECT meeting, source, MIN(asof) AS first, MAX(asof) AS last, COUNT(*) AS n "
        "FROM meeting_calc GROUP BY meeting, source ORDER BY meeting")
    by = defaultdict(dict)
    for r in rows:
        by[r["meeting"]][r["source"]] = {"first": r["first"], "last": r["last"], "n": r["n"]}
    return [{"meeting": m, **v} for m, v in by.items()]


def meeting_series(store: Store, source: str, meeting: str) -> dict:
    """某次会议：每个观察日各目标区间的概率。"""
    rows = store.q(
        "SELECT p.asof, p.lower_bp, p.prob FROM probs p WHERE p.source=? AND p.meeting=? "
        "ORDER BY p.asof", (source, meeting))
    calc_rows = store.q(
        "SELECT m.asof, m.exp_lower, m.delta_bp, r.base_lower, r.base_source FROM meeting_calc m "
        "JOIN runs r ON r.source=m.source AND r.asof=m.asof "
        "WHERE m.source=? AND m.meeting=? ORDER BY m.asof", (source, meeting))
    dates = [r["asof"] for r in calc_rows]
    idx = {d: i for i, d in enumerate(dates)}
    ranges = sorted({r["lower_bp"] for r in rows})
    probs = {bp: [0.0] * len(dates) for bp in ranges}
    for r in rows:
        if r["asof"] in idx:
            probs[r["lower_bp"]][idx[r["asof"]]] = r["prob"]
    hike, hold, cut = [], [], []
    for i, r in enumerate(calc_rows):
        base_bp = round(r["base_lower"] * 100)
        h = c = o = 0.0
        for bp in ranges:
            p = probs[bp][i]
            if bp > base_bp:
                h += p
            elif bp < base_bp:
                c += p
            else:
                o += p
        hike.append(h)
        hold.append(o)
        cut.append(c)
    return {
        "meeting": meeting, "source": source, "dates": dates, "ranges": ranges,
        "probs": {str(k): v for k, v in probs.items()},
        "exp_lower": [r["exp_lower"] for r in calc_rows],
        "delta_bp": [r["delta_bp"] for r in calc_rows],
        "base_lower": [r["base_lower"] for r in calc_rows],
        "p_hike": hike, "p_hold": hold, "p_cut": cut,
    }


def snapshot(store: Store, source: str, asof: str | None = None) -> dict:
    dates = _dates(store, source)
    if not dates:
        return {"asof": None, "meetings": []}
    asof = nearest_on_or_before(dates, asof) if asof else dates[-1]
    asof = asof or dates[0]

    def one(day: str | None):
        if not day:
            return None
        run = store.q("SELECT * FROM runs WHERE source=? AND asof=?", (source, day))[0]
        ms = store.q("SELECT * FROM meeting_calc WHERE source=? AND asof=? ORDER BY meeting",
                     (source, day))
        ps = store.q("SELECT meeting, lower_bp, prob FROM probs WHERE source=? AND asof=?",
                     (source, day))
        dist = defaultdict(dict)
        for p in ps:
            dist[p["meeting"]][str(p["lower_bp"])] = p["prob"]
        return {
            "asof": day, "base_lower": run["base_lower"], "base_source": run["base_source"],
            "meetings": [{"meeting": m["meeting"], "exp_lower": m["exp_lower"],
                          "delta_bp": m["delta_bp"], "method": m["method"],
                          "start": m["start"], "end": m["end"], "dist": dist[m["meeting"]]}
                         for m in ms],
        }

    d0 = dt.date.fromisoformat(asof)
    i = dates.index(asof)
    compare = {"1d": one(dates[i - 1]) if i else None}
    for key, days in (("1w", 7), ("1m", 30), ("3m", 91)):
        compare[key] = one(nearest_on_or_before(dates, (d0 - dt.timedelta(days=days)).isoformat()))
    out = one(asof)
    out["compare"] = compare
    out["dates"] = dates
    return out


def terminal(store: Store, source: str, horizon: str | None = None, mode: str = "peak") -> dict:
    """每个观察日：到 horizon 那次会议为止，路径上最高（peak）/最低（trough）目标区间的分布。"""
    runs = store.q("SELECT asof, base_lower FROM runs WHERE source=? ORDER BY asof", (source,))
    mc = store.q("SELECT asof, meeting, exp_lower, delta_bp FROM meeting_calc WHERE source=? "
                 "ORDER BY asof, meeting", (source,))
    fomc_all = sorted({r["meeting"] for r in mc})
    by_asof = defaultdict(list)
    for r in mc:
        by_asof[r["asof"]].append(r)
    if not horizon:
        last = runs[-1]["asof"] if runs else None
        horizon = by_asof[last][-1]["meeting"] if last and by_asof[last] else None
    dates, bases, dists, exp_ext, path_ext, path_meeting = [], [], [], [], [], []
    for run in runs:
        a = run["asof"]
        rows = [r for r in by_asof[a] if r["meeting"] <= (horizon or "")]
        if not rows or rows[-1]["meeting"] != horizon:
            continue
        expected = [m for m in fomc_all if a < m <= horizon]
        if [r["meeting"] for r in rows] != expected:
            continue  # 中间缺会议（官方导入没下全），这天不算
        base = run["base_lower"]
        prev = base
        steps = []
        for r in rows:
            d = r["delta_bp"] if r["delta_bp"] is not None else (r["exp_lower"] - prev) * 100
            prev = r["exp_lower"]
            steps.append(calc.split_steps(d))
        ext = calc.extreme_distribution(steps, mode)
        dist = {int(round((base + k * calc.STEP) * 100)): p for k, p in ext.items()}
        pick = max if mode == "peak" else min
        best = pick(rows, key=lambda r: r["exp_lower"])
        path_val = pick(best["exp_lower"], base)
        dates.append(a)
        bases.append(base)
        dists.append(dist)
        exp_ext.append(sum(bp / 100 * p for bp, p in dist.items()))
        path_ext.append(path_val)
        path_meeting.append(best["meeting"] if path_val != base else None)
    ranges = sorted({bp for d in dists for bp, p in d.items() if p >= 0.0005})
    return {
        "source": source, "mode": mode, "horizon": horizon, "dates": dates, "ranges": ranges,
        "probs": {str(bp): [d.get(bp, 0.0) for d in dists] for bp in ranges},
        "base_lower": bases, "exp_extreme": exp_ext, "path_extreme": path_ext,
        "path_extreme_meeting": path_meeting,
        "horizons": sorted({r["meeting"] for r in (by_asof[runs[-1]["asof"]] if runs else [])}),
    }
