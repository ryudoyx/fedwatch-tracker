"""每天早上的摘要：和上一个观察日比，挑出值得看的变化。

同一份内容三个地方用：Windows 通知（要短，气泡最多两百多字）、logs/digest_*.txt、
看板顶部的「今日摘要」卡片。
"""
from __future__ import annotations

import datetime as dt

from . import analysis
from .store import Store

STEP = 0.25


def _md(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(m)}/{int(d)}"


def _ym(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{y[2:]}年{int(m)}月"


def _range(bp: int) -> str:
    return f"{bp / 100:.2f}–{(bp + 25) / 100:.2f}%"


def _pp(now: float, before: float | None, digits: int = 1) -> str:
    """把变化写成（+3.2pp）这种；没有可比的就空着。"""
    if before is None:
        return ""
    d = (now - before) * 100
    if abs(d) < 0.5 * 10 ** -digits:   # 四舍五入后是 0 就别写 +0pp
        return "（持平）"
    return f"（{d:+.{digits}f}pp）"


def _split(dist: dict, base_bp: int) -> tuple[float, float, float]:
    hike = sum(p for bp, p in dist.items() if int(bp) > base_bp)
    cut = sum(p for bp, p in dist.items() if int(bp) < base_bp)
    return hike, 1 - hike - cut, cut


def _most_likely(dist: dict) -> tuple[int, float]:
    bp, p = max(dist.items(), key=lambda kv: kv[1])
    return int(bp), p


def focus_meetings(cfg, meetings: list[str]) -> list[str]:
    """下次会议永远在；再加上配置里指定的重点会议，没配就取半年后左右那次。"""
    if not meetings:
        return []
    # YAML 里不加引号的日期会被解析成 date 对象，统一转成字符串
    want = [str(m) for m in (cfg.digest.get("focus_meetings") or []) if str(m) in meetings]
    if not want and len(meetings) > 1:
        target = dt.date.fromisoformat(meetings[0]) + dt.timedelta(days=180)
        want = [min(meetings[1:], key=lambda m: abs((dt.date.fromisoformat(m) - target).days))]
    return meetings[:1] + [m for m in want if m != meetings[0]]


def build(store: Store, cfg) -> dict | None:
    dates = [r[0] for r in store.q("SELECT asof FROM runs WHERE source='calc' ORDER BY asof")]
    if not dates:
        return None
    cur = dates[-1]
    snap = analysis.snapshot(store, "calc", cur)
    prev = snap["compare"]["1d"]
    base_bp = round(snap["base_lower"] * 100)
    meetings = [m["meeting"] for m in snap["meetings"]]
    prev_dist = {m["meeting"]: m["dist"] for m in prev["meetings"]} if prev else {}
    prev_base_bp = round(prev["base_lower"] * 100) if prev else None

    lines: list[str] = []
    note = ""
    src = str(snap.get("base_source") or "")
    if not src.startswith("nyfed"):
        last = max((d for d in store.fomc_dates() if d <= cur), default=None)
        note = f"（{_md(last)} 决议按期货反推）" if last else f"（{src}）"
    elif prev_base_bp is not None and prev_base_bp != base_bp:
        note = f"（较前日 {(base_bp - prev_base_bp) / 100:+.2f}%）"
    lines.append(f"现行 {_range(base_bp)}{note}")

    for mt in focus_meetings(cfg, meetings):
        m = next(x for x in snap["meetings"] if x["meeting"] == mt)
        pd = prev_dist.get(mt)
        bp, p = _most_likely(m["dist"])
        if mt == meetings[0]:
            hike, hold, cut = _split(m["dist"], base_bp)
            ph, _, pc = _split(pd, base_bp) if pd else (None, None, None)
            main = f"加息 {hike:.0%}{_pp(hike, ph, 0)}" if hike >= cut else f"降息 {cut:.0%}{_pp(cut, pc, 0)}"
            lines.append(f"{_md(mt)} 会议：{main}，不变 {hold:.0%}")
        else:
            lines.append(f"{_ym(mt)}：最可能 {_range(bp)} {p:.0%}"
                         f"{_pp(p, pd.get(str(bp)) if pd else None, 0)}")

    term = analysis.terminal(store, "calc", None, "peak")
    if term["dates"] and term["dates"][-1] == cur:
        i = len(term["dates"]) - 1
        exp = term["exp_extreme"][i]
        d_exp = exp - term["exp_extreme"][i - 1] if i > 0 else None
        bp, p = _most_likely({bp: arr[i] for bp, arr in term["probs"].items()})
        chg = f"（{(d_exp * 100):+.0f}bp）" if d_exp is not None and abs(d_exp) >= 0.005 else ""
        lines.append(f"加息终点（到{_ym(term['horizon'])}）：期望 {exp:.2f}%{chg}，最可能 {_range(bp)} {p:.0%}")

    movers = []
    if prev:
        for m in snap["meetings"]:
            pd = prev_dist.get(m["meeting"])
            if not pd:
                continue
            for bp in set(m["dist"]) | set(pd):
                d = (m["dist"].get(bp, 0) - pd.get(bp, 0)) * 100
                if abs(d) >= float(cfg.digest.get("move_threshold", 3.0)):
                    movers.append((abs(d), d, m["meeting"], int(bp)))
    movers.sort(reverse=True)
    if movers:
        _, d, mt, bp = movers[0]
        lines.append(f"最大变动：{_ym(mt)} {_range(bp)} {d:+.1f}pp")

    title = f"FedWatch {_md(cur)}"
    head = lines[1] if len(lines) > 1 else lines[0]
    title = f"{title}：{head.replace('：', ' ', 1)}"

    full = [f"FedWatch 摘要 · 观察日 {cur}" + (f"（上一个 {prev['asof']}）" if prev else ""), ""]
    full += lines + ["", "各次会议（概率%，括号内为较前日变化 pp）："]
    bps = sorted({int(bp) for m in snap["meetings"] for bp, p in m["dist"].items() if p >= 0.005})
    full.append("会议".ljust(12) + "".join(f"{bp / 100:>8.2f}" for bp in bps))
    for m in snap["meetings"]:
        pd = prev_dist.get(m["meeting"]) or {}
        row = f"{m['meeting']:<12}"
        for bp in bps:
            p = m["dist"].get(str(bp), 0)
            row += f"{p * 100:>8.1f}" if p >= 0.005 else " " * 8
        full.append(row)
        drow = " " * 12
        for bp in bps:
            d = (m["dist"].get(str(bp), 0) - pd.get(str(bp), 0)) * 100
            drow += f"{d:>+8.1f}" if abs(d) >= 0.05 else " " * 8
        if drow.strip():
            full.append(drow)
    return {
        "asof": cur, "prev": prev["asof"] if prev else None,
        "title": title[:120], "lines": lines, "text": "\n".join(lines),
        "full_text": "\n".join(full),
        "movers": [{"meeting": mt, "lower_bp": bp, "change_pp": round(d, 2)} for _, d, mt, bp in movers[:8]],
    }
