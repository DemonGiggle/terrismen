from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


INITIAL_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        provider_type TEXT NOT NULL DEFAULT '',
        base_url TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        api_key TEXT NOT NULL DEFAULT '',
        temperature REAL NOT NULL DEFAULT 0.2,
        llm_timeout_seconds REAL NOT NULL DEFAULT 600.0,
        mystery_resolution_batch_size INTEGER NOT NULL DEFAULT 5,
        mystery_resolution_reference_mode TEXT NOT NULL DEFAULT 'notes_only'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        locator TEXT NOT NULL,
        page_number INTEGER,
        content TEXT NOT NULL,
        image_summary TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        image_path TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        note TEXT NOT NULL,
        keywords TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS unresolved_mysteries (
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mystery_refs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mystery_id INTEGER NOT NULL REFERENCES unresolved_mysteries(id) ON DELETE CASCADE,
        relation_type TEXT NOT NULL,
        note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
        source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
        ref_rank INTEGER NOT NULL DEFAULT 0,
        why_relevant TEXT NOT NULL DEFAULT '',
        CHECK (note_id IS NOT NULL OR source_id IS NOT NULL)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        citations_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_requests (
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
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
        note,
        keywords,
        content='notes',
        content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(
        content,
        locator,
        content='sources',
        content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS unresolved_mysteries_fts USING fts5(
        question,
        reason,
        keywords,
        resolution_summary,
        content='unresolved_mysteries',
        content_rowid='id'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sources_document_page ON sources(document_id, page_number)",
    "CREATE INDEX IF NOT EXISTS idx_notes_document_source ON notes(document_id, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_mysteries_document_status ON unresolved_mysteries(document_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_mysteries_source_note ON unresolved_mysteries(source_id, note_id)",
    "CREATE INDEX IF NOT EXISTS idx_mystery_refs_mystery_rank ON mystery_refs(mystery_id, relation_type, ref_rank)",
    "CREATE INDEX IF NOT EXISTS idx_mystery_refs_note ON mystery_refs(note_id)",
    "CREATE INDEX IF NOT EXISTS idx_mystery_refs_source ON mystery_refs(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_requests_created_at ON chat_requests(created_at)",
    """
    CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
        INSERT INTO notes_fts(rowid, note, keywords) VALUES (new.id, new.note, new.keywords);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
        INSERT INTO notes_fts(notes_fts, rowid, note, keywords) VALUES ('delete', old.id, old.note, old.keywords);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
        INSERT INTO notes_fts(notes_fts, rowid, note, keywords) VALUES ('delete', old.id, old.note, old.keywords);
        INSERT INTO notes_fts(rowid, note, keywords) VALUES (new.id, new.note, new.keywords);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sources_ai AFTER INSERT ON sources BEGIN
        INSERT INTO sources_fts(rowid, content, locator) VALUES (new.id, new.content, new.locator);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sources_ad AFTER DELETE ON sources BEGIN
        INSERT INTO sources_fts(sources_fts, rowid, content, locator) VALUES ('delete', old.id, old.content, old.locator);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sources_au AFTER UPDATE ON sources BEGIN
        INSERT INTO sources_fts(sources_fts, rowid, content, locator) VALUES ('delete', old.id, old.content, old.locator);
        INSERT INTO sources_fts(rowid, content, locator) VALUES (new.id, new.content, new.locator);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS unresolved_mysteries_ai AFTER INSERT ON unresolved_mysteries BEGIN
        INSERT INTO unresolved_mysteries_fts(rowid, question, reason, keywords, resolution_summary)
        VALUES (new.id, new.question, new.reason, new.keywords, new.resolution_summary);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS unresolved_mysteries_ad AFTER DELETE ON unresolved_mysteries BEGIN
        INSERT INTO unresolved_mysteries_fts(unresolved_mysteries_fts, rowid, question, reason, keywords, resolution_summary)
        VALUES ('delete', old.id, old.question, old.reason, old.keywords, old.resolution_summary);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS unresolved_mysteries_au AFTER UPDATE ON unresolved_mysteries BEGIN
        INSERT INTO unresolved_mysteries_fts(unresolved_mysteries_fts, rowid, question, reason, keywords, resolution_summary)
        VALUES ('delete', old.id, old.question, old.reason, old.keywords, old.resolution_summary);
        INSERT INTO unresolved_mysteries_fts(rowid, question, reason, keywords, resolution_summary)
        VALUES (new.id, new.question, new.reason, new.keywords, new.resolution_summary);
    END
    """,
)

SUPPORTED_BASELINE_COLUMNS = {
    "settings": {
        "id",
        "provider_type",
        "base_url",
        "model",
        "api_key",
        "temperature",
        "llm_timeout_seconds",
        "mystery_resolution_batch_size",
        "mystery_resolution_reference_mode",
    },
    "documents": {
        "id",
        "original_name",
        "stored_path",
        "media_type",
        "kind",
        "status",
        "progress_step_name",
        "progress_detail",
        "progress_step_index",
        "progress_step_count",
        "error",
        "created_at",
    },
    "sources": {
        "id",
        "document_id",
        "locator",
        "page_number",
        "content",
        "image_summary",
        "metadata_json",
        "created_at",
    },
    "source_images": {"id", "source_id", "image_path", "mime_type", "description"},
    "notes": {"id", "document_id", "source_id", "note", "keywords", "created_at"},
    "unresolved_mysteries": {
        "id",
        "document_id",
        "source_id",
        "note_id",
        "question",
        "reason",
        "keywords",
        "status",
        "resolution_summary",
        "resolution_note_id",
        "resolution_source_id",
        "created_at",
        "resolved_at",
    },
    "mystery_refs": {"id", "mystery_id", "relation_type", "note_id", "source_id", "ref_rank", "why_relevant"},
    "messages": {"id", "role", "content", "citations_json", "created_at"},
    "chat_requests": {
        "id",
        "question",
        "selected_document_ids_json",
        "status",
        "progress_step_name",
        "progress_step_index",
        "progress_step_count",
        "error",
        "user_message_id",
        "assistant_message_id",
        "created_at",
        "completed_at",
    },
}

FTS_TABLES = ("notes_fts", "sources_fts", "unresolved_mysteries_fts")
BASELINE_SCHEMA_VERSION = 1
Migration = Callable[[sqlite3.Connection], None]


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(database_path) as connection:
        _run_migrations(connection)


def _run_migrations(connection: sqlite3.Connection) -> None:
    current_version = _get_user_version(connection)
    latest_version = max(MIGRATIONS)

    if current_version > latest_version:
        raise RuntimeError(
            f"Database schema version {current_version} is newer than this app supports ({latest_version})"
        )

    if current_version == 0:
        if _has_user_schema(connection):
            if not _matches_supported_legacy_baseline(connection):
                raise RuntimeError(
                    "Unsupported database schema without migration metadata. "
                    "Only databases matching the current pre-migration schema can be baselined automatically."
                )
            _baseline_supported_legacy_database(connection)
            current_version = _get_user_version(connection)
        else:
            _apply_migration(connection, BASELINE_SCHEMA_VERSION)
            current_version = BASELINE_SCHEMA_VERSION

    while current_version < latest_version:
        next_version = current_version + 1
        _apply_migration(connection, next_version)
        current_version = next_version


def _get_user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0] if row is not None else 0)


def _has_user_schema(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _matches_supported_legacy_baseline(connection: sqlite3.Connection) -> bool:
    for table_name, expected_columns in SUPPORTED_BASELINE_COLUMNS.items():
        actual_columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")}
        if not expected_columns.issubset(actual_columns):
            return False
    return True


def _baseline_supported_legacy_database(connection: sqlite3.Connection) -> None:
    _run_in_transaction(connection, lambda: _apply_baseline_migration(connection))


def _apply_baseline_migration(connection: sqlite3.Connection) -> None:
    _migration_0001_initial_schema(connection)
    _rebuild_fts_tables(connection)
    connection.execute(f"PRAGMA user_version = {BASELINE_SCHEMA_VERSION}")


def _apply_migration(connection: sqlite3.Connection, version: int) -> None:
    migration = MIGRATIONS.get(version)
    if migration is None:
        raise RuntimeError(f"Missing migration {version}")
    _run_in_transaction(connection, lambda: _apply_versioned_migration(connection, version, migration))


def _apply_versioned_migration(connection: sqlite3.Connection, version: int, migration: Migration) -> None:
    migration(connection)
    connection.execute(f"PRAGMA user_version = {version}")


def _run_in_transaction(connection: sqlite3.Connection, callback: Callable[[], None]) -> None:
    connection.execute("BEGIN")
    try:
        callback()
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def _migration_0001_initial_schema(connection: sqlite3.Connection) -> None:
    for statement in INITIAL_SCHEMA_STATEMENTS:
        connection.execute(statement)
    _ensure_settings_row(connection)


def _ensure_settings_row(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO settings (
            id, provider_type, base_url, model, api_key, temperature, llm_timeout_seconds,
            mystery_resolution_batch_size, mystery_resolution_reference_mode
        )
        VALUES (1, '', '', '', '', 0.2, 600.0, 5, 'notes_only')
        ON CONFLICT(id) DO NOTHING
        """
    )


def _rebuild_fts_tables(connection: sqlite3.Connection) -> None:
    for table_name in FTS_TABLES:
        connection.execute(f"INSERT INTO {table_name}({table_name}) VALUES ('rebuild')")


def _migration_0002_add_document_note_batch_size(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE settings
        ADD COLUMN document_note_batch_size INTEGER NOT NULL DEFAULT 5
        """
    )


def _create_note_sources_schema(connection: sqlite3.Connection) -> None:
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
        "CREATE INDEX IF NOT EXISTS idx_note_sources_note_rank ON note_sources(note_id, ref_rank)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_note_sources_source_note ON note_sources(source_id, note_id)"
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS note_sources_notes_ai AFTER INSERT ON notes BEGIN
            INSERT OR IGNORE INTO note_sources (note_id, source_id, ref_rank)
            VALUES (new.id, new.source_id, 1);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS note_sources_notes_au AFTER UPDATE OF source_id ON notes BEGIN
            DELETE FROM note_sources
            WHERE note_id = old.id AND source_id = old.source_id;
            INSERT OR IGNORE INTO note_sources (note_id, source_id, ref_rank)
            VALUES (new.id, new.source_id, 1);
            UPDATE note_sources
            SET ref_rank = 1
            WHERE note_id = new.id AND source_id = new.source_id;
        END
        """
    )


def _migration_0003_add_note_sources(connection: sqlite3.Connection) -> None:
    _create_note_sources_schema(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO note_sources (note_id, source_id, ref_rank)
        SELECT id, source_id, 1
        FROM notes
        """
    )


def _migration_0004_add_malformed_notes(connection: sqlite3.Connection) -> None:
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
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_malformed_notes_document_source ON malformed_notes(document_id, source_id)"
    )


def _migration_0005_add_think_level(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE settings
        ADD COLUMN think_level TEXT NOT NULL DEFAULT 'off'
        """
    )


def _migration_0006_split_think_level_by_workflow(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE settings
        ADD COLUMN ingestion_think_level TEXT NOT NULL DEFAULT 'off'
        """
    )
    connection.execute(
        """
        ALTER TABLE settings
        ADD COLUMN chat_think_level TEXT NOT NULL DEFAULT 'off'
        """
    )
    connection.execute(
        """
        UPDATE settings
        SET ingestion_think_level = think_level,
            chat_think_level = think_level
        WHERE id = 1
        """
    )


MIGRATIONS: dict[int, Migration] = {
    1: _migration_0001_initial_schema,
    2: _migration_0002_add_document_note_batch_size,
    3: _migration_0003_add_note_sources,
    4: _migration_0004_add_malformed_notes,
    5: _migration_0005_add_think_level,
    6: _migration_0006_split_think_level_by_workflow,
}

LATEST_SCHEMA_VERSION = max(MIGRATIONS)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in ("citations_json", "metadata_json", "selected_document_ids_json"):
        if field in result and isinstance(result[field], str):
            result[field] = json.loads(result[field])
    return result
