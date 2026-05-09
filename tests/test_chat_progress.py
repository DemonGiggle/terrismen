from __future__ import annotations

from pathlib import Path

from terrismen.config import AppConfig
from terrismen.db import connect, init_db, utcnow
from terrismen.services.chat import continue_chat_request, create_chat_request


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses

    def complete(self, system_prompt: str, user_prompt: str, *, images=None) -> str:
        return self._responses.pop(0)


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
        SET provider_type = ?, base_url = ?, model = ?, api_key = ?, temperature = ?
        WHERE id = 1
        """,
        ("ollama", "http://localhost:11434", "llama3.2", "", 0.2),
    )
    connection.commit()


def seed_note(connection) -> int:
    document_id = connection.execute(
        """
        INSERT INTO documents (
            original_name, stored_path, media_type, kind, status,
            progress_step_name, progress_step_index, progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("spec.pdf", "/tmp/spec.pdf", "application/pdf", "pdf", "ready", "finalizing document", 7, 7, "", utcnow()),
    ).lastrowid
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Page 2", 2, "The system uses BM25 before the provider picks the final references.", "", "{}", utcnow()),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO notes (document_id, source_id, note, keywords, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            document_id,
            source_id,
            "The retrieval stage uses BM25 before source selection.\nKeywords: bm25, retrieval",
            "bm25, retrieval",
            utcnow(),
        ),
    )
    connection.commit()
    return int(source_id)


def test_continue_chat_request_updates_final_progress(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    source_id = seed_note(connection)
    request = create_chat_request(connection, "How are sources ranked?")
    connection.close()

    monkeypatch.setattr(
        "terrismen.services.chat.build_provider",
        lambda settings: FakeProvider(
            [
                f'{{"source_ids":[{source_id}]}}',
                "The app ranks candidate notes with BM25 before selecting final sources. [spec.pdf - Page 2]",
            ]
        ),
    )

    continue_chat_request(config, request["id"])

    check_connection = connect(config.database_path)
    row = check_connection.execute(
        """
        SELECT status, progress_step_name, progress_step_index, progress_step_count, assistant_message_id
        FROM chat_requests
        WHERE id = ?
        """,
        (request["id"],),
    ).fetchone()

    assert row["status"] == "completed"
    assert row["progress_step_name"] == "generating final answer"
    assert row["progress_step_index"] == 6
    assert row["progress_step_count"] == 6
    assert row["assistant_message_id"] is not None
    check_connection.close()


def test_continue_chat_request_persists_failed_step(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    create_chat_request(connection, "How are sources ranked?")
    request = create_chat_request(connection, "What does the doc say?")
    connection.close()

    monkeypatch.setattr("terrismen.services.chat.search_candidate_notes", lambda connection, question: (_ for _ in ()).throw(RuntimeError("search failed")))

    continue_chat_request(config, request["id"])

    check_connection = connect(config.database_path)
    row = check_connection.execute(
        "SELECT status, error, progress_step_name, progress_step_index FROM chat_requests WHERE id = ?",
        (request["id"],),
    ).fetchone()

    assert row["status"] == "failed"
    assert row["error"] == "search failed"
    assert row["progress_step_name"] == "searching candidate notes"
    assert row["progress_step_index"] == 3
    check_connection.close()
