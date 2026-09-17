"""导出静态网页（GitHub Pages 用）。

和本地看板是同一个 index.html：页面里多一个 <meta name="fedwatch-static">，
前端就改去读 data/*.json，而不是请求本地服务的 /api/*。
每个接口的每种参数组合都预先算好写成一个文件。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import analysis
from .config import Config
from .store import Store

WEB_DIR = Path(__file__).resolve().parent / "web"


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export(store: Store, cfg: Config, out: Path, repo_url: str | None = None) -> int:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", '<head>\n<meta name="fedwatch-static" content="1">', 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    n = 0
    st = analysis.state(store, cfg)
    st.update({"static": True, "busy": None, "logs": [], "cme_dir": "data/cme_downloads", "repo_url": repo_url})
    _dump(out / "data" / "state.json", st)
    n += 1
    for source in analysis.SOURCES:
        dates = [r[0] for r in store.q("SELECT asof FROM runs WHERE source=? ORDER BY asof", (source,))]
        if not dates:
            continue
        base = out / "data" / source
        for m in st["meetings"]:
            if m.get(source):
                _dump(base / "meeting" / f"{m['meeting']}.json",
                      analysis.meeting_series(store, source, m["meeting"]))
                n += 1
        _dump(base / "snapshot" / "latest.json", analysis.snapshot(store, source, None))
        for d in dates:
            _dump(base / "snapshot" / f"{d}.json", analysis.snapshot(store, source, d))
        n += len(dates) + 1
        for mode in ("peak", "trough"):
            t = analysis.terminal(store, source, None, mode)
            _dump(base / "terminal" / f"{mode}-default.json", t)
            n += 1
            for hz in t.get("horizons") or []:
                _dump(base / "terminal" / f"{mode}-{hz}.json", analysis.terminal(store, source, hz, mode))
                n += 1
    return n
