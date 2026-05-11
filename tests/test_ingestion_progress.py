from __future__ import annotations

from pathlib import Path

from terrismen.config import AppConfig
from terrismen.db import connect, init_db
from terrismen.services.ingestion import (
    continue_document_ingestion,
    create_document_ingestion,
    load_mystery_resolution_batch_size,
    retry_document_ingestion,
)
from terrismen.services.notes import GeneratedNote, MysteryResolution
from terrismen.services.parsers import ParsedSource, ParserError


class FakeProvider:
    def complete(self, system_prompt: str, user_prompt: str, *, images=None) -> str:
        return "{}"


def build_config(tmp_path: Path) -> AppConfig:
    uploads_dir = tmp_path / "uploads"
    images_dir = tmp_path / "images"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        data_root=tmp_path,
        uploads_dir=uploads_dir,
        images_dir=images_dir,
        database_path=tmp_path / "terrismen.db",
        host="127.0.0.1",
        port=8000,
    )


def configure_provider(connection) -> None:
    connection.execute(
        """
        UPDATE settings
        SET provider_type = ?, base_url = ?, model = ?, api_key = ?, temperature = ?, llm_timeout_seconds = ?
        WHERE id = 1
        """,
        ("ollama", "http://localhost:11434", "llama3.2", "", 0.2, 600.0),
    )
    connection.commit()


def test_load_mystery_resolution_batch_size_uses_default_valid_and_invalid_values(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)

    assert load_mystery_resolution_batch_size(connection) == 5

    connection.execute("UPDATE settings SET mystery_resolution_batch_size = ? WHERE id = 1", (9,))
    connection.commit()
    assert load_mystery_resolution_batch_size(connection) == 9

    connection.execute("UPDATE settings SET mystery_resolution_batch_size = ? WHERE id = 1", (0,))
    connection.commit()
    assert load_mystery_resolution_batch_size(connection) == 5

    connection.execute("UPDATE settings SET mystery_resolution_batch_size = ? WHERE id = 1", (99,))
    connection.commit()
    assert load_mystery_resolution_batch_size(connection) == 5
    connection.close()


def test_create_document_ingestion_records_initial_progress(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)

    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )

    row = connection.execute(
        "SELECT status, progress_step_name, progress_detail, progress_step_index, progress_step_count FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    assert row["status"] == "processing"
    assert row["progress_step_name"] == "parsing document"
    assert row["progress_detail"] == ""
    assert row["progress_step_index"] == 3
    assert row["progress_step_count"] == 7
    connection.close()


def test_continue_document_ingestion_updates_final_progress(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )
    connection.close()

    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(
        "terrismen.services.ingestion.generate_note",
        lambda provider, document_name, source, image_descriptions: GeneratedNote(
            note_text="Summary line\nKeywords: alpha, beta",
            keywords="alpha, beta",
            mysteries=[],
        ),
    )
    monkeypatch.setattr(
        "terrismen.services.ingestion.resolve_mystery",
        lambda provider, document_name, mystery, candidates: MysteryResolution(
            status="open",
            summary="Still open",
            note_ids=[],
            source_ids=[],
        ),
    )

    continue_document_ingestion(config, document_id)

    check_connection = connect(config.database_path)
    row = check_connection.execute(
        """
        SELECT status, kind, progress_step_name, progress_detail, progress_step_index, progress_step_count,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()

    assert row["status"] == "ready"
    assert row["kind"] == "text"
    assert row["progress_step_name"] == "finalizing document"
    assert row["progress_detail"] == ""
    assert row["progress_step_index"] == 7
    assert row["progress_step_count"] == 7
    assert row["note_count"] == 1
    check_connection.close()


def test_continue_document_ingestion_persists_failed_step(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )
    connection.close()

    monkeypatch.setattr("terrismen.services.ingestion.parse_document", lambda file_path, images_dir: (_ for _ in ()).throw(ParserError("parse failed")))

    continue_document_ingestion(config, document_id)

    check_connection = connect(config.database_path)
    row = check_connection.execute(
        "SELECT status, error, progress_step_name, progress_step_index FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    assert row["status"] == "failed"
    assert row["error"] == "parse failed"
    assert row["progress_step_name"] == "parsing document"
    assert row["progress_step_index"] == 3
    check_connection.close()


def test_retry_document_ingestion_resets_partial_outputs_for_parse_stage(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )
    connection.execute(
        "UPDATE documents SET status = 'failed', progress_step_name = ?, progress_detail = ?, progress_step_index = ?, error = ? WHERE id = ?",
        ("parsing document", "", 3, "provider failed", document_id),
    )
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Page 1", 1, "content", "{}"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, 'now')",
        (document_id, source_id, "note", "k"),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO unresolved_mysteries (document_id, source_id, note_id, question, reason, keywords, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'open', 'now')
        """,
        (document_id, source_id, note_id, "mystery?", "", ""),
    )
    connection.commit()

    payload = retry_document_ingestion(connection, document_id)

    row = connection.execute(
        """
        SELECT status, error, kind, progress_step_name, progress_detail,
               (SELECT COUNT(*) FROM sources WHERE document_id = documents.id) AS source_count,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count,
               (SELECT COUNT(*) FROM unresolved_mysteries WHERE document_id = documents.id) AS mystery_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()

    assert payload["status"] == "processing"
    assert row["status"] == "processing"
    assert row["error"] == ""
    assert row["kind"] == ""
    assert row["progress_step_name"] == "parsing document"
    assert row["progress_detail"] == ""
    assert row["source_count"] == 0
    assert row["note_count"] == 0
    assert row["mystery_count"] == 0
    connection.close()


def test_retry_document_ingestion_reuses_completed_sources_for_note_stage(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )
    connection.execute(
        "UPDATE documents SET kind = ?, status = 'failed', progress_step_name = ?, progress_detail = ?, progress_step_index = ?, error = ? WHERE id = ?",
        ("text", "generating notes", "Processing 1/3 sections", 5, "provider failed", document_id),
    )
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Chunk 1", 1, "content", "{}"),
    ).lastrowid
    connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, 'now')",
        (document_id, source_id, "note", "k"),
    )
    connection.commit()

    payload = retry_document_ingestion(connection, document_id)

    row = connection.execute(
        """
        SELECT status, error, kind, progress_step_name, progress_detail,
               (SELECT COUNT(*) FROM sources WHERE document_id = documents.id) AS source_count,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()

    assert payload["status"] == "processing"
    assert row["status"] == "processing"
    assert row["error"] == ""
    assert row["kind"] == "text"
    assert row["progress_step_name"] == "generating notes"
    assert row["progress_detail"] == ""
    assert row["source_count"] == 1
    assert row["note_count"] == 1
    connection.close()


def test_retry_document_ingestion_reuses_existing_outputs_for_mystery_stage(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )
    connection.execute(
        "UPDATE documents SET kind = ?, status = 'failed', progress_step_name = ?, progress_detail = ?, progress_step_index = ?, error = ? WHERE id = ?",
        ("text", "resolving mysteries", "Processing 1/4 mysteries", 6, "resolution failed", document_id),
    )
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Page 1", 1, "content", "{}"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, 'now')",
        (document_id, source_id, "note", "k"),
    ).lastrowid
    mystery_id = connection.execute(
        """
        INSERT INTO unresolved_mysteries (
            document_id, source_id, note_id, question, reason, keywords, status,
            resolution_summary, resolution_note_id, resolution_source_id, created_at, resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'resolved', ?, ?, ?, 'now', 'now')
        """,
        (document_id, source_id, note_id, "mystery?", "", "", "resolved text", note_id, source_id),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO mystery_refs (mystery_id, relation_type, note_id, source_id, ref_rank, why_relevant)
        VALUES (?, 'resolution_note', ?, NULL, 1, '')
        """,
        (mystery_id, note_id),
    )
    connection.commit()

    payload = retry_document_ingestion(connection, document_id)

    row = connection.execute(
        """
        SELECT status, error, kind, progress_step_name, progress_detail,
               (SELECT COUNT(*) FROM sources WHERE document_id = documents.id) AS source_count,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    mystery_row = connection.execute(
        """
        SELECT status, resolution_summary, resolution_note_id, resolution_source_id, resolved_at
        FROM unresolved_mysteries
        WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    ref_count = connection.execute("SELECT COUNT(*) FROM mystery_refs WHERE mystery_id = ?", (mystery_id,)).fetchone()[0]

    assert payload["status"] == "processing"
    assert row["status"] == "processing"
    assert row["error"] == ""
    assert row["kind"] == "text"
    assert row["progress_step_name"] == "resolving mysteries"
    assert row["progress_detail"] == ""
    assert row["source_count"] == 1
    assert row["note_count"] == 1
    assert mystery_row["status"] == "resolved"
    assert mystery_row["resolution_summary"] == "resolved text"
    assert mystery_row["resolution_note_id"] == note_id
    assert mystery_row["resolution_source_id"] == source_id
    assert mystery_row["resolved_at"] == "now"
    assert ref_count == 1
    connection.close()


def test_continue_document_ingestion_resumes_only_open_mysteries(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )
    connection.execute(
        "UPDATE documents SET kind = ?, status = 'processing', progress_step_name = ?, progress_detail = ?, progress_step_index = ? WHERE id = ?",
        ("text", "resolving mysteries", "Processing 1/2 mysteries", 6, document_id),
    )
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Page 1", 1, "content", "{}"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, 'now')",
        (document_id, source_id, "note", "k"),
    ).lastrowid
    resolved_mystery_id = connection.execute(
        """
        INSERT INTO unresolved_mysteries (
            document_id, source_id, note_id, question, reason, keywords, status,
            resolution_summary, resolution_note_id, resolution_source_id, created_at, resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'resolved', ?, ?, ?, 'now', 'now')
        """,
        (document_id, source_id, note_id, "resolved?", "", "", "resolved text", note_id, source_id),
    ).lastrowid
    open_mystery_id = connection.execute(
        """
        INSERT INTO unresolved_mysteries (
            document_id, source_id, note_id, question, reason, keywords, status,
            resolution_summary, resolution_note_id, resolution_source_id, created_at, resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'open', '', NULL, NULL, 'now', NULL)
        """,
        (document_id, source_id, note_id, "open?", "", ""),
    ).lastrowid
    connection.commit()
    connection.close()

    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: FakeProvider())
    seen_questions: list[str] = []

    def fake_resolve_mystery(provider, document_name, mystery, candidates):
        seen_questions.append(str(mystery["question"]))
        return MysteryResolution(status="resolved", summary="Now resolved", note_ids=[note_id], source_ids=[source_id])

    monkeypatch.setattr("terrismen.services.ingestion.resolve_mystery", fake_resolve_mystery)
    monkeypatch.setattr(
        "terrismen.services.ingestion._search_mystery_candidates",
        lambda connection, document_id, search_text, limit=8: [
            {
                "note_id": note_id,
                "source_id": source_id,
                "note": "note",
                "keywords": "k",
                "content": "content",
                "locator": "Page 1",
                "page_number": 1,
            }
        ],
    )

    continue_document_ingestion(config, document_id)

    check_connection = connect(config.database_path)
    resolved_row = check_connection.execute(
        "SELECT status, resolution_summary FROM unresolved_mysteries WHERE id = ?",
        (resolved_mystery_id,),
    ).fetchone()
    open_row = check_connection.execute(
        "SELECT status, resolution_summary FROM unresolved_mysteries WHERE id = ?",
        (open_mystery_id,),
    ).fetchone()

    assert seen_questions == ["open?"]
    assert resolved_row["status"] == "resolved"
    assert resolved_row["resolution_summary"] == "resolved text"
    assert open_row["status"] == "resolved"
    assert open_row["resolution_summary"] == "Now resolved"
    check_connection.close()


def test_retry_document_ingestion_rejects_non_failed_documents(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )

    try:
        retry_document_ingestion(connection, document_id)
    except ValueError as exc:
        assert str(exc) == "Only failed documents can be retried"
    else:
        raise AssertionError("retry_document_ingestion should reject non-failed documents")

    connection.close()
