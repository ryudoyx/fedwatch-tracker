"""SQLite 存储。

source 字段区分两类概率：
  calc  自己用 ZQ 收盘价按 CME 方法算的（每天自动）
  cme   从 CME 官网 FedWatch「Historical → Downloads」导入的官方历史
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

from . import fomc

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    trade_date TEXT NOT NULL,
    contract   TEXT NOT NULL,
    month      TEXT NOT NULL,
    close      REAL NOT NULL,
    updated_at TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'yahoo',   -- yahoo / tradingview
    PRIMARY KEY (trade_date, contract)
);
CREATE INDEX IF NOT EXISTS prices_by_contract ON prices (contract, trade_date);
CREATE TABLE IF NOT EXISTS effr (
    date  TEXT PRIMARY KEY,
    rate  REAL,
    lower REAL NOT NULL,
    upper REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fomc (
    decision_date TEXT PRIMARY KEY
);
-- 每个观察日一行：基准区间（当时的现行目标区间）从哪来
CREATE TABLE IF NOT EXISTS runs (
    source      TEXT NOT NULL,
    asof        TEXT NOT NULL,
    base_lower  REAL NOT NULL,
    base_source TEXT NOT NULL,
    problems    TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (source, asof)
);
CREATE TABLE IF NOT EXISTS meeting_calc (
    source   TEXT NOT NULL,
    asof     TEXT NOT NULL,
    meeting  TEXT NOT NULL,
    start    REAL,
    "end"    REAL,
    method   TEXT,
    delta_bp REAL,
    exp_lower REAL NOT NULL,   -- 会后目标区间下限的期望值（%）
    PRIMARY KEY (source, asof, meeting)
);
CREATE TABLE IF NOT EXISTS probs (
    source   TEXT NOT NULL,
    asof     TEXT NOT NULL,
    meeting  TEXT NOT NULL,
    lower_bp INTEGER NOT NULL,  -- 目标区间下限，bp。375 = 3.75%-4.00%
    prob     REAL NOT NULL,     -- 0~1
    PRIMARY KEY (source, asof, meeting, lower_bp)
);
-- 导入过的官方文件（失败的也记下，文件没改动就不再重试、不再每天报错）
CREATE TABLE IF NOT EXISTS imports (
    file        TEXT PRIMARY KEY,
    size        INTEGER,
    mtime       REAL,
    meeting     TEXT,
    rows        INTEGER,
    imported_at TEXT,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


BEIJING = dt.timezone(dt.timedelta(hours=8))


def now_str() -> str:
    """一律按北京时间记：云端服务器是 UTC，页面上显示的更新时间要和本机一致。"""
    return dt.datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(imports)")}
        if "error" not in cols:
            self.conn.execute("ALTER TABLE imports ADD COLUMN error TEXT")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(prices)")}
        if "source" not in cols:
            self.conn.execute("ALTER TABLE prices ADD COLUMN source TEXT NOT NULL DEFAULT 'yahoo'")

    def close(self):
        with self.lock:
            self.conn.close()

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    # ---------- 原始数据 ----------
    def upsert_prices(self, data: dict[str, dict[dt.date, float]], source: str = "yahoo") -> int:
        """同一天同一合约两个来源都有时，TradingView（日线收盘按结算价）优先，Yahoo 不覆盖它。"""
        ts = now_str()
        rows = [(d.isoformat() if isinstance(d, dt.date) else d, code,
                 fomc.month_str(fomc.contract_month(code)), px, ts, source)
                for code, series in data.items() for d, px in series.items()]
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT INTO prices (trade_date, contract, month, close, updated_at, source) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(trade_date, contract) DO UPDATE SET "
                "close=excluded.close, updated_at=excluded.updated_at, source=excluded.source "
                "WHERE (prices.close != excluded.close OR prices.source != excluded.source) "
                "AND NOT (prices.source = 'tradingview' AND excluded.source != 'tradingview')", rows)
        return len(rows)

    def upsert_effr(self, rows: list[dict]) -> int:
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO effr VALUES (:date, :rate, :lower, :upper)", rows)
        return len(rows)

    def set_fomc(self, dates: list[dt.date]):
        with self.lock, self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO fomc VALUES (?)",
                                  [(d.isoformat(),) for d in dates])

    def fomc_dates(self) -> list[str]:
        return [r[0] for r in self.q("SELECT decision_date FROM fomc ORDER BY 1")]

    def trade_dates(self, since: str | None = None) -> list[dt.date]:
        rows = self.q("SELECT DISTINCT trade_date FROM prices WHERE trade_date >= ? ORDER BY 1",
                      (since or "0000",))
        return [dt.date.fromisoformat(r[0]) for r in rows]

    def avg_rates(self, asof: dt.date, stale_days: int = 7) -> dict[fomc.Month, float]:
        """asof 当天各合约隐含利率；个别冷门合约当天没成交就用 7 天内最近一次收盘。"""
        lo, hi = (asof - dt.timedelta(days=stale_days)).isoformat(), asof.isoformat()
        rows = self.q(
            "SELECT month, close FROM prices p WHERE p.trade_date BETWEEN ? AND ? "
            "AND p.trade_date = (SELECT MAX(q.trade_date) FROM prices q "
            "  WHERE q.contract = p.contract AND q.trade_date BETWEEN ? AND ?)", (lo, hi, lo, hi))
        return {fomc.parse_month(r["month"]): round(100.0 - r["close"], 6) for r in rows}

    def effr_on_or_before(self, day: dt.date) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM effr WHERE date <= ? ORDER BY date DESC LIMIT 1",
                      (day.isoformat(),))
        return rows[0] if rows else None

    def effr_after(self, day: dt.date) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM effr WHERE date > ? ORDER BY date LIMIT 1", (day.isoformat(),))
        return rows[0] if rows else None

    # ---------- 结果 ----------
    def save_result(self, source: str, asof: dt.date, base_lower: float, base_source: str,
                    meetings: list[dict], problems: list[str]):
        """meetings: [{meeting, start, end, method, delta_bp, exp_lower, dist: {lower_bp: prob}}]"""
        a = asof.isoformat()
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM probs WHERE source=? AND asof=?", (source, a))
            self.conn.execute("DELETE FROM meeting_calc WHERE source=? AND asof=?", (source, a))
            if not meetings:
                self.conn.execute("DELETE FROM runs WHERE source=? AND asof=?", (source, a))
                return
            self.conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                (source, a, base_lower, base_source,
                 json.dumps(problems, ensure_ascii=False) if problems else None, now_str()))
            self.conn.executemany(
                "INSERT INTO meeting_calc VALUES (?,?,?,?,?,?,?,?)",
                [(source, a, m["meeting"], m.get("start"), m.get("end"), m.get("method"),
                  m.get("delta_bp"), m["exp_lower"]) for m in meetings])
            self.conn.executemany(
                "INSERT INTO probs VALUES (?,?,?,?,?)",
                [(source, a, m["meeting"], int(k), float(p))
                 for m in meetings for k, p in m["dist"].items() if p >= 5e-5])

    def get_meta(self, key: str, default=None):
        rows = self.q("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def set_meta(self, key: str, value):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                              (key, json.dumps(value, ensure_ascii=False)))
