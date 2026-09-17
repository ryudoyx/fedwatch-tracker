"""命令行入口：python -m fedwatch <命令>"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import analysis, archive, site
from .config import Config
from .pipeline import Tracker, safe_update
from .store import Store


def _table(snap: dict, meeting: str | None):
    base = snap["base_lower"]
    ms = [m for m in snap["meetings"] if not meeting or m["meeting"] == meeting]
    if not ms:
        print("没有这次会议的数据")
        return
    bps = sorted({int(k) for m in ms for k, p in m["dist"].items() if p >= 0.0005})
    head = f"{'会议':<12}{'预期变动':>8}" + "".join(f"{bp/100:>7.2f}" for bp in bps)
    print(f"观察日 {snap['asof']}  现行区间 {base:.2f}-{base + 0.25:.2f}%（{snap['base_source']}）")
    print("列 = 目标区间下限（%），数值 = 概率（%）")
    print(head)
    for m in ms:
        d = m["delta_bp"]
        cells = "".join(
            f"{m['dist'].get(str(bp), 0) * 100:>7.1f}" if m["dist"].get(str(bp), 0) >= 0.0005 else f"{'':>7}"
            for bp in bps)
        print(f"{m['meeting']:<12}{(f'{d:+.1f}bp' if d is not None else ''):>10}{cells}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fedwatch", description="FedWatch 概率跟踪")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="首次运行：抓 Yahoo 能给的全部历史并回算")
    sub.add_parser("daily", help="每日任务：抓最新数据、计算、导入官方文件")
    sub.add_parser("recompute", help="用库里已有价格全部重算（改了算法或日程后用）")
    sub.add_parser("import", help="导入 data/cme_downloads 里新放进去的 CME 官方下载文件")
    sp = sub.add_parser("serve", help="打开本地看板")
    sp.add_argument("--port", type=int)
    sp.add_argument("--no-browser", action="store_true")
    sh = sub.add_parser("show", help="终端里看某天的概率表")
    sh.add_argument("--meeting", help="只看某次会议，如 2027-03-17")
    sh.add_argument("--asof", help="观察日，默认最新")
    sh.add_argument("--source", default="calc", choices=analysis.SOURCES)
    cl = sub.add_parser("cloud", help="云端每日任务：读 CSV 存档 → 抓 TradingView/纽约联储 → 写回存档 → 重算 → 出静态网页")
    cl.add_argument("--site", help="静态网页输出目录，如 _site")
    ex = sub.add_parser("export-site", help="把当前数据库导出成静态网页")
    ex.add_argument("out")
    sub.add_parser("export-archive", help="把数据库里的原始数据写成 data/archive/*.csv")
    args = ap.parse_args(argv)

    cfg = Config.load()
    if args.cmd == "serve":
        from .server import run

        run(cfg, port=args.port, open_browser=not args.no_browser)
        return 0

    store = Store(cfg.db_path)
    try:
        tracker = Tracker(cfg, store)
        if args.cmd in ("bootstrap", "daily"):
            res = safe_update(tracker, bootstrap=args.cmd == "bootstrap")
            for e in res.get("errors", []):
                print("  [x]", e)
            if res.get("ok"):
                return 0
            # 1 = 程序崩溃；2 = 跑完了但有部分数据没抓到
            return 1 if any(e.startswith("程序出错") for e in res.get("errors", [])) else 2
        if args.cmd == "cloud":
            got = archive.load(store, cfg.archive_dir)
            print(f"读入存档：价格 {got['prices']} 行，EFFR {got['effr']} 行，FOMC {got['fomc']} 个")
            res = safe_update(tracker, price_source="tradingview", recompute_all=True)
            wrote = archive.write(store, cfg.archive_dir)
            print(f"写回存档：价格 {wrote['prices']} 行，EFFR {wrote['effr']} 行")
            if args.site:
                repo = None
                if os.environ.get("GITHUB_REPOSITORY"):
                    repo = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}"
                k = site.export(store, cfg, Path(args.site), repo_url=repo)
                print(f"静态网页：{k} 个数据文件 → {args.site}")
            for e in res.get("errors", []):
                print(f"::warning::{e}")
            if res.get("ok"):
                return 0
            return 1 if any(e.startswith("程序出错") for e in res.get("errors", [])) else 2
        if args.cmd == "export-site":
            k = site.export(store, cfg, Path(args.out))
            print(f"静态网页：{k} 个数据文件 → {args.out}")
            return 0
        if args.cmd == "export-archive":
            wrote = archive.write(store, cfg.archive_dir)
            print(f"存档：价格 {wrote['prices']} 行，EFFR {wrote['effr']} 行 → {cfg.archive_dir}")
            return 0
        if args.cmd == "recompute":
            n = tracker.compute_dates(store.trade_dates())
            print(f"重算完成：{n} 个观察日")
            return 0
        if args.cmd == "import":
            s = tracker.import_cme()
            print(f"导入 {s['files']} 个文件，{s['rows']} 行")
            for e in s["errors"]:
                print("  [x]", e)
            return 0 if not s["errors"] else 2
        if args.cmd == "show":
            snap = analysis.snapshot(store, args.source, args.asof)
            if not snap.get("asof"):
                print("还没有数据，先运行 bootstrap")
                return 1
            _table(snap, args.meeting)
            return 0
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
