from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


DATA_ROOT_ENV_VAR = "TERRISMEN_DATA_ROOT"
APP_CONFIG_ENV_VAR = "TERRISMEN_APP_CONFIG"


@dataclass(slots=True)
class AppConfig:
    data_root: Path
    uploads_dir: Path
    images_dir: Path
    database_path: Path
    host: str
    port: int
    app_config_path: Path | None = None


def data_root_is_env_controlled() -> bool:
    return bool(os.getenv(DATA_ROOT_ENV_VAR))


def save_data_root_override(app_config_path: Path, data_root: Path) -> None:
    app_config_path.parent.mkdir(parents=True, exist_ok=True)
    app_config_path.write_text(json.dumps({"data_root": str(data_root)}, indent=2) + "\n", encoding="utf-8")


def switch_data_root(current_config: AppConfig, requested_path: str) -> AppConfig:
    if data_root_is_env_controlled():
        raise ValueError(f"Data folder is controlled by {DATA_ROOT_ENV_VAR} and cannot be changed from the UI.")
    if current_config.app_config_path is None:
        raise ValueError("App config path is unavailable for saving the data folder location.")
    if not requested_path.strip():
        raise ValueError("Data folder path is required.")

    current_root = current_config.data_root
    new_root = Path(requested_path).expanduser().resolve()
    if new_root == current_root:
        save_data_root_override(current_config.app_config_path, new_root)
        return load_config()
    if new_root.is_relative_to(current_root):
        raise ValueError("Data folder cannot be moved inside the current data folder.")
    if new_root.exists():
        if not new_root.is_dir() or any(new_root.iterdir()):
            raise ValueError("Target data folder already exists and is not empty.")
        new_root.rmdir()

    new_root.parent.mkdir(parents=True, exist_ok=True)
    if current_root.exists():
        shutil.move(str(current_root), str(new_root))
    else:
        new_root.mkdir(parents=True, exist_ok=True)
    save_data_root_override(current_config.app_config_path, new_root)
    return load_config()


def _app_config_path() -> Path:
    configured_path = os.getenv(APP_CONFIG_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return (Path.home() / ".config" / "terrismen" / "config.json").resolve()


def _load_saved_data_root(app_config_path: Path) -> Path | None:
    try:
        payload = json.loads(app_config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None

    data_root = payload.get("data_root")
    if not isinstance(data_root, str) or not data_root.strip():
        return None
    return Path(data_root).expanduser().resolve()


def load_config() -> AppConfig:
    project_root = Path(__file__).resolve().parent.parent
    app_config_path = _app_config_path()
    env_data_root = os.getenv(DATA_ROOT_ENV_VAR)
    saved_data_root = _load_saved_data_root(app_config_path)
    raw_data_root = env_data_root or saved_data_root or (project_root / "data")
    data_root = Path(raw_data_root).expanduser().resolve()
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
        app_config_path=app_config_path,
    )
