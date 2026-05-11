from __future__ import annotations

from pathlib import Path

from terrismen.services.chat import search_candidate_notes
from terrismen.services.notes import (
    MYSTERY_BATCH_INVALID_SUMMARY,
    MYSTERY_ITEM_INVALID_SUMMARY,
    build_mystery_resolution_request,
    generate_note,
    parse_mystery_resolution_batch_response,
    resolve_mysteries,
    resolve_mystery,
)
from terrismen.services.parsers import ParsedSource
from terrismen.db import connect, init_db, utcnow


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, object]] = []

    def complete(self, system_prompt: str, user_prompt: str, *, images=None) -> str:
        self.calls.append((system_prompt, user_prompt, images))
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
            {"results":[{"mystery_id":1,"status":"resolved","summary":"Later notes explain that BM25 is used before the model picks the final references.","note_ids":[11],"source_ids":[21]}]}
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
    assert '"mystery_id": 1' in provider.calls[0][1]
    assert '"candidate_sources"' in provider.calls[0][1]


def test_resolve_mysteries_builds_batch_prompt_and_parses_full_batch() -> None:
    provider = FakeProvider(
        [
            """
            ```json
            {
              "results": [
                {
                  "mystery_id": 101,
                  "status": "resolved",
                  "summary": "Later notes define the ranking as BM25 before final source selection.",
                  "note_ids": [11],
                  "source_ids": [21]
                },
                {
                  "mystery_id": 102,
                  "status": "open",
                  "summary": "The notes describe the shortlist but still do not define the tie-breaker.",
                  "note_ids": [],
                  "source_ids": []
                }
              ]
            }
            ```
            """
        ]
    )
    requests = [
        build_mystery_resolution_request(
            "spec.pdf",
            {
                "id": 101,
                "question": "How are notes ranked before the final answer step?",
                "reason": "The earlier page mentions ranking but omits the algorithm.",
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
        ),
        build_mystery_resolution_request(
            "spec.pdf",
            {
                "id": 102,
                "question": "What tie-breaker decides between equal BM25 scores?",
                "reason": "Ranking is mentioned, but equal-score handling is omitted.",
                "keywords": "ranking, tie-breaker",
                "origin_locator": "Page 3",
                "origin_page_number": 3,
            },
            [
                {
                    "note_id": 12,
                    "source_id": 22,
                    "locator": "Page 9",
                    "page_number": 9,
                    "note": "The retrieval section repeats BM25 but never explains ties.",
                    "content": "No tie-breaker is given in the later section either.",
                }
            ],
        ),
    ]

    results = resolve_mysteries(provider, requests)

    assert [result.mystery_id for result in results] == [101, 102]
    assert results[0].status == "resolved"
    assert results[0].note_ids == [11]
    assert results[1].status == "open"
    assert '"mystery_id": 101' in provider.calls[0][1]
    assert '"mystery_id": 102' in provider.calls[0][1]
    assert '"candidate_sources"' in provider.calls[0][1]


def test_parse_batch_mystery_response_supports_mixed_outcomes() -> None:
    requests = [
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 201, "question": "resolved?", "reason": "", "keywords": "", "origin_locator": "Page 1", "origin_page_number": 1},
            [{"note_id": 31, "source_id": 41, "locator": "Page 4", "page_number": 4, "note": "note", "content": "source"}],
        ),
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 202, "question": "open?", "reason": "", "keywords": "", "origin_locator": "Page 2", "origin_page_number": 2},
            [{"note_id": 32, "source_id": 42, "locator": "Page 5", "page_number": 5, "note": "note", "content": "source"}],
        ),
    ]

    results = parse_mystery_resolution_batch_response(
        """
        {
          "results": [
            {"mystery_id": 201, "status": "resolved", "summary": "Resolved from later notes.", "note_ids": [31], "source_ids": [41]},
            {"mystery_id": 202, "status": "open", "summary": "Still undefined in the provided material.", "note_ids": [], "source_ids": []}
          ]
        }
        """,
        requests,
    )

    assert [result.status for result in results] == ["resolved", "open"]
    assert results[0].source_ids == [41]
    assert results[1].summary == "Still undefined in the provided material."


def test_parse_batch_mystery_response_salvages_valid_items_when_one_item_is_malformed() -> None:
    requests = [
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 301, "question": "first?", "reason": "", "keywords": "", "origin_locator": "Page 1", "origin_page_number": 1},
            [{"note_id": 51, "source_id": 61, "locator": "Page 4", "page_number": 4, "note": "note", "content": "source"}],
        ),
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 302, "question": "second?", "reason": "", "keywords": "", "origin_locator": "Page 2", "origin_page_number": 2},
            [{"note_id": 52, "source_id": 62, "locator": "Page 5", "page_number": 5, "note": "note", "content": "source"}],
        ),
    ]

    results = parse_mystery_resolution_batch_response(
        """
        {
          "results": [
            {"mystery_id": 301, "status": "resolved", "summary": "Grounded answer found.", "note_ids": [51], "source_ids": [61]},
            {"mystery_id": 302, "status": "resolved", "summary": "This invents ids.", "note_ids": [999], "source_ids": [62]}
          ]
        }
        """,
        requests,
    )

    assert results[0].status == "resolved"
    assert results[1].status == "open"
    assert results[1].summary == MYSTERY_ITEM_INVALID_SUMMARY


def test_parse_batch_mystery_response_ignores_unknown_mystery_ids() -> None:
    requests = [
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 401, "question": "known?", "reason": "", "keywords": "", "origin_locator": "Page 1", "origin_page_number": 1},
            [{"note_id": 71, "source_id": 81, "locator": "Page 4", "page_number": 4, "note": "note", "content": "source"}],
        )
    ]

    results = parse_mystery_resolution_batch_response(
        """
        {
          "results": [
            {"mystery_id": 999, "status": "resolved", "summary": "unknown", "note_ids": [71], "source_ids": [81]}
          ]
        }
        """,
        requests,
    )

    assert results[0].status == "open"
    assert results[0].summary == MYSTERY_ITEM_INVALID_SUMMARY


def test_parse_batch_mystery_response_uses_first_valid_duplicate_result() -> None:
    requests = [
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 501, "question": "duplicate?", "reason": "", "keywords": "", "origin_locator": "Page 1", "origin_page_number": 1},
            [{"note_id": 91, "source_id": 101, "locator": "Page 4", "page_number": 4, "note": "note", "content": "source"}],
        )
    ]

    results = parse_mystery_resolution_batch_response(
        """
        {
          "results": [
            {"mystery_id": 501, "status": "open", "summary": "Still unresolved.", "note_ids": [], "source_ids": []},
            {"mystery_id": 501, "status": "resolved", "summary": "Later answer.", "note_ids": [91], "source_ids": [101]}
          ]
        }
        """,
        requests,
    )

    assert results[0].status == "open"
    assert results[0].summary == "Still unresolved."


def test_parse_batch_mystery_response_requires_results_list() -> None:
    requests = [
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 601, "question": "missing results?", "reason": "", "keywords": "", "origin_locator": "Page 1", "origin_page_number": 1},
            [{"note_id": 111, "source_id": 121, "locator": "Page 4", "page_number": 4, "note": "note", "content": "source"}],
        )
    ]

    results = parse_mystery_resolution_batch_response('{"status":"resolved"}', requests)

    assert results[0].status == "open"
    assert results[0].summary == MYSTERY_BATCH_INVALID_SUMMARY


def test_parse_batch_mystery_response_falls_back_on_invalid_json() -> None:
    requests = [
        build_mystery_resolution_request(
            "spec.pdf",
            {"id": 701, "question": "invalid json?", "reason": "", "keywords": "", "origin_locator": "Page 1", "origin_page_number": 1},
            [{"note_id": 131, "source_id": 141, "locator": "Page 4", "page_number": 4, "note": "note", "content": "source"}],
        )
    ]

    results = parse_mystery_resolution_batch_response('```json {"results":[{"mystery_id":701', requests)

    assert results[0].status == "open"
    assert results[0].summary == MYSTERY_BATCH_INVALID_SUMMARY


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
