from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from terrismen.db import BASELINE_SCHEMA_VERSION, LATEST_SCHEMA_VERSION, connect, init_db


def get_user_version(database_path: Path) -> int:
    with connect(database_path) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0] if row is not None else 0)


def build_current_shape_database_without_metadata(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                provider_type TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                api_key TEXT NOT NULL DEFAULT '',
                temperature REAL NOT NULL DEFAULT 0.2,
                llm_timeout_seconds REAL NOT NULL DEFAULT 600.0,
                mystery_resolution_batch_size INTEGER NOT NULL DEFAULT 5,
                mystery_resolution_reference_mode TEXT NOT NULL DEFAULT 'notes_only'
            );

            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                media_type TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                progress_step_name TEXT NOT NULL DEFAULT '',
                progress_detail TEXT NOT NULL DEFAULT '',
                progress_step_index INTEGER NOT NULL DEFAULT 0,
                progress_step_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                page_number INTEGER,
                content TEXT NOT NULL,
                image_summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE source_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                image_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                note TEXT NOT NULL,
                keywords TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE unresolved_mysteries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                question TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                keywords TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                resolution_summary TEXT NOT NULL DEFAULT '',
                resolution_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
                resolution_source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE TABLE mystery_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mystery_id INTEGER NOT NULL REFERENCES unresolved_mysteries(id) ON DELETE CASCADE,
                relation_type TEXT NOT NULL,
                note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
                source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
                ref_rank INTEGER NOT NULL DEFAULT 0,
                why_relevant TEXT NOT NULL DEFAULT '',
                CHECK (note_id IS NOT NULL OR source_id IS NOT NULL)
            );

            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                citations_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE chat_requests (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                selected_document_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'processing',
                progress_step_name TEXT NOT NULL DEFAULT '',
                progress_step_index INTEGER NOT NULL DEFAULT 0,
                progress_step_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                user_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                assistant_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO settings (
                id, provider_type, base_url, model, api_key, temperature, llm_timeout_seconds,
                mystery_resolution_batch_size, mystery_resolution_reference_mode
            )
            VALUES (1, '', '', '', '', 0.2, 600.0, 5, 'notes_only')
            """
        )
        document_id = connection.execute(
            """
            INSERT INTO documents (
                original_name, stored_path, media_type, kind, status, progress_step_name, progress_detail,
                progress_step_index, progress_step_count, error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy.txt", "/tmp/legacy.txt", "text/plain", "text", "ready", "", "", 0, 0, "", "now"),
        ).lastrowid
        source_id = connection.execute(
            """
            INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, "1", 1, "legacy source", "", "{}", "now"),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO notes (document_id, source_id, note, keywords, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (document_id, source_id, "legacy note body", "legacykeyword", "now"),
        )
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
    finally:
        connection.close()


def build_version_1_database_with_metadata(database_path: Path) -> None:
    build_current_shape_database_without_metadata(database_path)
    with connect(database_path) as connection:
        connection.execute(f"PRAGMA user_version = {BASELINE_SCHEMA_VERSION}")
        connection.commit()


def build_version_2_database_with_metadata(database_path: Path) -> None:
    build_version_1_database_with_metadata(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            ALTER TABLE settings
            ADD COLUMN document_note_batch_size INTEGER NOT NULL DEFAULT 5
            """
        )
        connection.execute("PRAGMA user_version = 2")
        connection.commit()


def build_version_4_database_with_metadata(database_path: Path) -> None:
    build_version_2_database_with_metadata(database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS note_sources (
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                ref_rank INTEGER NOT NULL,
                PRIMARY KEY (note_id, source_id),
                CHECK (ref_rank >= 1)
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO note_sources (note_id, source_id, ref_rank)
            SELECT id, source_id, 1
            FROM notes
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS malformed_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                page_number INTEGER,
                error_type TEXT NOT NULL DEFAULT '',
                error_detail TEXT NOT NULL DEFAULT '',
                raw_response TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_id)
            )
            """
        )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()


def test_init_db_sets_user_version_for_fresh_database(tmp_path) -> None:
    database_path = tmp_path / "fresh.sqlite3"

    init_db(database_path)

    assert get_user_version(database_path) == LATEST_SCHEMA_VERSION
    with connect(database_path) as connection:
        settings_count = connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
        settings_row = connection.execute(
            "SELECT document_note_batch_size, think_level FROM settings WHERE id = 1"
        ).fetchone()
        malformed_notes_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'malformed_notes'"
        ).fetchone()

    assert settings_count == 1
    assert settings_row["document_note_batch_size"] == 5
    assert settings_row["think_level"] == "off"
    assert malformed_notes_exists is not None


def test_init_db_is_idempotent_for_latest_database(tmp_path) -> None:
    database_path = tmp_path / "idempotent.sqlite3"

    init_db(database_path)
    init_db(database_path)

    assert get_user_version(database_path) == LATEST_SCHEMA_VERSION
    with connect(database_path) as connection:
        settings_count = connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0]

    assert settings_count == 1


def test_init_db_baselines_current_schema_without_metadata(tmp_path) -> None:
    database_path = tmp_path / "legacy-current.sqlite3"
    build_current_shape_database_without_metadata(database_path)

    init_db(database_path)

    assert get_user_version(database_path) == LATEST_SCHEMA_VERSION
    with connect(database_path) as connection:
        rebuilt_hits = connection.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
            ("legacykeyword",),
        ).fetchall()
        document_note_batch_size = connection.execute(
            "SELECT document_note_batch_size, think_level FROM settings WHERE id = 1"
        ).fetchone()
        connection.execute(
            """
            INSERT INTO notes (document_id, source_id, note, keywords, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, 1, "newly inserted note", "postbaseline", "later"),
        )
        connection.commit()
        trigger_hits = connection.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?",
            ("postbaseline",),
        ).fetchall()

    assert len(rebuilt_hits) == 1
    assert len(trigger_hits) == 1
    assert document_note_batch_size["document_note_batch_size"] == 5
    assert document_note_batch_size["think_level"] == "off"


def test_init_db_migrates_version_1_database_to_add_document_note_batch_size(tmp_path) -> None:
    database_path = tmp_path / "version1.sqlite3"
    build_version_1_database_with_metadata(database_path)

    init_db(database_path)

    assert get_user_version(database_path) == LATEST_SCHEMA_VERSION
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT document_note_batch_size, mystery_resolution_batch_size, mystery_resolution_reference_mode, think_level
            FROM settings
            WHERE id = 1
            """
        ).fetchone()

    assert row["document_note_batch_size"] == 5
    assert row["mystery_resolution_batch_size"] == 5
    assert row["mystery_resolution_reference_mode"] == "notes_only"
    assert row["think_level"] == "off"


def test_init_db_migrates_version_4_database_to_add_think_level(tmp_path) -> None:
    database_path = tmp_path / "version4.sqlite3"
    build_version_4_database_with_metadata(database_path)

    init_db(database_path)

    assert get_user_version(database_path) == LATEST_SCHEMA_VERSION
    with connect(database_path) as connection:
        row = connection.execute("SELECT think_level FROM settings WHERE id = 1").fetchone()

    assert row["think_level"] == "off"


def test_init_db_migrates_version_2_database_to_add_note_sources_and_backfill(tmp_path) -> None:
    database_path = tmp_path / "version2.sqlite3"
    build_version_2_database_with_metadata(database_path)

    init_db(database_path)

    assert get_user_version(database_path) == LATEST_SCHEMA_VERSION
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT note_id, source_id, ref_rank FROM note_sources ORDER BY note_id, source_id"
        ).fetchall()

    assert [dict(row) for row in rows] == [{"note_id": 1, "source_id": 1, "ref_rank": 1}]


def test_init_db_creates_note_sources_triggers_for_new_note_inserts_and_source_updates(tmp_path) -> None:
    database_path = tmp_path / "note-sources-sync.sqlite3"
    init_db(database_path)

    with connect(database_path) as connection:
        document_id = connection.execute(
            """
            INSERT INTO documents (
                original_name, stored_path, media_type, kind, status, progress_step_name, progress_detail,
                progress_step_index, progress_step_count, error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("notes.txt", "/tmp/notes.txt", "text/plain", "text", "ready", "", "", 0, 0, "", "now"),
        ).lastrowid
        source_id = connection.execute(
            """
            INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, "Chunk 1", 1, "alpha", "", "{}", "now"),
        ).lastrowid
        replacement_source_id = connection.execute(
            """
            INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, "Chunk 2", 2, "beta", "", "{}", "now"),
        ).lastrowid
        note_id = connection.execute(
            """
            INSERT INTO notes (document_id, source_id, note, keywords, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (document_id, source_id, "note body", "keywords", "now"),
        ).lastrowid
        connection.execute("UPDATE notes SET source_id = ? WHERE id = ?", (replacement_source_id, note_id))
        connection.commit()
        rows = connection.execute(
            "SELECT note_id, source_id, ref_rank FROM note_sources WHERE note_id = ? ORDER BY ref_rank, source_id",
            (note_id,),
        ).fetchall()

    assert [dict(row) for row in rows] == [{"note_id": note_id, "source_id": replacement_source_id, "ref_rank": 1}]


def test_init_db_rejects_unsupported_legacy_schema(tmp_path) -> None:
    database_path = tmp_path / "unsupported.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                provider_type TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                api_key TEXT NOT NULL DEFAULT '',
                temperature REAL NOT NULL DEFAULT 0.2
            );
            PRAGMA user_version = 0;
            """
        )

    with pytest.raises(RuntimeError, match="Unsupported database schema without migration metadata"):
        init_db(database_path)


def test_init_db_rejects_newer_database_schema_version(tmp_path) -> None:
    database_path = tmp_path / "newer.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 999")
        connection.commit()

    with pytest.raises(RuntimeError, match="newer than this app supports"):
        init_db(database_path)
