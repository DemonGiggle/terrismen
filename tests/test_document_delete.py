from __future__ import annotations

from pathlib import Path

from terrismen.db import connect, init_db, utcnow
from terrismen.services.documents import delete_document


def test_delete_document_removes_rows_and_files(tmp_path: Path) -> None:
    database_path = tmp_path / "terrismen.db"
    init_db(database_path)
    upload_path = tmp_path / "uploads" / "doc.txt"
    image_path = tmp_path / "images" / "page.png"
    upload_path.parent.mkdir(parents=True)
    image_path.parent.mkdir(parents=True)
    upload_path.write_text("document")
    image_path.write_text("image")

    connection = connect(database_path)
    document_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc.txt", str(upload_path), "text/plain", "text", "ready", "", utcnow()),
    ).lastrowid
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, "Chunk 1", 1, "source", "", "{}", utcnow()),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
        (document_id, source_id, "note", "note", utcnow()),
    ).lastrowid
    mystery_id = connection.execute(
        """
        INSERT INTO unresolved_mysteries (document_id, source_id, note_id, question, reason, keywords, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, source_id, note_id, "question", "reason", "question", "open", utcnow()),
    ).lastrowid
    connection.execute(
        "INSERT INTO source_images (source_id, image_path, mime_type, description) VALUES (?, ?, ?, ?)",
        (source_id, str(image_path), "image/png", "image"),
    )
    connection.execute(
        "INSERT INTO mystery_refs (mystery_id, relation_type, note_id, ref_rank, why_relevant) VALUES (?, ?, ?, ?, ?)",
        (mystery_id, "related", note_id, 1, "because"),
    )
    connection.execute(
        """
        INSERT INTO malformed_notes (
            document_id, source_id, locator, page_number, error_type, error_detail, raw_response, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, source_id, "Chunk 1", 1, "partial_coverage", "bad note", "{}", utcnow(), utcnow()),
    )
    connection.commit()

    assert delete_document(connection, int(document_id)) is True

    for table in ("documents", "sources", "notes", "unresolved_mysteries", "mystery_refs", "source_images", "malformed_notes"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert not upload_path.exists()
    assert not image_path.exists()
    connection.close()


def test_delete_document_returns_false_for_missing_document(tmp_path: Path) -> None:
    database_path = tmp_path / "terrismen.db"
    init_db(database_path)
    connection = connect(database_path)

    assert delete_document(connection, 999) is False
    connection.close()
