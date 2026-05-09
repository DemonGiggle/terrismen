from __future__ import annotations

import sqlite3
from pathlib import Path

from terrismen.db import connect, init_db, utcnow
from terrismen.services.chat import search_candidate_notes
from terrismen.services.notes import build_reference_label, extract_keywords


def test_extract_keywords_uses_keywords_line() -> None:
    note = "Summary line\nKeywords: api, latency, timeout"
    assert extract_keywords(note) == "api, latency, timeout"


def test_build_reference_label_prefers_pdf_pages() -> None:
    assert build_reference_label("spec.pdf", "Page 4", 4) == "spec.pdf - Page 4"
    assert build_reference_label("table.xlsx", "Sheet Specs rows 1-5", 1) == "table.xlsx - Sheet Specs rows 1-5"


def test_search_candidate_notes_returns_matches(tmp_path: Path) -> None:
    database_path = tmp_path / "terrismen.db"
    init_db(database_path)
    connection = connect(database_path)

    doc_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("spec.pdf", "/tmp/spec.pdf", "application/pdf", "pdf", "ready", "", utcnow()),
    ).lastrowid
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, "Page 2", 2, "The system retries failed uploads three times before stopping.", "", "{}", utcnow()),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO notes (document_id, source_id, note, keywords, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            source_id,
            "The upload workflow retries failed uploads three times and surfaces the last error.\nKeywords: upload, retries, errors",
            "upload, retries, errors",
            utcnow(),
        ),
    )
    connection.commit()

    rows = search_candidate_notes(connection, "How many retries happen for uploads?")

    assert rows
    assert rows[0]["source_id"] == source_id
    connection.close()
