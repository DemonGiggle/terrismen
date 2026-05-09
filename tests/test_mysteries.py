from __future__ import annotations

from pathlib import Path

from terrismen.services.chat import search_candidate_notes
from terrismen.services.notes import generate_note, resolve_mystery
from terrismen.services.parsers import ParsedSource
from terrismen.db import connect, init_db, utcnow


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses

    def complete(self, system_prompt: str, user_prompt: str, *, images=None) -> str:
        return self._responses.pop(0)


def test_generate_note_parses_structured_mysteries() -> None:
    provider = FakeProvider(
        [
            """
            {
              "note": "The page defines a two-stage retrieval flow and emphasizes direct source citations.",
              "keywords": ["retrieval", "citations"],
              "mysteries": [
                {
                  "question": "What ranking logic decides the final source shortlist?",
                  "reason": "The page says a shortlist exists but does not describe the ranking algorithm.",
                  "keywords": ["ranking", "shortlist"]
                }
              ]
            }
            """
        ]
    )

    result = generate_note(
        provider,
        "spec.pdf",
        ParsedSource(locator="Page 4", content="Two-stage retrieval with source citations.", page_number=4),
        [],
    )

    assert "Keywords: retrieval, citations" in result.note_text
    assert result.keywords == "retrieval, citations"
    assert len(result.mysteries) == 1
    assert result.mysteries[0].question == "What ranking logic decides the final source shortlist?"


def test_resolve_mystery_parses_grounded_ids() -> None:
    provider = FakeProvider(
        [
            """
            {"status":"resolved","summary":"Later notes explain that BM25 is used before the model picks the final references.","note_ids":[11],"source_ids":[21]}
            """
        ]
    )
    resolution = resolve_mystery(
        provider,
        "spec.pdf",
        {
            "question": "How are notes ranked before the final answer step?",
            "reason": "This page mentions ranking but omits the algorithm.",
            "keywords": "ranking, retrieval",
            "origin_locator": "Page 2",
            "origin_page_number": 2,
        },
        [
            {
                "note_id": 11,
                "source_id": 21,
                "locator": "Page 8",
                "page_number": 8,
                "note": "The app uses BM25 over notes and source text before source selection.",
                "content": "BM25 is used to rank notes before the provider chooses the best source IDs.",
            }
        ],
    )

    assert resolution.status == "resolved"
    assert resolution.note_ids == [11]
    assert resolution.source_ids == [21]


def test_search_candidate_notes_returns_mystery_resolution_matches(tmp_path: Path) -> None:
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
        (doc_id, "Page 2", 2, "The system ranks notes with BM25 before selecting final source references.", "", "{}", utcnow()),
    ).lastrowid
    note_id = connection.execute(
        """
        INSERT INTO notes (document_id, source_id, note, keywords, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            source_id,
            "The retrieval stage ranks notes with BM25 before the provider chooses source IDs.\nKeywords: retrieval, bm25, ranking",
            "retrieval, bm25, ranking",
            utcnow(),
        ),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO unresolved_mysteries (
            document_id, source_id, note_id, question, reason, keywords, status, resolution_summary,
            resolution_note_id, resolution_source_id, created_at, resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            source_id,
            note_id,
            "What ranking logic decides the final shortlist?",
            "The earlier page mentions ranking but not the algorithm.",
            "ranking, shortlist",
            "resolved",
            "Later notes explain that BM25 performs the ranking before the provider picks final source references.",
            note_id,
            source_id,
            utcnow(),
            utcnow(),
        ),
    )
    connection.commit()

    rows = search_candidate_notes(connection, "Which ranking logic decides the shortlist?")

    assert rows
    assert any("BM25" in row["note"] for row in rows)
    connection.close()
