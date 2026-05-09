from __future__ import annotations

from pathlib import Path

from terrismen.config import AppConfig
from terrismen.db import connect, init_db
from terrismen.services.ingestion import continue_document_ingestion, create_document_ingestion
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
        SET provider_type = ?, base_url = ?, model = ?, api_key = ?, temperature = ?
        WHERE id = 1
        """,
        ("ollama", "http://localhost:11434", "llama3.2", "", 0.2),
    )
    connection.commit()


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
        "SELECT status, progress_step_name, progress_step_index, progress_step_count FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    assert row["status"] == "processing"
    assert row["progress_step_name"] == "parsing document"
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
        SELECT status, kind, progress_step_name, progress_step_index, progress_step_count,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()

    assert row["status"] == "ready"
    assert row["kind"] == "text"
    assert row["progress_step_name"] == "finalizing document"
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
