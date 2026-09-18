from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    db_path: Path
    cme_download_dir: Path
    bootstrap_range: str = "2y"
    daily_range: str = "3mo"
    months_ahead: int = 30
    port: int = 8766
    target_override: list[float] | None = None
    request: dict = field(default_factory=dict)
    price_source: str = "yahoo"
    archive_dir: Path = ROOT / "data" / "archive"
    digest: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or ROOT / "config.yaml"
        raw = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        def p(key, default):
            v = Path(raw.get(key) or default)
            return v if v.is_absolute() else ROOT / v

        override = raw.get("target_override")
        if override is not None:
            if not (isinstance(override, (list, tuple)) and len(override) == 2):
                raise SystemExit("config.yaml 里 target_override 要写成 [下限, 上限]，比如 [3.75, 4.00]")
            override = [float(override[0]), float(override[1])]
        return cls(
            db_path=p("db_path", "data/fedwatch.sqlite"),
            cme_download_dir=p("cme_download_dir", "data/cme_downloads"),
            bootstrap_range=str(raw.get("bootstrap_range") or "2y"),
            daily_range=str(raw.get("daily_range") or "3mo"),
            months_ahead=int(raw.get("months_ahead") or 30),
            port=int(raw.get("port") or 8766),
            target_override=override,
            request=dict(raw.get("request") or {}),
            price_source=str(raw.get("price_source") or "yahoo"),
            archive_dir=p("archive_dir", "data/archive"),
            digest=dict(raw.get("digest") or {}),
        )
