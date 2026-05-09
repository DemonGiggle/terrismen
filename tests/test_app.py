from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def load_app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("TERRISMEN_DATA_ROOT", str(tmp_path))
    import terrismen.app as app_module

    return importlib.reload(app_module)


def test_index_links_to_settings_page(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/settings"' in response.text
    assert 'id="settings-form"' not in response.text


def test_settings_page_renders_dedicated_form(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)

    response = client.get("/settings")

    assert response.status_code == 200
    assert 'id="settings-form"' in response.text
    assert "Back to workspace" in response.text
