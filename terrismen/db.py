from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

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
);

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
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    locator TEXT NOT NULL,
    page_number INTEGER,
    content TEXT NOT NULL,
    image_summary TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

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
);

CREATE TABLE IF NOT EXISTS mystery_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mystery_id INTEGER NOT NULL REFERENCES unresolved_mysteries(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
    source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    ref_rank INTEGER NOT NULL DEFAULT 0,
    why_relevant TEXT NOT NULL DEFAULT '',
    CHECK (note_id IS NOT NULL OR source_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

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
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    note,
    keywords,
    content='notes',
    content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(
    content,
    locator,
    content='sources',
    content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS unresolved_mysteries_fts USING fts5(
    question,
    reason,
    keywords,
    resolution_summary,
    content='unresolved_mysteries',
    content_rowid='id'
);

CREATE INDEX IF NOT EXISTS idx_sources_document_page ON sources(document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_notes_document_source ON notes(document_id, source_id);
CREATE INDEX IF NOT EXISTS idx_mysteries_document_status ON unresolved_mysteries(document_id, status);
CREATE INDEX IF NOT EXISTS idx_mysteries_source_note ON unresolved_mysteries(source_id, note_id);
CREATE INDEX IF NOT EXISTS idx_mystery_refs_mystery_rank ON mystery_refs(mystery_id, relation_type, ref_rank);
CREATE INDEX IF NOT EXISTS idx_mystery_refs_note ON mystery_refs(note_id);
CREATE INDEX IF NOT EXISTS idx_mystery_refs_source ON mystery_refs(source_id);
CREATE INDEX IF NOT EXISTS idx_chat_requests_created_at ON chat_requests(created_at);


CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, note, keywords) VALUES (new.id, new.note, new.keywords);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, note, keywords) VALUES ('delete', old.id, old.note, old.keywords);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, note, keywords) VALUES ('delete', old.id, old.note, old.keywords);
    INSERT INTO notes_fts(rowid, note, keywords) VALUES (new.id, new.note, new.keywords);
END;

CREATE TRIGGER IF NOT EXISTS sources_ai AFTER INSERT ON sources BEGIN
    INSERT INTO sources_fts(rowid, content, locator) VALUES (new.id, new.content, new.locator);
END;

CREATE TRIGGER IF NOT EXISTS sources_ad AFTER DELETE ON sources BEGIN
    INSERT INTO sources_fts(sources_fts, rowid, content, locator) VALUES ('delete', old.id, old.content, old.locator);
END;

CREATE TRIGGER IF NOT EXISTS sources_au AFTER UPDATE ON sources BEGIN
    INSERT INTO sources_fts(sources_fts, rowid, content, locator) VALUES ('delete', old.id, old.content, old.locator);
    INSERT INTO sources_fts(rowid, content, locator) VALUES (new.id, new.content, new.locator);
END;

CREATE TRIGGER IF NOT EXISTS unresolved_mysteries_ai AFTER INSERT ON unresolved_mysteries BEGIN
    INSERT INTO unresolved_mysteries_fts(rowid, question, reason, keywords, resolution_summary)
    VALUES (new.id, new.question, new.reason, new.keywords, new.resolution_summary);
END;

CREATE TRIGGER IF NOT EXISTS unresolved_mysteries_ad AFTER DELETE ON unresolved_mysteries BEGIN
    INSERT INTO unresolved_mysteries_fts(unresolved_mysteries_fts, rowid, question, reason, keywords, resolution_summary)
    VALUES ('delete', old.id, old.question, old.reason, old.keywords, old.resolution_summary);
END;

CREATE TRIGGER IF NOT EXISTS unresolved_mysteries_au AFTER UPDATE ON unresolved_mysteries BEGIN
    INSERT INTO unresolved_mysteries_fts(unresolved_mysteries_fts, rowid, question, reason, keywords, resolution_summary)
    VALUES ('delete', old.id, old.question, old.reason, old.keywords, old.resolution_summary);
    INSERT INTO unresolved_mysteries_fts(rowid, question, reason, keywords, resolution_summary)
    VALUES (new.id, new.question, new.reason, new.keywords, new.resolution_summary);
END;
"""


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
        connection.executescript(SCHEMA)
        _ensure_column(connection, "documents", "progress_step_name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "documents", "progress_detail", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "documents", "progress_step_index", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "documents", "progress_step_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "settings", "llm_timeout_seconds", "REAL NOT NULL DEFAULT 600.0")
        _ensure_column(connection, "settings", "mystery_resolution_batch_size", "INTEGER NOT NULL DEFAULT 5")
        _ensure_column(
            connection,
            "settings",
            "mystery_resolution_reference_mode",
            "TEXT NOT NULL DEFAULT 'notes_only'",
        )
        _ensure_column(connection, "chat_requests", "selected_document_ids_json", "TEXT NOT NULL DEFAULT '[]'")
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
        connection.commit()


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_definition: str) -> None:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(row["name"] == column_name for row in rows):
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in ("citations_json", "metadata_json", "selected_document_ids_json"):
        if field in result and isinstance(result[field], str):
            result[field] = json.loads(result[field])
    return result
