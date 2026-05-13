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
    assert '/static/styles.css?v=asset-notes-hover-20260513' in response.text
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
    assert "Document note batch size" in response.text
    assert "Mystery batch size" in response.text
    assert "Mystery reference mode" in response.text
    assert response.text.index("Document note batch size") < response.text.index("Mystery batch size")
    assert '/static/styles.css?v=asset-notes-hover-20260513' in response.text
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
            "document_note_batch_size": 6,
            "mystery_resolution_batch_size": 7,
            "mystery_resolution_reference_mode": "notes_and_sources",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_root"] == str(tmp_path)
    assert payload["data_root_locked"] is True
    assert payload["llm_timeout_seconds"] == 900
    assert payload["document_note_batch_size"] == 6
    assert payload["mystery_resolution_batch_size"] == 7
    assert payload["mystery_resolution_reference_mode"] == "notes_and_sources"


def test_settings_api_rejects_invalid_document_note_batch_size(tmp_path, monkeypatch) -> None:
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
            "document_note_batch_size": 0,
        },
    )

    assert response.status_code == 422


def test_settings_api_rejects_invalid_mystery_batch_size(tmp_path, monkeypatch) -> None:
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
            "document_note_batch_size": 5,
            "mystery_resolution_batch_size": 0,
        },
    )

    assert response.status_code == 422


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
    assert response.json()["detail"] == "Only failed documents or ready documents with malformed notes can be retried"


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
    assert '/static/styles.css?v=asset-notes-hover-20260513' in response.text
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
    connection.execute(
        """
        INSERT INTO malformed_notes (
            document_id, source_id, locator, page_number, error_type, error_detail, raw_response, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            source_id,
            "Page 1",
            1,
            "partial_coverage",
            "The model response omitted this source unit.",
            '{"notes":[]}',
            "now",
            "now",
        ),
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

    malformed_response = client.get(f"/api/documents/{document_id}/notes?note_type=malformed&page_size=2")
    assert malformed_response.status_code == 200
    malformed_payload = malformed_response.json()
    assert malformed_payload["total"] == 1
    assert malformed_payload["items"][0]["error_type"] == "partial_coverage"
    assert malformed_payload["items"][0]["reference_label"] == "notes.pdf - Page 1"


def test_retry_document_endpoint_restarts_ready_document_with_malformed_notes(tmp_path, monkeypatch) -> None:
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
        ("notes.txt", "/tmp/notes.txt", "text/plain", "text", "ready", "finalizing document", "", 7, 7, "", "now"),
    ).lastrowid
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Chunk 2", 2, "content", "{}"),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO malformed_notes (
            document_id, source_id, locator, page_number, error_type, error_detail, raw_response, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'now', 'now')
        """,
        (document_id, source_id, "Chunk 2", 2, "partial_coverage", "bad note", "{}"),
    )
    connection.commit()
    connection.close()

    response = client.post(f"/api/documents/{document_id}/retry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    assert payload["malformed_note_count"] == 1


def test_document_detail_api_includes_note_for_secondary_source_links(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)
    connection = app_module.connect(app_module.config.database_path)
    document_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.pdf", "/tmp/notes.pdf", "application/pdf", "pdf", "ready", "", "now"),
    ).lastrowid
    primary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 1", 1, "primary", "", "{}", "now"),
    ).lastrowid
    secondary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 2", 2, "secondary", "", "{}", "now"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
        (document_id, primary_source_id, "combined note", "combined", "now"),
    ).lastrowid
    connection.execute(
        "INSERT INTO note_sources (note_id, source_id, ref_rank) VALUES (?, ?, ?)",
        (note_id, secondary_source_id, 2),
    )
    connection.commit()
    connection.close()

    response = client.get(f"/api/documents/{document_id}")

    assert response.status_code == 200
    payload = response.json()
    sources_by_id = {item["id"]: item for item in payload["sources"]}
    assert sources_by_id[secondary_source_id]["note"] == "combined note"


def test_document_notes_api_returns_multi_source_references(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)
    connection = app_module.connect(app_module.config.database_path)
    document_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.pdf", "/tmp/notes.pdf", "application/pdf", "pdf", "ready", "", "now"),
    ).lastrowid
    primary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 4", 4, "primary", "", "{}", "now"),
    ).lastrowid
    secondary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 5", 5, "secondary", "", "{}", "now"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
        (document_id, primary_source_id, "combined note", "combined", "now"),
    ).lastrowid
    connection.execute(
        "INSERT INTO note_sources (note_id, source_id, ref_rank) VALUES (?, ?, ?)",
        (note_id, secondary_source_id, 2),
    )
    connection.commit()
    connection.close()

    response = client.get(f"/api/documents/{document_id}/notes?note_type=normal&page=1&page_size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["reference_label"] == "notes.pdf - Pages 4-5"
    assert [ref["source_id"] for ref in payload["items"][0]["references"]] == [primary_source_id, secondary_source_id]


def test_summarize_note_refs_formats_noncontiguous_pages_and_mixed_sources(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)

    noncontiguous_summary = app_module.summarize_note_refs(
        "notes.pdf",
        [
            {"reference_label": "notes.pdf - Page 4", "locator": "Page 4", "page_number": 4},
            {"reference_label": "notes.pdf - Page 6", "locator": "Page 6", "page_number": 6},
        ],
    )
    mixed_summary = app_module.summarize_note_refs(
        "notes.pdf",
        [
            {"reference_label": "notes.pdf - Chunk 1", "locator": "Chunk 1", "page_number": None},
            {"reference_label": "notes.pdf - Chunk 2", "locator": "Chunk 2", "page_number": None},
            {"reference_label": "notes.pdf - Chunk 3", "locator": "Chunk 3", "page_number": None},
        ],
    )

    assert noncontiguous_summary == "notes.pdf - Pages 4, 6"
    assert mixed_summary == "notes.pdf - 3 source units"


def test_document_notes_api_count_matches_notes_with_primary_reference_rows(tmp_path, monkeypatch) -> None:
    app_module = load_app_module(tmp_path, monkeypatch)
    client = TestClient(app_module.app)
    connection = app_module.connect(app_module.config.database_path)
    document_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.pdf", "/tmp/notes.pdf", "application/pdf", "pdf", "ready", "", "now"),
    ).lastrowid
    primary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 4", 4, "primary", "", "{}", "now"),
    ).lastrowid
    orphan_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 5", 5, "orphan", "", "{}", "now"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
        (document_id, primary_source_id, "note with refs", "note", "now"),
    ).lastrowid
    orphan_note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
        (document_id, orphan_source_id, "orphan note", "orphan", "now"),
    ).lastrowid
    connection.execute("DELETE FROM note_sources WHERE note_id = ?", (orphan_note_id,))
    connection.commit()
    connection.close()

    response = client.get(f"/api/documents/{document_id}/notes?note_type=normal&page=1&page_size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == note_id
