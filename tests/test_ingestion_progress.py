from __future__ import annotations

import json
from pathlib import Path

from terrismen.config import AppConfig
from terrismen.db import connect, init_db
from terrismen.llm.base import ProviderError
from terrismen.services.ingestion import (
    _load_existing_source_rows,
    _search_mystery_candidates,
    _resolve_document_mysteries,
    continue_document_ingestion,
    create_document_ingestion,
    load_chat_provider_settings,
    load_document_note_batch_size,
    load_ingestion_provider_settings,
    load_mystery_resolution_batch_size,
    load_mystery_resolution_reference_mode,
    resume_document_ingestion,
    retry_document_ingestion,
)
from terrismen.services.notes import (
    BatchGeneratedNote,
    BatchMysteryDraft,
    GeneratedNote,
    MysteryBatchResolution,
    MysteryDraft,
    MysteryResolution,
    ParsedBatchNotes,
)
from terrismen.services.parsers import ParsedSource, ParserError


class FakeProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or ["{}"])
        self.calls: list[tuple[str, str, object]] = []

    def complete(self, system_prompt: str, user_prompt: str, *, images=None) -> str:
        self.calls.append((system_prompt, user_prompt, images))
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


def test_load_document_note_batch_size_uses_default_valid_and_invalid_values(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)

    assert load_document_note_batch_size(connection) == 5

    connection.execute("UPDATE settings SET document_note_batch_size = ? WHERE id = 1", (9,))
    connection.commit()
    assert load_document_note_batch_size(connection) == 9

    connection.execute("UPDATE settings SET document_note_batch_size = ? WHERE id = 1", (0,))
    connection.commit()
    assert load_document_note_batch_size(connection) == 5

    connection.execute("UPDATE settings SET document_note_batch_size = ? WHERE id = 1", (99,))
    connection.commit()
    assert load_document_note_batch_size(connection) == 5
    connection.close()


def test_load_mystery_resolution_reference_mode_uses_default_valid_and_invalid_values(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)

    assert load_mystery_resolution_reference_mode(connection) == "notes_only"

    connection.execute("UPDATE settings SET mystery_resolution_reference_mode = ? WHERE id = 1", ("notes_and_sources",))
    connection.commit()
    assert load_mystery_resolution_reference_mode(connection) == "notes_and_sources"

    connection.execute("UPDATE settings SET mystery_resolution_reference_mode = ? WHERE id = 1", ("bad-value",))
    connection.commit()
    assert load_mystery_resolution_reference_mode(connection) == "notes_only"
    connection.close()


def test_load_provider_settings_use_separate_ingestion_and_chat_think_levels(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)

    connection.execute(
        "UPDATE settings SET ingestion_think_level = ?, chat_think_level = ? WHERE id = 1",
        ("high", "low"),
    )
    connection.commit()

    ingestion_settings = load_ingestion_provider_settings(connection)
    chat_settings = load_chat_provider_settings(connection)

    assert ingestion_settings.think_level == "high"
    assert chat_settings.think_level == "low"
    connection.close()


def test_load_ingestion_provider_settings_normalizes_invalid_think_level(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)

    connection.execute("UPDATE settings SET ingestion_think_level = ? WHERE id = 1", ("unexpected",))
    connection.commit()

    settings = load_ingestion_provider_settings(connection)

    assert settings.think_level == "off"
    connection.close()


def test_search_mystery_candidates_returns_secondary_source_links(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
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
    primary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Chunk 1", 1, "alpha content", "{}"),
    ).lastrowid
    secondary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Chunk 2", 2, "beta content", "{}"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, 'now')",
        (document_id, primary_source_id, "Combined alpha and beta note", "alpha, beta"),
    ).lastrowid
    connection.execute(
        "INSERT INTO note_sources (note_id, source_id, ref_rank) VALUES (?, ?, ?)",
        (note_id, secondary_source_id, 2),
    )
    connection.commit()

    rows = _search_mystery_candidates(connection, document_id=int(document_id), search_text="beta note")

    assert rows
    assert {row["source_id"] for row in rows} == {primary_source_id, secondary_source_id}
    connection.close()


def test_load_existing_source_rows_counts_secondary_source_links(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
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
    primary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Chunk 1", 1, "alpha content", "{}"),
    ).lastrowid
    secondary_source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Chunk 2", 2, "beta content", "{}"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, 'now')",
        (document_id, primary_source_id, "Combined alpha and beta note", "alpha, beta"),
    ).lastrowid
    connection.execute(
        "INSERT INTO note_sources (note_id, source_id, ref_rank) VALUES (?, ?, ?)",
        (note_id, secondary_source_id, 2),
    )
    connection.commit()

    rows = _load_existing_source_rows(connection, int(document_id))

    assert rows[("Chunk 1", 1)]["note_count"] == 1
    assert rows[("Chunk 2", 2)]["note_count"] == 1
    connection.close()


def seed_open_mystery(connection, document_id: int) -> tuple[int, int, int]:
    source_id = connection.execute(
        """
        INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, '', ?, 'now')
        """,
        (document_id, "Page 3", 3, "Raw source excerpt that should only appear in notes+sources mode.", "{}"),
    ).lastrowid
    note_id = connection.execute(
        "INSERT INTO notes (document_id, source_id, note, keywords, created_at) VALUES (?, ?, ?, ?, 'now')",
        (document_id, source_id, "Later note that clarifies the ranking behavior.", "ranking, clarification"),
    ).lastrowid
    mystery_id = connection.execute(
        """
        INSERT INTO unresolved_mysteries (
            document_id, source_id, note_id, question, reason, keywords, status,
            resolution_summary, resolution_note_id, resolution_source_id, created_at, resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'open', '', NULL, NULL, 'now', NULL)
        """,
        (document_id, source_id, note_id, "How are ties resolved?", "The page explains BM25 but not ties.", "ranking, tie"),
    ).lastrowid
    connection.commit()
    return int(source_id), int(note_id), int(mystery_id)


def test_resolve_document_mysteries_notes_only_mode_omits_sources_and_requires_note_grounding(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute("UPDATE settings SET mystery_resolution_reference_mode = ? WHERE id = 1", ("notes_only",))
    document_id = connection.execute(
        """
        INSERT INTO documents (
            original_name, stored_path, media_type, kind, status, progress_step_name, progress_detail, progress_step_index,
            progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.txt", "/tmp/notes.txt", "text/plain", "text", "processing", "resolving mysteries", "", 6, 7, "", "now"),
    ).lastrowid
    source_id, note_id, mystery_id = seed_open_mystery(connection, int(document_id))
    provider = FakeProvider(
        [
            f'{{"results":[{{"mystery_id":{mystery_id},"status":"resolved","summary":"Claims a source-only resolution.","note_ids":[],"source_ids":[{source_id}]}}]}}'
        ]
    )
    monkeypatch.setattr(
        "terrismen.services.ingestion._search_mystery_candidates",
        lambda connection, document_id, search_text, limit=8: [
            {
                "note_id": note_id,
                "source_id": source_id,
                "note": "Later note that clarifies the ranking behavior.",
                "keywords": "ranking, clarification",
                "content": "Raw source excerpt that should only appear in notes+sources mode.",
                "locator": "Page 3",
                "page_number": 3,
            }
        ],
    )

    _resolve_document_mysteries(connection, provider=provider, document_id=int(document_id), document_name="notes.txt")

    mystery_row = connection.execute(
        "SELECT status, resolution_note_id, resolution_source_id FROM unresolved_mysteries WHERE id = ?",
        (mystery_id,),
    ).fetchone()
    prompt_text = provider.calls[0][1]

    assert mystery_row["status"] == "open"
    assert mystery_row["resolution_note_id"] is None
    assert mystery_row["resolution_source_id"] is None
    assert '"candidate_sources": []' in prompt_text
    assert "Raw source excerpt that should only appear in notes+sources mode." not in prompt_text
    connection.close()


def test_resolve_document_mysteries_notes_and_sources_mode_persists_source_refs(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute("UPDATE settings SET mystery_resolution_reference_mode = ? WHERE id = 1", ("notes_and_sources",))
    document_id = connection.execute(
        """
        INSERT INTO documents (
            original_name, stored_path, media_type, kind, status, progress_step_name, progress_detail, progress_step_index,
            progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.txt", "/tmp/notes.txt", "text/plain", "text", "processing", "resolving mysteries", "", 6, 7, "", "now"),
    ).lastrowid
    source_id, note_id, mystery_id = seed_open_mystery(connection, int(document_id))
    provider = FakeProvider(
        [
            f'{{"results":[{{"mystery_id":{mystery_id},"status":"resolved","summary":"Resolved directly from the source excerpt.","note_ids":[],"source_ids":[{source_id}]}}]}}'
        ]
    )
    monkeypatch.setattr(
        "terrismen.services.ingestion._search_mystery_candidates",
        lambda connection, document_id, search_text, limit=8: [
            {
                "note_id": note_id,
                "source_id": source_id,
                "note": "Later note that clarifies the ranking behavior.",
                "keywords": "ranking, clarification",
                "content": "Raw source excerpt that should only appear in notes+sources mode.",
                "locator": "Page 3",
                "page_number": 3,
            }
        ],
    )

    _resolve_document_mysteries(connection, provider=provider, document_id=int(document_id), document_name="notes.txt")

    mystery_row = connection.execute(
        "SELECT status, resolution_note_id, resolution_source_id FROM unresolved_mysteries WHERE id = ?",
        (mystery_id,),
    ).fetchone()
    ref_rows = connection.execute(
        "SELECT relation_type, note_id, source_id FROM mystery_refs WHERE mystery_id = ? ORDER BY relation_type, ref_rank",
        (mystery_id,),
    ).fetchall()
    prompt_text = provider.calls[0][1]

    assert mystery_row["status"] == "resolved"
    assert mystery_row["resolution_note_id"] is None
    assert mystery_row["resolution_source_id"] == source_id
    assert [dict(row) for row in ref_rows] == [{"relation_type": "resolution_source", "note_id": None, "source_id": source_id}]
    assert "Raw source excerpt that should only appear in notes+sources mode." in prompt_text
    connection.close()


def test_continue_document_ingestion_batches_mysteries_by_setting_and_handles_final_short_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute(
        """
        UPDATE settings
        SET document_note_batch_size = ?, mystery_resolution_batch_size = ?, mystery_resolution_reference_mode = ?
        WHERE id = 1
        """,
        (3, 2, "notes_only"),
    )
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\ngamma\n",
    )
    connection.close()

    class BatchProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def complete(self, system_prompt: str, user_prompt: str, *, images=None) -> str:
            payload = json.loads(user_prompt.split("Batch input:\n", 1)[1])
            mysteries = payload["mysteries"]
            self.calls.append(payload)
            if len(self.calls) == 1:
                return json.dumps(
                    {
                        "results": [
                            {
                                "mystery_id": mysteries[0]["mystery_id"],
                                "status": "resolved",
                                "summary": "Resolved from a later note.",
                                "note_ids": [mysteries[0]["candidate_notes"][0]["note_id"]],
                                "source_ids": [],
                            },
                            {
                                "mystery_id": mysteries[1]["mystery_id"],
                                "status": "open",
                                "summary": "Still unresolved after reviewing later notes.",
                                "note_ids": [],
                                "source_ids": [],
                            },
                        ]
                    }
                )
            return json.dumps(
                {
                    "results": [
                        {
                            "mystery_id": mysteries[0]["mystery_id"],
                            "status": "resolved",
                            "summary": "This result invents note ids.",
                            "note_ids": [999999],
                            "source_ids": [],
                        }
                    ]
                }
            )

    provider = BatchProvider()
    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: provider)
    monkeypatch.setattr(
        "terrismen.services.ingestion.parse_document",
        lambda file_path, images_dir: (
            "text",
            [
                ParsedSource(locator="Chunk 1", content="alpha content", page_number=1),
                ParsedSource(locator="Chunk 2", content="beta content", page_number=2),
                ParsedSource(locator="Chunk 3", content="gamma content", page_number=3),
            ],
        ),
    )
    monkeypatch.setattr(
        "terrismen.services.ingestion.generate_batch_notes",
        lambda provider, sources: ParsedBatchNotes(
            notes=[
                BatchGeneratedNote(
                    source_ids=[source.source_id],
                    note_text=f"{source.locator} note\nKeywords: {source.content.split()[0]}",
                    keywords=source.content.split()[0],
                    mysteries=[
                        BatchMysteryDraft(
                            source_id=source.source_id,
                            question=f"{source.content.split()[0]} mystery?",
                            reason=f"{source.content.split()[0]} gap",
                            keywords=source.content.split()[0],
                        )
                    ],
                )
                for source in sources
            ],
            missing_source_ids=[],
        ),
    )

    def search_candidates(connection, document_id, search_text, limit=8):
        keyword = "alpha" if "alpha" in search_text else "beta" if "beta" in search_text else "gamma"
        rows = connection.execute(
            """
            SELECT notes.id AS note_id, notes.source_id, notes.note, notes.keywords, sources.content, sources.locator, sources.page_number
            FROM notes
            JOIN sources ON sources.id = notes.source_id
            WHERE notes.document_id = ? AND notes.keywords LIKE ?
            ORDER BY notes.id
            LIMIT ?
            """,
            (document_id, f"%{keyword}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    monkeypatch.setattr("terrismen.services.ingestion._search_mystery_candidates", search_candidates)

    continue_document_ingestion(config, document_id)

    check_connection = connect(config.database_path)
    rows = check_connection.execute(
        "SELECT question, status, resolution_summary FROM unresolved_mysteries WHERE document_id = ? ORDER BY id",
        (document_id,),
    ).fetchall()
    document_row = check_connection.execute(
        "SELECT status, progress_step_name FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    assert [len(call["mysteries"]) for call in provider.calls] == [2, 1]
    assert [row["status"] for row in rows] == ["resolved", "open", "open"]
    assert rows[0]["resolution_summary"] == "Resolved from a later note."
    assert rows[1]["resolution_summary"] == "Still unresolved after reviewing later notes."
    assert "invalid result for this mystery" in rows[2]["resolution_summary"].lower()
    assert document_row["status"] == "ready"
    assert document_row["progress_step_name"] == "finalizing document"
    check_connection.close()


def test_resolve_document_mysteries_applies_mixed_batch_results_independently(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute(
        """
        UPDATE settings
        SET mystery_resolution_batch_size = ?, mystery_resolution_reference_mode = ?
        WHERE id = 1
        """,
        (3, "notes_only"),
    )
    document_id = connection.execute(
        """
        INSERT INTO documents (
            original_name, stored_path, media_type, kind, status, progress_step_name, progress_detail, progress_step_index,
            progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("notes.txt", "/tmp/notes.txt", "text/plain", "text", "processing", "resolving mysteries", "", 6, 7, "", "now"),
    ).lastrowid
    seeded = [seed_open_mystery(connection, int(document_id)) for _ in range(3)]

    class MixedBatchProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def complete(self, system_prompt: str, user_prompt: str, *, images=None) -> str:
            payload = json.loads(user_prompt.split("Batch input:\n", 1)[1])
            self.calls.append(payload)
            mysteries = payload["mysteries"]
            return json.dumps(
                {
                    "results": [
                        {
                            "mystery_id": mysteries[0]["mystery_id"],
                            "status": "resolved",
                            "summary": "Resolved from later notes.",
                            "note_ids": [mysteries[0]["candidate_notes"][0]["note_id"]],
                            "source_ids": [],
                        },
                        {
                            "mystery_id": mysteries[1]["mystery_id"],
                            "status": "open",
                            "summary": "Still open after reviewing the later notes.",
                            "note_ids": [],
                            "source_ids": [],
                        },
                        {
                            "mystery_id": mysteries[2]["mystery_id"],
                            "status": "resolved",
                            "summary": "Invented ids.",
                            "note_ids": [999999],
                            "source_ids": [],
                        },
                    ]
                }
            )

    provider = MixedBatchProvider()

    def candidate_for_mystery(connection, document_id, search_text, limit=8):
        index = 0 if "alpha" not in search_text else 0
        if "second" in search_text:
            index = 1
        if "third" in search_text:
            index = 2
        source_id, note_id, _ = seeded[index]
        row = connection.execute(
            """
            SELECT notes.id AS note_id, notes.source_id, notes.note, notes.keywords, sources.content, sources.locator, sources.page_number
            FROM notes
            JOIN sources ON sources.id = notes.source_id
            WHERE notes.id = ?
            """,
            (note_id,),
        ).fetchone()
        return [dict(row)]

    questions = ["first mystery?", "second mystery?", "third mystery?"]
    for (_, _, mystery_id), question in zip(seeded, questions, strict=True):
        connection.execute("UPDATE unresolved_mysteries SET question = ?, keywords = ? WHERE id = ?", (question, question, mystery_id))
    connection.commit()
    monkeypatch.setattr("terrismen.services.ingestion._search_mystery_candidates", candidate_for_mystery)

    _resolve_document_mysteries(connection, provider=provider, document_id=int(document_id), document_name="notes.txt")

    rows = connection.execute(
        """
        SELECT question, status, resolution_summary, resolution_note_id, resolution_source_id
        FROM unresolved_mysteries
        WHERE document_id = ?
        ORDER BY id
        """,
        (document_id,),
    ).fetchall()

    assert len(provider.calls) == 1
    assert [row["status"] for row in rows] == ["resolved", "open", "open"]
    assert rows[0]["resolution_note_id"] is not None
    assert rows[0]["resolution_source_id"] is not None
    assert rows[1]["resolution_note_id"] is None
    assert rows[1]["resolution_source_id"] is None
    assert "invalid result for this mystery" in rows[2]["resolution_summary"].lower()
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
        "terrismen.services.ingestion.generate_batch_notes",
        lambda provider, sources: ParsedBatchNotes(
            notes=[
                BatchGeneratedNote(
                    source_ids=[source.source_id for source in sources],
                    note_text="Summary line\nKeywords: alpha, beta",
                    keywords="alpha, beta",
                    mysteries=[],
                )
            ],
            missing_source_ids=[],
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


def test_continue_document_ingestion_batches_note_generation_and_uses_image_descriptions(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute("UPDATE settings SET document_note_batch_size = ? WHERE id = 1", (2,))
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\ngamma\n",
    )
    connection.close()

    class StubImage:
        mime_type = "image/png"

    note_batches: list[list[int]] = []
    first_batch_images: list[list[str]] = []

    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(
        "terrismen.services.ingestion.parse_document",
        lambda file_path, images_dir: (
            "text",
            [
                ParsedSource(
                    locator="Chunk 1",
                    content="alpha content",
                    page_number=1,
                    images=[(images_dir / "chunk1.png", StubImage())],
                ),
                ParsedSource(locator="Chunk 2", content="beta content", page_number=2),
                ParsedSource(locator="Chunk 3", content="gamma content", page_number=3),
            ],
        ),
    )
    monkeypatch.setattr(
        "terrismen.services.ingestion.describe_images",
        lambda provider, source: [f"{source.locator} image summary"] if source.images else [],
    )

    def fake_generate_batch_notes(provider, sources):
        note_batches.append([source.source_id for source in sources])
        if len(note_batches) == 1:
            first_batch_images.extend([list(source.image_descriptions) for source in sources])
        return ParsedBatchNotes(
            notes=[
                BatchGeneratedNote(
                    source_ids=[source.source_id],
                    note_text=f"{source.locator} note",
                    keywords=source.locator.lower(),
                    mysteries=[],
                )
                for source in sources
            ],
            missing_source_ids=[],
        )

    monkeypatch.setattr("terrismen.services.ingestion.generate_batch_notes", fake_generate_batch_notes)

    continue_document_ingestion(config, document_id)

    check_connection = connect(config.database_path)
    source_rows = check_connection.execute(
        "SELECT locator, image_summary FROM sources WHERE document_id = ? ORDER BY id",
        (document_id,),
    ).fetchall()

    assert note_batches == [[1, 2], [3]]
    assert first_batch_images[0] == ["Chunk 1 image summary"]
    assert first_batch_images[1] == []
    assert [dict(row) for row in source_rows] == [
        {"locator": "Chunk 1", "image_summary": "Chunk 1 image summary"},
        {"locator": "Chunk 2", "image_summary": ""},
        {"locator": "Chunk 3", "image_summary": ""},
    ]
    check_connection.close()


def test_continue_document_ingestion_skips_source_after_image_description_exception(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute("UPDATE settings SET document_note_batch_size = ? WHERE id = 1", (2,))
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\ngamma\n",
    )
    connection.close()

    class StubImage:
        mime_type = "image/png"

    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(
        "terrismen.services.ingestion.parse_document",
        lambda file_path, images_dir: (
            "text",
            [
                ParsedSource(
                    locator="Chunk 1",
                    content="alpha content",
                    page_number=1,
                    images=[(images_dir / "chunk1.png", StubImage())],
                ),
                ParsedSource(locator="Chunk 2", content="beta content", page_number=2),
                ParsedSource(locator="Chunk 3", content="gamma content", page_number=3),
            ],
        ),
    )
    monkeypatch.setattr(
        "terrismen.services.ingestion.describe_images",
        lambda provider, source: (_ for _ in ()).throw(ProviderError("LLM request timed out after 600.0s")),
    )
    monkeypatch.setattr(
        "terrismen.services.ingestion.generate_batch_notes",
        lambda provider, sources: ParsedBatchNotes(
            notes=[
                BatchGeneratedNote(
                    source_ids=[source.source_id],
                    note_text=f"{source.locator} note",
                    keywords=source.locator.lower(),
                    mysteries=[],
                )
                for source in sources
            ],
            missing_source_ids=[],
        ),
    )

    continue_document_ingestion(config, document_id)

    check_connection = connect(config.database_path)
    document_row = check_connection.execute(
        """
        SELECT status, progress_step_name,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count,
               (SELECT COUNT(*) FROM malformed_notes WHERE document_id = documents.id) AS malformed_note_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    malformed_rows = check_connection.execute(
        "SELECT source_id, locator, error_type, error_detail FROM malformed_notes WHERE document_id = ? ORDER BY source_id",
        (document_id,),
    ).fetchall()
    check_connection.close()

    assert document_row["status"] == "ready"
    assert document_row["progress_step_name"] == "finalizing document"
    assert document_row["note_count"] == 2
    assert document_row["malformed_note_count"] == 1
    assert [dict(row) for row in malformed_rows] == [
        {
            "source_id": 1,
            "locator": "Chunk 1",
            "error_type": "describing_images_exception",
            "error_detail": "Skipping this source unit after a describing images error: ProviderError: LLM request timed out after 600.0s",
        }
    ]


def test_continue_document_ingestion_records_malformed_notes_and_retry_recovers_missing_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute("UPDATE settings SET document_note_batch_size = ? WHERE id = 1", (2,))
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\ngamma\n",
    )
    connection.close()

    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(
        "terrismen.services.ingestion.parse_document",
        lambda file_path, images_dir: (
            "text",
            [
                ParsedSource(locator="Chunk 1", content="alpha content", page_number=1),
                ParsedSource(locator="Chunk 2", content="beta content", page_number=2),
                ParsedSource(locator="Chunk 3", content="gamma content", page_number=3),
            ],
        ),
    )

    batch_calls: list[list[int]] = []

    def fake_generate_batch_notes(provider, sources):
        batch_calls.append([source.source_id for source in sources])
        if len(batch_calls) == 1:
            return ParsedBatchNotes(
                notes=[
                    BatchGeneratedNote(
                        source_ids=[sources[0].source_id],
                        note_text="Alpha note",
                        keywords="alpha",
                        mysteries=[],
                    )
                ],
                missing_source_ids=[sources[1].source_id],
            )
        return ParsedBatchNotes(
            notes=[
                BatchGeneratedNote(
                    source_ids=[source.source_id for source in sources],
                    note_text="Recovered note",
                    keywords="recovered",
                    mysteries=[],
                )
            ],
            missing_source_ids=[],
        )

    monkeypatch.setattr("terrismen.services.ingestion.generate_batch_notes", fake_generate_batch_notes)

    continue_document_ingestion(config, document_id)

    failed_connection = connect(config.database_path)
    document_row = failed_connection.execute(
        "SELECT status, error, progress_step_name FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    source_note_counts = failed_connection.execute(
        """
        SELECT sources.locator, COUNT(note_sources.note_id) AS note_count
        FROM sources
        LEFT JOIN note_sources ON note_sources.source_id = sources.id
        WHERE sources.document_id = ?
        GROUP BY sources.id
        ORDER BY sources.id
        """,
        (document_id,),
    ).fetchall()
    malformed_rows = failed_connection.execute(
        "SELECT source_id, locator, error_type, error_detail, raw_response FROM malformed_notes WHERE document_id = ? ORDER BY source_id",
        (document_id,),
    ).fetchall()
    failed_connection.close()

    assert document_row["status"] == "ready"
    assert document_row["progress_step_name"] == "finalizing document"
    assert document_row["error"] == ""
    assert [dict(row) for row in source_note_counts] == [
        {"locator": "Chunk 1", "note_count": 1},
        {"locator": "Chunk 2", "note_count": 0},
        {"locator": "Chunk 3", "note_count": 1},
    ]
    assert [dict(row) for row in malformed_rows] == [
        {
            "source_id": 2,
            "locator": "Chunk 2",
            "error_type": "partial_coverage",
            "error_detail": "The model response omitted this source unit while covering 1/2 source units.",
            "raw_response": "",
        }
    ]

    retry_connection = connect(config.database_path)
    payload = retry_document_ingestion(retry_connection, document_id)
    retry_connection.close()

    assert payload["status"] == "processing"
    assert payload["malformed_note_count"] == 1

    continue_document_ingestion(config, document_id)

    recovered_connection = connect(config.database_path)
    document_row = recovered_connection.execute(
        """
        SELECT status, progress_step_name,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count,
               (SELECT COUNT(*) FROM malformed_notes WHERE document_id = documents.id) AS malformed_note_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    source_note_counts = recovered_connection.execute(
        """
        SELECT sources.locator, COUNT(note_sources.note_id) AS note_count
        FROM sources
        LEFT JOIN note_sources ON note_sources.source_id = sources.id
        WHERE sources.document_id = ?
        GROUP BY sources.id
        ORDER BY sources.id
        """,
        (document_id,),
    ).fetchall()
    recovered_connection.close()

    assert batch_calls == [[1, 2], [3], [2]]
    assert document_row["status"] == "ready"
    assert document_row["progress_step_name"] == "finalizing document"
    assert document_row["note_count"] == 3
    assert document_row["malformed_note_count"] == 0
    assert [dict(row) for row in source_note_counts] == [
        {"locator": "Chunk 1", "note_count": 1},
        {"locator": "Chunk 2", "note_count": 1},
        {"locator": "Chunk 3", "note_count": 1},
    ]


def test_continue_document_ingestion_skips_batch_after_note_generation_exception(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute("UPDATE settings SET document_note_batch_size = ? WHERE id = 1", (2,))
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\ngamma\n",
    )
    connection.close()

    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(
        "terrismen.services.ingestion.parse_document",
        lambda file_path, images_dir: (
            "text",
            [
                ParsedSource(locator="Chunk 1", content="alpha content", page_number=1),
                ParsedSource(locator="Chunk 2", content="beta content", page_number=2),
                ParsedSource(locator="Chunk 3", content="gamma content", page_number=3),
            ],
        ),
    )

    batch_calls: list[list[int]] = []

    def fake_generate_batch_notes(provider, sources):
        batch_calls.append([source.source_id for source in sources])
        if len(batch_calls) == 1:
            raise ProviderError("LLM request timed out after 600.0s")
        return ParsedBatchNotes(
            notes=[
                BatchGeneratedNote(
                    source_ids=[source.source_id for source in sources],
                    note_text="Recovered note",
                    keywords="recovered",
                    mysteries=[],
                )
            ],
            missing_source_ids=[],
        )

    monkeypatch.setattr("terrismen.services.ingestion.generate_batch_notes", fake_generate_batch_notes)

    continue_document_ingestion(config, document_id)

    check_connection = connect(config.database_path)
    document_row = check_connection.execute(
        """
        SELECT status,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count,
               (SELECT COUNT(*) FROM malformed_notes WHERE document_id = documents.id) AS malformed_note_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    malformed_rows = check_connection.execute(
        "SELECT source_id, error_type, error_detail FROM malformed_notes WHERE document_id = ? ORDER BY source_id",
        (document_id,),
    ).fetchall()
    check_connection.close()

    assert batch_calls == [[1, 2], [3]]
    assert document_row["status"] == "ready"
    assert document_row["note_count"] == 1
    assert document_row["malformed_note_count"] == 2
    assert [dict(row) for row in malformed_rows] == [
        {
            "source_id": 1,
            "error_type": "provider_exception",
            "error_detail": "Skipping this source unit after a note-generation error: ProviderError: LLM request timed out after 600.0s",
        },
        {
            "source_id": 2,
            "error_type": "provider_exception",
            "error_detail": "Skipping this source unit after a note-generation error: ProviderError: LLM request timed out after 600.0s",
        },
    ]


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
    connection.execute(
        """
        INSERT INTO malformed_notes (
            document_id, source_id, locator, page_number, error_type, error_detail, raw_response, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'now', 'now')
        """,
        (document_id, source_id, "Page 1", 1, "partial_coverage", "bad note", "{}"),
    )
    connection.commit()

    payload = retry_document_ingestion(connection, document_id)

    row = connection.execute(
        """
        SELECT status, error, kind, progress_step_name, progress_detail,
               (SELECT COUNT(*) FROM sources WHERE document_id = documents.id) AS source_count,
               (SELECT COUNT(*) FROM notes WHERE document_id = documents.id) AS note_count,
               (SELECT COUNT(*) FROM malformed_notes WHERE document_id = documents.id) AS malformed_note_count,
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
    assert row["malformed_note_count"] == 0
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


def test_retry_document_ingestion_allows_ready_documents_with_malformed_notes(tmp_path: Path) -> None:
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
        "UPDATE documents SET kind = ?, status = 'ready', progress_step_name = ?, progress_detail = ?, progress_step_index = ?, error = '' WHERE id = ?",
        ("text", "finalizing document", "", 7, document_id),
    )
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

    payload = retry_document_ingestion(connection, document_id)

    row = connection.execute(
        "SELECT status, progress_step_name, error FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    assert payload["status"] == "processing"
    assert payload["malformed_note_count"] == 1
    assert row["status"] == "processing"
    assert row["progress_step_name"] == "generating notes"
    assert row["error"] == ""
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

    def fake_resolve_mysteries(provider, requests, include_source_excerpts=True):
        seen_questions.extend(str(request.question) for request in requests)
        return [
            MysteryBatchResolution(
                mystery_id=request.mystery_id,
                status="resolved",
                summary="Now resolved",
                note_ids=[note_id],
                source_ids=[source_id],
            )
            for request in requests
        ]

    monkeypatch.setattr("terrismen.services.ingestion.resolve_mysteries", fake_resolve_mysteries)
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


def test_continue_document_ingestion_keeps_processing_after_mystery_resolution_exception(tmp_path: Path, monkeypatch) -> None:
    config = build_config(tmp_path)
    init_db(config.database_path)
    connection = connect(config.database_path)
    configure_provider(connection)
    connection.execute("UPDATE settings SET mystery_resolution_batch_size = ? WHERE id = 1", (1,))
    document_id = create_document_ingestion(
        connection,
        config,
        original_name="notes.txt",
        media_type="text/plain",
        blob=b"alpha\nbeta\n",
    )
    connection.execute(
        "UPDATE documents SET kind = ?, status = 'processing', progress_step_name = ?, progress_detail = ?, progress_step_index = ? WHERE id = ?",
        ("text", "resolving mysteries", "Processing 0/2 mysteries", 6, document_id),
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
    for question in ("open one?", "open two?"):
        connection.execute(
            """
            INSERT INTO unresolved_mysteries (
                document_id, source_id, note_id, question, reason, keywords, status,
                resolution_summary, resolution_note_id, resolution_source_id, created_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'open', '', NULL, NULL, 'now', NULL)
            """,
            (document_id, source_id, note_id, question, "", ""),
        )
    connection.commit()
    connection.close()

    monkeypatch.setattr("terrismen.services.ingestion.build_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(
        "terrismen.services.ingestion.resolve_mysteries",
        lambda provider, requests, include_source_excerpts=True: (_ for _ in ()).throw(
            ProviderError("LLM request timed out after 600.0s")
        ),
    )
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
    document_row = check_connection.execute(
        "SELECT status, progress_step_name FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    mystery_rows = check_connection.execute(
        "SELECT question, status, resolution_summary FROM unresolved_mysteries WHERE document_id = ? ORDER BY id",
        (document_id,),
    ).fetchall()
    check_connection.close()

    assert document_row["status"] == "ready"
    assert document_row["progress_step_name"] == "finalizing document"
    assert [dict(row) for row in mystery_rows] == [
        {
            "question": "open one?",
            "status": "open",
            "resolution_summary": "Mystery resolution was skipped after an LLM error: ProviderError: LLM request timed out after 600.0s",
        },
        {
            "question": "open two?",
            "status": "open",
            "resolution_summary": "Mystery resolution was skipped after an LLM error: ProviderError: LLM request timed out after 600.0s",
        },
    ]


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
        assert str(exc) == "Only failed documents or ready documents with malformed notes can be retried"
    else:
        raise AssertionError("retry_document_ingestion should reject non-failed documents")

    connection.close()


def test_resume_document_ingestion_normalizes_finalizing_stage_for_processing_documents(tmp_path: Path) -> None:
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
        ("text", "finalizing document", "Waiting to wrap up", 7, document_id),
    )
    connection.commit()

    payload = resume_document_ingestion(connection, document_id)

    row = connection.execute(
        "SELECT status, progress_step_name, progress_detail, error FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    assert payload["status"] == "processing"
    assert payload["progress_step_name"] == "resolving mysteries"
    assert payload["progress_detail"] == ""
    assert row["status"] == "processing"
    assert row["progress_step_name"] == "resolving mysteries"
    assert row["progress_detail"] == ""
    assert row["error"] == ""
    connection.close()


def test_resume_document_ingestion_rejects_non_processing_documents(tmp_path: Path) -> None:
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
        "UPDATE documents SET status = 'ready', progress_step_name = ? WHERE id = ?",
        ("finalizing document", document_id),
    )
    connection.commit()

    try:
        resume_document_ingestion(connection, document_id)
    except ValueError as exc:
        assert str(exc) == "Only processing documents can be force resumed"
    else:
        raise AssertionError("resume_document_ingestion should reject non-processing documents")

    connection.close()
