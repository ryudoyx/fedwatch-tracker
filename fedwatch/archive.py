"""把原始数据存成 CSV 文本（放进 git 仓库）。

云端每次运行：CSV → 新建的 SQLite → 抓当天数据 → 写回 CSV → 全部重算 → 出静态网页。
只存原始输入（期货价格、EFFR、FOMC 日程），概率随时可以重算；SQLite 不进仓库，
免得二进制文件每天一个新版本把仓库撑大。
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from .store import Store

PRICES = "zq_prices.csv"
EFFR = "effr.csv"
FOMC = "fomc.csv"


def write(store: Store, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    rows = store.q("SELECT trade_date, contract, close, source FROM prices ORDER BY trade_date, contract")
    _write(folder / PRICES, ["trade_date", "contract", "close", "source"],
           [(r[0], r[1], f"{r[2]:.4f}", r[3]) for r in rows])
    effr = store.q("SELECT date, rate, lower, upper FROM effr ORDER BY date")
    _write(folder / EFFR, ["date", "rate", "lower", "upper"],
           [(r[0], "" if r[1] is None else f"{r[1]:.2f}", f"{r[2]:.2f}", f"{r[3]:.2f}") for r in effr])
    _write(folder / FOMC, ["decision_date"], [(d,) for d in store.fomc_dates()])
    return {"prices": len(rows), "effr": len(effr)}


def load(store: Store, folder: Path) -> dict:
    out = {"prices": 0, "effr": 0, "fomc": 0}
    p = folder / PRICES
    if p.exists():
        by_source: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        for r in _read(p):
            by_source[r.get("source") or "yahoo"][r["contract"]][r["trade_date"]] = float(r["close"])
            out["prices"] += 1
        # 先 yahoo 后 tradingview，保证重复的日子最后留下的是 TradingView 的
        for source in sorted(by_source, key=lambda s: s == "tradingview"):
            store.upsert_prices(by_source[source], source)
    p = folder / EFFR
    if p.exists():
        rows = [{"date": r["date"], "rate": float(r["rate"]) if r["rate"] else None,
                 "lower": float(r["lower"]), "upper": float(r["upper"])} for r in _read(p)]
        out["effr"] = store.upsert_effr(rows)
    p = folder / FOMC
    if p.exists():
        dates = [dt.date.fromisoformat(r["decision_date"]) for r in _read(p)]
        store.set_fomc(dates)
        out["fomc"] = len(dates)
    return out


def _write(path: Path, header: list[str], rows):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
