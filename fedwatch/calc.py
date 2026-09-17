"""复现 CME FedWatch 的概率算法。

原文：https://www.cmegroup.com/articles/2023/understanding-the-cme-group-fedwatch-tool-methodology.html

要点：
1. 30 天联邦基金期货（ZQ）价格 → 该月隐含平均 EFFR = 100 - 价格。
2. 没有会议的整月是"锚月"：它的均值 = 上个月的月末利率 = 下个月的月初利率。
   当前月即使没会议也不当锚（月份不完整）。
3. 会议月：均值 = 月初 × N/D + 月末 × M/D（N = 1 号到决议日的天数，M = 剩余天数）。
   知道月初或月末其中一个，就能反解另一个。
4. 传播方向：锚月往前（更早的月份）一路倒推，直到碰到上一个锚月；往后只推一个月。
5. 每次会议的预期变动 = 月末 - 月初，折成 25bp 的步数，整数部分和小数部分拆成
   两个相邻结果的概率；逐次会议卷积，得到每次会议后目标区间的累积分布。
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass, field

from .fomc import Calendar, Month, add_months, days_in, month_of

STEP = 0.25  # 每一步 25bp（单位：%）


@dataclass
class MeetingCalc:
    meeting: dt.date
    start: float          # 会议月月初的隐含 EFFR（%）
    end: float            # 会议月月末的隐含 EFFR（%）
    method: str           # backward / forward / chain，见 _solve_month
    delta_bp: float       # 本次会议的预期变动（bp）
    step_probs: dict[int, float]   # 本次会议单独：步数 → 概率
    dist: dict[int, float] = field(default_factory=dict)  # 会议后累积：相对基准区间的步数 → 概率

    def exp_steps(self) -> float:
        return sum(k * p for k, p in self.dist.items())


@dataclass
class Result:
    asof: dt.date
    base_lower: float              # 基准（当前）目标区间下限，%
    meetings: list[MeetingCalc]
    problems: list[str]

    def lower_of(self, steps: int) -> float:
        return round(self.base_lower + steps * STEP, 4)


def split_steps(delta_bp: float) -> dict[int, float]:
    """预期变动 → 相邻两个步数的概率。-7.5bp → {-1: 0.3, 0: 0.7}"""
    n = delta_bp / 25.0
    k = math.floor(n + 1e-9)
    frac = n - k
    if frac < 1e-9:
        return {k: 1.0}
    return {k: 1.0 - frac, k + 1: frac}


def compute(asof: dt.date, avg: dict[Month, float], cal: Calendar, base_lower: float,
            rule: str = "cme") -> Result:
    """avg: 月份 → 该月 ZQ 隐含平均利率（%）。只用 asof 当天能拿到的价格。

    rule 只为校验时对照用：
      cme      后一个月是锚 → 倒推；否则前一个月是锚 → 正推；否则接下个会议月的月初倒推
      backward 一律倒推（后一个月是锚就用锚，否则接下个会议月的月初）
    """
    cur = month_of(asof)
    upcoming = cal.upcoming(asof)
    by_month = {month_of(d): d for d in upcoming}
    problems: list[str] = []

    def is_anchor(m: Month) -> bool:
        return m > cur and cal.covers(m) and not cal.has_meeting(m) and m in avg

    start: dict[Month, float] = {}
    end: dict[Month, float] = {}
    method: dict[Month, str] = {}

    # 倒序处理：链式倒推要用到下一个会议月已经算好的月初
    for m in sorted(by_month, reverse=True):
        d = by_month[m]
        if m not in avg:
            problems.append(f"{d} 缺 {m[0]}-{m[1]:02d} 合约价格")
            continue
        D = days_in(m)
        N = d.day
        M = D - N
        a = avg[m]
        nxt, prv = add_months(m, 1), add_months(m, -1)

        def backward(e: float, how: str):
            end[m] = e
            start[m] = (a - (M / D) * e) / (N / D)
            method[m] = how

        if is_anchor(nxt):
            backward(avg[nxt], "backward")
        elif rule == "cme" and is_anchor(prv) and M > 0:
            start[m] = avg[prv]
            end[m] = (a - (N / D) * avg[prv]) / (M / D)
            method[m] = "forward"
        elif nxt in start:
            backward(start[nxt], "chain")
        elif is_anchor(prv) and M > 0:
            start[m] = avg[prv]
            end[m] = (a - (N / D) * avg[prv]) / (M / D)
            method[m] = "forward"
        else:
            problems.append(f"{d} 前后都没有可用的锚月")

    meetings: list[MeetingCalc] = []
    dist: dict[int, float] = {0: 1.0}
    floor_steps = math.ceil(-base_lower / STEP - 1e-9)  # 目标区间下限不低于 0
    for d in upcoming:
        m = month_of(d)
        if m not in start:
            break  # 中间断了一次会议，后面的累积分布就没法算了
        delta_bp = (end[m] - start[m]) * 100
        sp = split_steps(delta_bp)
        new: dict[int, float] = defaultdict(float)
        for k0, p0 in dist.items():
            for k1, p1 in sp.items():
                new[max(k0 + k1, floor_steps)] += p0 * p1
        dist = {k: p for k, p in new.items() if p > 1e-12}
        meetings.append(MeetingCalc(d, start[m], end[m], method[m], delta_bp, sp, dict(dist)))
    return Result(asof, base_lower, meetings, problems)


def extreme_distribution(step_probs: list[dict[int, float]], mode: str = "peak") -> dict[int, float]:
    """沿概率树走完这些会议，路径上到达过的最高（peak）/ 最低（trough）步数的分布。

    基准区间（0 步）本身也算到达过：如果市场只定价降息，最高点就是当前区间。
    和 FedWatch 同样假设各次会议互相独立。
    """
    pick = max if mode == "peak" else min
    states: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for sp in step_probs:
        new: dict[tuple[int, int], float] = defaultdict(float)
        for (lvl, ext), p in states.items():
            for k, q in sp.items():
                nl = lvl + k
                new[(nl, pick(ext, nl))] += p * q
        states = {s: p for s, p in new.items() if p > 1e-12}
    out: dict[int, float] = defaultdict(float)
    for (_, ext), p in states.items():
        out[ext] += p
    return dict(out)


def infer_decision_steps(month_avg: float, pre_rate: float, decision: dt.date,
                         next_month_avg: float | None = None) -> int | None:
    """会议刚开完、纽约联储还没公布新区间时，用当月合约反推决议（加/减了几步）。

    会后当月合约几乎已经确定：均值 = 会前 EFFR × N/D + 会后 EFFR × M/D。
    决议日在月末（M=0）时改用下个月合约。
    """
    m = month_of(decision)
    D = days_in(m)
    N = decision.day
    M = D - N
    if M > 0:
        post = (month_avg - pre_rate * N / D) / (M / D)
    elif next_month_avg is not None:
        post = next_month_avg
    else:
        return None
    return round((post - pre_rate) / STEP)
