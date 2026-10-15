"""把原始数据存成 CSV 文本（放进 git 仓库）。

云端每次运行：CSV → 新建的 SQLite → 抓当天数据 → 写回 CSV → 全部重算 → 出静态网页。
只存原始输入（期货价格、EFFR、FOMC 日程），概率随时可以重算；SQLite 不进仓库，
免得二进制文件每天一个新版本把仓库撑大。
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from .store import Store

PRICES = "zq_prices.csv"
QUOTES = "zq_quotes.csv"
EFFR = "effr.csv"
FOMC = "fomc.csv"


def write(store: Store, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    rows = store.q("SELECT trade_date, contract, close, source, updated_at FROM prices ORDER BY trade_date, contract")
    _write(folder / PRICES, ["trade_date", "contract", "close", "source", "updated_at"],
           [(r[0], r[1], f"{r[2]:.4f}", r[3], r[4]) for r in rows])
    _write(folder / QUOTES, ["trade_date", "contract", "source", "close", "fetched_at"],
           [tuple(r) for r in store.q("SELECT * FROM price_quotes ORDER BY trade_date,contract,source")])
    _write(folder / "events.csv", ["event_date", "kind", "label", "url"],
           [(e["date"], e["kind"], e["label"], e["url"]) for e in store.get_meta("macro_events", [])])
    research_path = folder / "research.json"
    research_tmp = research_path.with_suffix(".tmp")
    research_tmp.write_text(json.dumps(store.get_meta("research_data", {}), ensure_ascii=False,
                                       sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    research_tmp.replace(research_path)
    effr = store.q("SELECT date, rate, lower, upper FROM effr ORDER BY date")
    _write(folder / EFFR, ["date", "rate", "lower", "upper"],
           [(r[0], "" if r[1] is None else f"{r[1]:.2f}", f"{r[2]:.2f}", f"{r[3]:.2f}") for r in effr])
    _write(folder / FOMC, ["decision_date"], [(d,) for d in store.fomc_dates()])
    return {"prices": len(rows), "effr": len(effr)}


def load(store: Store, folder: Path) -> dict:
    out = {"prices": 0, "effr": 0, "fomc": 0}
    research_path = folder / "research.json"
    if research_path.exists():
        store.set_meta("research_data", json.loads(research_path.read_text(encoding="utf-8")))
    p = folder / PRICES
    if p.exists():
        by_source: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        archived_prices = _read(p)
        for r in archived_prices:
            by_source[r.get("source") or "yahoo"][r["contract"]][r["trade_date"]] = float(r["close"])
            out["prices"] += 1
        # 先 yahoo 后 tradingview，保证重复的日子最后留下的是 TradingView 的
        for source in sorted(by_source, key=lambda s: s == "tradingview"):
            store.upsert_prices(by_source[source], source)
        with store.lock, store.conn:
            store.conn.executemany("UPDATE prices SET updated_at=? WHERE trade_date=? AND contract=?",
                                   [(r.get("updated_at") or "未知（旧版存档）", r["trade_date"], r["contract"])
                                    for r in archived_prices])
            store.conn.executemany("UPDATE price_quotes SET fetched_at=? WHERE trade_date=? AND contract=? AND source=?",
                                   [(r.get("updated_at") or "未知（旧版存档）", r["trade_date"], r["contract"], r.get("source") or "yahoo")
                                    for r in archived_prices])
    p = folder / QUOTES
    if p.exists():
        with store.lock, store.conn:
            store.conn.executemany("INSERT OR REPLACE INTO price_quotes VALUES (?,?,?,?,?)",
                                   [(r["trade_date"], r["contract"], r["source"], float(r["close"]), r["fetched_at"])
                                    for r in _read(p)])
    p = folder / "events.csv"
    if p.exists():
        store.set_meta("macro_events", [{"date": r["event_date"], "kind": r["kind"],
                                        "label": r["label"], "url": r["url"]} for r in _read(p)])
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
