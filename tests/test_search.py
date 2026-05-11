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


def test_search_candidate_notes_respects_document_scope(tmp_path: Path) -> None:
    database_path = tmp_path / "terrismen.db"
    init_db(database_path)
    connection = connect(database_path)

    first_doc_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("first.pdf", "/tmp/first.pdf", "application/pdf", "pdf", "ready", "", utcnow()),
    ).lastrowid
    second_doc_id = connection.execute(
        """
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("second.pdf", "/tmp/second.pdf", "application/pdf", "pdf", "ready", "", utcnow()),
    ).lastrowid
    first_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (first_doc_id, "Page 1", 1, "Alpha scoped retrieval fact.", "", "{}", utcnow()),
    ).lastrowid
    second_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (second_doc_id, "Page 1", 1, "Alpha scoped retrieval fact.", "", "{}", utcnow()),
    ).lastrowid
    connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
        (first_doc_id, first_source_id, "Alpha scoped retrieval fact from first.", "alpha", utcnow()),
    )
    connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, ?)",
        (second_doc_id, second_source_id, "Alpha scoped retrieval fact from second.", "alpha", utcnow()),
    )
    connection.commit()

    rows = search_candidate_notes(connection, "alpha scoped", document_ids=[int(second_doc_id)])

    assert rows
    assert {row["source_id"] for row in rows} == {second_source_id}
    connection.close()


def test_search_candidate_notes_returns_secondary_source_links(tmp_path: Path) -> None:
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
    primary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, "Page 2", 2, "Primary source content.", "", "{}", utcnow()),
    ).lastrowid
    secondary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, "Page 3", 3, "Secondary source content.", "", "{}", utcnow()),
    ).lastrowid
    note_id = connection.execute(
        """
        INSERT INTO notes (document_id, source_id, note, keywords, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            primary_source_id,
            "A batched note that refers to both related pages.\nKeywords: batched, related",
            "batched, related",
            utcnow(),
        ),
    ).lastrowid
    connection.execute(
        "INSERT INTO note_sources (note_id, source_id, ref_rank) VALUES (?, ?, ?)",
        (note_id, secondary_source_id, 2),
    )
    connection.commit()

    rows = search_candidate_notes(connection, "related pages")

    assert rows
    assert {row["source_id"] for row in rows} == {primary_source_id, secondary_source_id}
    connection.close()
