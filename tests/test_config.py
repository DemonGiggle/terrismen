from __future__ import annotations

import json

import pytest

from terrismen.config import load_config, save_data_root_override, switch_data_root


def test_load_config_uses_temp_app_config_path(tmp_path, monkeypatch) -> None:
    app_config_path = tmp_path / "app-config.json"
    configured_data_root = tmp_path / "configured-data"
    monkeypatch.delenv("TERRISMEN_DATA_ROOT", raising=False)
    monkeypatch.setenv("TERRISMEN_APP_CONFIG", str(app_config_path))
    save_data_root_override(app_config_path, configured_data_root)

    config = load_config()

    assert config.app_config_path == app_config_path.resolve()
    assert config.data_root == configured_data_root.resolve()
    assert config.uploads_dir.parent == configured_data_root.resolve()
    assert config.images_dir.parent == configured_data_root.resolve()


def test_switch_data_root_moves_temp_files_only_within_tmp_path(tmp_path, monkeypatch) -> None:
    app_config_path = tmp_path / "app-config.json"
    old_data_root = tmp_path / "old-data"
    new_data_root = tmp_path / "new-data"
    monkeypatch.delenv("TERRISMEN_DATA_ROOT", raising=False)
    monkeypatch.setenv("TERRISMEN_APP_CONFIG", str(app_config_path))
    save_data_root_override(app_config_path, old_data_root)
    (old_data_root / "uploads").mkdir(parents=True, exist_ok=True)
    (old_data_root / "uploads" / "note.txt").write_text("hello", encoding="utf-8")

    config = load_config()
    switched = switch_data_root(config, str(new_data_root))

    assert switched.data_root == new_data_root.resolve()
    assert (new_data_root / "uploads" / "note.txt").read_text(encoding="utf-8") == "hello"
    assert not old_data_root.exists()
    saved = json.loads(app_config_path.read_text(encoding="utf-8"))
    assert saved["data_root"] == str(new_data_root.resolve())


def test_switch_data_root_rejects_ui_change_when_env_controls_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TERRISMEN_DATA_ROOT", str(tmp_path / "env-data"))
    monkeypatch.setenv("TERRISMEN_APP_CONFIG", str(tmp_path / "app-config.json"))
    config = load_config()

    with pytest.raises(ValueError, match="controlled by TERRISMEN_DATA_ROOT"):
        switch_data_root(config, str(tmp_path / "other-data"))


def test_switch_data_root_rejects_file_destination(tmp_path, monkeypatch) -> None:
    app_config_path = tmp_path / "app-config.json"
    old_data_root = tmp_path / "old-data"
    file_destination = tmp_path / "occupied-path"
    monkeypatch.delenv("TERRISMEN_DATA_ROOT", raising=False)
    monkeypatch.setenv("TERRISMEN_APP_CONFIG", str(app_config_path))
    save_data_root_override(app_config_path, old_data_root)
    file_destination.write_text("not a directory", encoding="utf-8")

    config = load_config()

    with pytest.raises(ValueError, match="already exists and is not empty"):
        switch_data_root(config, str(file_destination))
