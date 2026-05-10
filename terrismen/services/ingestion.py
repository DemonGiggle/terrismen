from __future__ import annotations

import json
import sqlite3
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Final

from terrismen.config import AppConfig
from terrismen.db import connect, utcnow
from terrismen.llm import ProviderSettings, build_provider
from terrismen.llm.base import ProviderError
from terrismen.services.notes import MysteryDraft, describe_images, generate_note, resolve_mystery
from terrismen.services.parsers import ParserError, parse_document
from terrismen.services.retrieval import build_fts_query

INGESTION_STEPS: Final[tuple[str, ...]] = (
    "validating provider/settings",
    "storing upload",
    "parsing document",
    "describing images",
    "generating notes",
    "resolving mysteries",
    "finalizing document",
)


def file_extension(name: str) -> str:
    return Path(name).suffix.lower()


def allowed_extension(name: str) -> bool:
    return file_extension(name) in {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md", ".text"}


def load_provider_settings(connection: sqlite3.Connection) -> ProviderSettings:
    row = connection.execute(
        "SELECT provider_type, base_url, model, api_key, temperature, llm_timeout_seconds FROM settings WHERE id = 1"
    ).fetchone()
    return ProviderSettings(
        provider_type=row["provider_type"],
        base_url=row["base_url"],
        model=row["model"],
        api_key=row["api_key"],
        temperature=row["temperature"],
        llm_timeout_seconds=row["llm_timeout_seconds"],
    )


def update_document_progress(connection: sqlite3.Connection, document_id: int, step_name: str) -> None:
    connection.execute(
        """
        UPDATE documents
        SET progress_step_name = ?, progress_step_index = ?, progress_step_count = ?
        WHERE id = ?
        """,
        (step_name, INGESTION_STEPS.index(step_name) + 1, len(INGESTION_STEPS), document_id),
    )


def _insert_mystery(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    source_id: int,
    note_id: int,
    draft: MysteryDraft,
) -> None:
    connection.execute(
        """
        INSERT INTO unresolved_mysteries (
            document_id, source_id, note_id, question, reason, keywords, status, resolution_summary, created_at, resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'open', '', ?, NULL)
        """,
        (document_id, source_id, note_id, draft.question, draft.reason, draft.keywords, utcnow()),
    )


def _search_mystery_candidates(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    search_text: str,
    limit: int = 8,
) -> list[dict[str, object]]:
    fts_query = build_fts_query(search_text)
    rows: list[sqlite3.Row]
    if fts_query:
        rows = connection.execute(
            """
            SELECT notes.id AS note_id, notes.source_id, notes.note, notes.keywords, sources.content, sources.locator, sources.page_number
            FROM notes_fts
            JOIN notes ON notes_fts.rowid = notes.id
            JOIN sources ON sources.id = notes.source_id
            WHERE notes.document_id = ? AND notes_fts MATCH ?
            ORDER BY bm25(notes_fts)
            LIMIT ?
            """,
            (document_id, fts_query, limit),
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]

    like_pattern = f"%{search_text[:160]}%"
    rows = connection.execute(
        """
        SELECT notes.id AS note_id, notes.source_id, notes.note, notes.keywords, sources.content, sources.locator, sources.page_number
        FROM notes
        JOIN sources ON sources.id = notes.source_id
        WHERE notes.document_id = ?
          AND (notes.note LIKE ? OR notes.keywords LIKE ? OR sources.content LIKE ?)
        ORDER BY notes.id DESC
        LIMIT ?
        """,
        (document_id, like_pattern, like_pattern, like_pattern, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _replace_resolution_refs(
    connection: sqlite3.Connection,
    *,
    mystery_id: int,
    note_ids: list[int],
    source_ids: list[int],
) -> None:
    connection.execute(
        "DELETE FROM mystery_refs WHERE mystery_id = ? AND relation_type IN ('resolution_note', 'resolution_source')",
        (mystery_id,),
    )
    for ref_rank, note_id in enumerate(note_ids, start=1):
        connection.execute(
            """
            INSERT INTO mystery_refs (mystery_id, relation_type, note_id, source_id, ref_rank, why_relevant)
            VALUES (?, 'resolution_note', ?, NULL, ?, '')
            """,
            (mystery_id, note_id, ref_rank),
        )
    for ref_rank, source_id in enumerate(source_ids, start=1):
        connection.execute(
            """
            INSERT INTO mystery_refs (mystery_id, relation_type, note_id, source_id, ref_rank, why_relevant)
            VALUES (?, 'resolution_source', NULL, ?, ?, '')
            """,
            (mystery_id, source_id, ref_rank),
        )


def _resolve_document_mysteries(
    connection: sqlite3.Connection,
    *,
    provider,
    document_id: int,
    document_name: str,
) -> None:
    mysteries = connection.execute(
        """
        SELECT unresolved_mysteries.id, unresolved_mysteries.question, unresolved_mysteries.reason, unresolved_mysteries.keywords,
               unresolved_mysteries.note_id, unresolved_mysteries.source_id, sources.locator AS origin_locator,
               sources.page_number AS origin_page_number
        FROM unresolved_mysteries
        JOIN sources ON sources.id = unresolved_mysteries.source_id
        WHERE unresolved_mysteries.document_id = ?
        ORDER BY unresolved_mysteries.id
        """,
        (document_id,),
    ).fetchall()

    for mystery_row in mysteries:
        mystery = dict(mystery_row)
        search_text = " ".join(
            item for item in [mystery["question"], mystery["reason"], mystery["keywords"]] if item
        ).strip()
        candidates = _search_mystery_candidates(connection, document_id=document_id, search_text=search_text)
        if not candidates:
            connection.execute(
                """
                UPDATE unresolved_mysteries
                SET status = 'open', resolution_summary = 'No matching notes or sources were strong enough to resolve this mystery.',
                    resolution_note_id = NULL, resolution_source_id = NULL, resolved_at = NULL
                WHERE id = ?
                """,
                (mystery["id"],),
            )
            continue

        resolution = resolve_mystery(provider, document_name, mystery, candidates)
        candidate_note_map = {int(candidate["note_id"]): candidate for candidate in candidates if candidate["note_id"] is not None}
        candidate_source_map = {
            int(candidate["source_id"]): candidate for candidate in candidates if candidate["source_id"] is not None
        }

        note_ids = [note_id for note_id in resolution.note_ids if note_id in candidate_note_map]
        source_ids = [source_id for source_id in resolution.source_ids if source_id in candidate_source_map]
        for note_id in note_ids:
            source_id = int(candidate_note_map[note_id]["source_id"])
            if source_id not in source_ids:
                source_ids.append(source_id)

        note_ids = list(OrderedDict.fromkeys(note_ids))
        source_ids = list(OrderedDict.fromkeys(source_ids))
        primary_note_id = note_ids[0] if note_ids else None
        primary_source_id = source_ids[0] if source_ids else None
        resolved = resolution.status == "resolved" and (primary_note_id is not None or primary_source_id is not None)
        summary = resolution.summary.strip()
        if not summary and not resolved:
            summary = "The document still does not provide enough evidence to resolve this mystery."
        if not summary and resolved:
            summary = "This mystery was resolved from later notes and source excerpts."

        connection.execute(
            """
            UPDATE unresolved_mysteries
            SET status = ?, resolution_summary = ?, resolution_note_id = ?, resolution_source_id = ?, resolved_at = ?
            WHERE id = ?
            """,
            (
                "resolved" if resolved else "open",
                summary,
                primary_note_id,
                primary_source_id,
                utcnow() if resolved else None,
                mystery["id"],
            ),
        )
        _replace_resolution_refs(
            connection,
            mystery_id=int(mystery["id"]),
            note_ids=note_ids,
            source_ids=source_ids,
        )


def create_document_ingestion(
    connection: sqlite3.Connection,
    config: AppConfig,
    *,
    original_name: str,
    media_type: str,
    blob: bytes,
) -> int:
    settings = load_provider_settings(connection)
    if not settings.is_configured():
        raise ProviderError("Configure an OpenAI-compatible or Ollama provider before uploading documents.")
    if not allowed_extension(original_name):
        raise ParserError("Unsupported document type. Upload PDF, DOCX, DOC, XLSX, XLS, TXT, or Markdown.")

    stored_name = f"{uuid.uuid4().hex}{Path(original_name).suffix.lower()}"
    stored_path = config.uploads_dir / stored_name
    stored_path.write_bytes(blob)

    cursor = connection.execute(
        """
        INSERT INTO documents (
            original_name, stored_path, media_type, kind, status,
            progress_step_name, progress_step_index, progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, '', 'processing', ?, ?, ?, '', ?)
        """,
        (
            original_name,
            str(stored_path),
            media_type or "application/octet-stream",
            "parsing document",
            INGESTION_STEPS.index("parsing document") + 1,
            len(INGESTION_STEPS),
            utcnow(),
        ),
    )
    document_id = int(cursor.lastrowid)
    connection.commit()
    return document_id


def continue_document_ingestion(config: AppConfig, document_id: int) -> None:
    with connect(config.database_path) as connection:
        _continue_document_ingestion(connection, config, document_id=document_id)


def _continue_document_ingestion(connection: sqlite3.Connection, config: AppConfig, *, document_id: int) -> None:
    try:
        document_row = connection.execute(
            """
            SELECT id, original_name, stored_path, media_type
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
        if document_row is None:
            return
        original_name = document_row["original_name"]
        stored_path = Path(document_row["stored_path"])
        settings = load_provider_settings(connection)
        update_document_progress(connection, document_id, "parsing document")
        connection.commit()
        kind, parsed_sources = parse_document(stored_path, config.images_dir)
        update_document_progress(connection, document_id, "describing images")
        connection.commit()
        provider = build_provider(settings)
        connection.execute("UPDATE documents SET kind = ? WHERE id = ?", (kind, document_id))
        update_document_progress(connection, document_id, "generating notes")
        connection.commit()

        for source in parsed_sources:
            source_cursor = connection.execute(
                """
                INSERT INTO sources (document_id, locator, page_number, content, image_summary, metadata_json, created_at)
                VALUES (?, ?, ?, ?, '', ?, ?)
                """,
                (
                    document_id,
                    source.locator,
                    source.page_number,
                    source.content,
                    json.dumps(source.metadata),
                    utcnow(),
                ),
            )
            source_id = int(source_cursor.lastrowid)
            image_descriptions = describe_images(provider, source) if source.images else []
            for (image_path, image), description in zip(source.images, image_descriptions, strict=False):
                connection.execute(
                    """
                    INSERT INTO source_images (source_id, image_path, mime_type, description)
                    VALUES (?, ?, ?, ?)
                    """,
                    (source_id, str(image_path), image.mime_type, description),
                )
            if image_descriptions:
                connection.execute(
                    "UPDATE sources SET image_summary = ? WHERE id = ?",
                    ("\n".join(image_descriptions), source_id),
                )
            generated_note = generate_note(provider, original_name, source, image_descriptions)
            note_cursor = connection.execute(
                """
                INSERT INTO notes (document_id, source_id, note, keywords, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, source_id, generated_note.note_text, generated_note.keywords, utcnow()),
            )
            note_id = int(note_cursor.lastrowid)
            for mystery in generated_note.mysteries:
                _insert_mystery(
                    connection,
                    document_id=document_id,
                    source_id=source_id,
                    note_id=note_id,
                    draft=mystery,
                )

        update_document_progress(connection, document_id, "resolving mysteries")
        connection.commit()
        _resolve_document_mysteries(connection, provider=provider, document_id=document_id, document_name=original_name)
        update_document_progress(connection, document_id, "finalizing document")
        connection.execute(
            "UPDATE documents SET status = 'ready', error = '', progress_step_name = ?, progress_step_index = ?, progress_step_count = ? WHERE id = ?",
            ("finalizing document", len(INGESTION_STEPS), len(INGESTION_STEPS), document_id),
        )
        connection.commit()
    except Exception as exc:
        connection.execute("UPDATE documents SET status = 'failed', error = ? WHERE id = ?", (str(exc), document_id))
        connection.commit()
