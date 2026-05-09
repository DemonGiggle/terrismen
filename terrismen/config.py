from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    data_root: Path
    uploads_dir: Path
    images_dir: Path
    database_path: Path
    host: str
    port: int


def load_config() -> AppConfig:
    project_root = Path(__file__).resolve().parent.parent
    data_root = Path(os.getenv("TERRISMEN_DATA_ROOT", project_root / "data")).expanduser().resolve()
    uploads_dir = data_root / "uploads"
    images_dir = data_root / "images"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        data_root=data_root,
        uploads_dir=uploads_dir,
        images_dir=images_dir,
        database_path=data_root / "terrismen.db",
        host=os.getenv("TERRISMEN_HOST", "127.0.0.1"),
        port=int(os.getenv("TERRISMEN_PORT", "8000")),
    )
