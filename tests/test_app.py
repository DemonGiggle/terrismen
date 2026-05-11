from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def load_app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("TERRISMEN_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("TERRISMEN_APP_CONFIG", str(tmp_path / "app-config.json"))
    import terrismen.app as app_module

    return importlib.reload(app_module)


def test_index_links_to_settings_page(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/settings"' in response.text
    assert 'id="settings-form"' not in response.text
    assert "Ask your documents." in response.text
    assert "Start Process" in response.text
    assert "Using selected documents" in response.text
    assert "Document detail" not in response.text
    assert '/static/styles.css?v=asset-math-render-20260511' in response.text
    assert '/static/app.js?v=asset-math-render-20260511' in response.text


def test_settings_page_renders_dedicated_form(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)

    response = client.get("/settings")

    assert response.status_code == 200
    assert 'id="settings-form"' in response.text
    assert "Back to workspace" in response.text
    assert "Data folder" in response.text
    assert "Current data path" in response.text
    assert "LLM timeout (seconds)" in response.text
    assert '/static/styles.css?v=asset-math-render-20260511' in response.text
    assert '/static/settings.js?v=asset-ui-data-root-20260511' in response.text


def test_settings_api_round_trips_timeout(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)

    response = client.put(
        "/api/settings",
        json={
            "data_root": str(tmp_path),
            "provider_type": "ollama",
            "base_url": "http://localhost:11434",
            "model": "llama3.2",
            "api_key": "",
            "temperature": 0.2,
            "llm_timeout_seconds": 900,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_root"] == str(tmp_path)
    assert payload["data_root_locked"] is True
    assert payload["llm_timeout_seconds"] == 900


def test_upload_returns_initial_progress_payload(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "continue_document_ingestion", lambda config, document_id: None)
    client = TestClient(app_module.app)

    client.put(
        "/api/settings",
        json={
            "data_root": str(tmp_path),
            "provider_type": "ollama",
            "base_url": "http://localhost:11434",
            "model": "llama3.2",
            "api_key": "",
            "temperature": 0.2,
            "llm_timeout_seconds": 600,
        },
    )

    response = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"alpha\nbeta\n", "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"processing", "ready"}
    assert payload["progress_detail"] == ""
    assert payload["progress_step_count"] == 7


def test_chat_returns_request_progress_payload(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "continue_chat_request", lambda config, request_id: None)
    client = TestClient(app_module.app)

    response = client.post("/api/chat", json={"message": "What does the document say?", "document_ids": []})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert payload["progress_step_count"] == 6
    assert payload["selected_document_ids"] == []


def test_retry_document_endpoint_restarts_failed_document(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "continue_document_ingestion", lambda config, document_id: None)
    client = TestClient(app_module.app)
    connection = app_module.connect(app_module.config.database_path)
    connection.execute(
        """
        UPDATE settings
        SET provider_type = ?, base_url = ?, model = ?, api_key = ?, temperature = ?, llm_timeout_seconds = ?
        WHERE id = 1
        """,
        ("ollama", "http://localhost:11434", "llama3.2", "", 0.2, 600.0),
    )
    document_id = connection.execute(
        """
        INSERT INTO documents (
            original_name, stored_path, media_type, kind, status, progress_step_name, progress_detail, progress_step_index,
            progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.txt", "/tmp/notes.txt", "text/plain", "", "failed", "parsing document", "Processing 1/3 pages", 3, 7, "parse failed", "now"),
    ).lastrowid
    connection.commit()
    connection.close()

    response = client.post(f"/api/documents/{document_id}/retry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert payload["error"] == ""
    assert payload["progress_detail"] == ""


def test_retry_document_endpoint_rejects_non_failed_document(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "continue_document_ingestion", lambda config, document_id: None)
    client = TestClient(app_module.app)
    connection = app_module.connect(app_module.config.database_path)
    document_id = connection.execute(
        """
        INSERT INTO documents (
            original_name, stored_path, media_type, kind, status, progress_step_name, progress_detail, progress_step_index,
            progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.txt", "/tmp/notes.txt", "text/plain", "", "processing", "parsing document", "", 3, 7, "", "now"),
    ).lastrowid
    connection.commit()
    connection.close()

    response = client.post(f"/api/documents/{document_id}/retry")

    assert response.status_code == 400
    assert response.json()["detail"] == "Only failed documents can be retried"


def test_notes_page_serves_static_page(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)

    response = client.get("/documents/123/notes")

    assert response.status_code == 200
    assert "Notes" in response.text
    assert "note-type-filter" in response.text
    assert 'class="shell notes-shell"' in response.text
    assert "<main>" in response.text
    assert 'aria-label="Previous page of notes"' in response.text
    assert 'aria-label="Next page of notes"' in response.text
    assert '/static/styles.css?v=asset-math-render-20260511' in response.text
    assert '/static/notes.js?v=asset-math-render-20260511' in response.text


def test_document_notes_api_paginates_and_filters(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)
    connection = app_module.connect(app_module.config.database_path)
    document_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.pdf", "/tmp/notes.pdf", "application/pdf", "pdf", "ready", "", app_module.config.database_path.as_posix()),
    ).lastrowid
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 1", 1, "content", "", "{}", app_module.config.database_path.as_posix()),
    ).lastrowid
    for index in range(3):
        note_id = connection.execute(
            "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
            (document_id, source_id, f"note {index}", "note", app_module.config.database_path.as_posix()),
        ).lastrowid
    connection.execute(
        """
        INSERT INTO unresolved_mysteries (document_id, source_id, note_id, question, reason, keywords, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, source_id, note_id, "mystery?", "unclear", "mystery", "open", app_module.config.database_path.as_posix()),
    )
    connection.commit()
    connection.close()

    response = client.get(f"/api/documents/{document_id}/notes?note_type=normal&page=2&page_size=2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["page"] == 2
    assert payload["total_pages"] == 2
    assert len(payload["items"]) == 1

    mystery_response = client.get(f"/api/documents/{document_id}/notes?note_type=mystery&page_size=2")
    assert mystery_response.status_code == 200
    mystery_payload = mystery_response.json()
    assert mystery_payload["total"] == 1
    assert mystery_payload["items"][0]["question"] == "mystery?"
