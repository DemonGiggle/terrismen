from __future__ import annotations

import json
import sqlite3
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from terrismen.config import AppConfig
from terrismen.db import connect, utcnow
from terrismen.llm import ProviderSettings, build_provider
from terrismen.llm.base import ProviderError
from terrismen.services.notes import (
    BatchNoteSourceInput,
    MysteryDraft,
    MysteryResolutionRequest,
    build_reference_label,
    build_mystery_resolution_request,
    describe_images,
    generate_batch_notes,
    resolve_mysteries,
    resolve_mystery,
)
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
DEFAULT_MYSTERY_RESOLUTION_BATCH_SIZE = 5
MAX_MYSTERY_RESOLUTION_BATCH_SIZE = 20
DEFAULT_DOCUMENT_NOTE_BATCH_SIZE = 5
MAX_DOCUMENT_NOTE_BATCH_SIZE = 20
DEFAULT_MYSTERY_RESOLUTION_REFERENCE_MODE = "notes_only"
MYSTERY_RESOLUTION_REFERENCE_MODES = {"notes_only", "notes_and_sources"}


@dataclass(slots=True)
class PreparedMysteryResolution:
    mystery: dict[str, object]
    request: MysteryResolutionRequest
    candidate_note_map: dict[int, dict[str, object]]
    candidate_source_map: dict[int, dict[str, object]]


@dataclass(slots=True)
class PendingBatchSource:
    source_id: int
    source: ParsedSource


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


def normalize_mystery_resolution_batch_size(value: object) -> int:
    if isinstance(value, bool):
        return DEFAULT_MYSTERY_RESOLUTION_BATCH_SIZE
    if isinstance(value, int):
        size = value
    elif isinstance(value, float) and value.is_integer():
        size = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        size = int(value.strip())
    else:
        return DEFAULT_MYSTERY_RESOLUTION_BATCH_SIZE
    if size < 1 or size > MAX_MYSTERY_RESOLUTION_BATCH_SIZE:
        return DEFAULT_MYSTERY_RESOLUTION_BATCH_SIZE
    return size


def normalize_document_note_batch_size(value: object) -> int:
    if isinstance(value, bool):
        return DEFAULT_DOCUMENT_NOTE_BATCH_SIZE
    if isinstance(value, int):
        size = value
    elif isinstance(value, float) and value.is_integer():
        size = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        size = int(value.strip())
    else:
        return DEFAULT_DOCUMENT_NOTE_BATCH_SIZE
    if size < 1 or size > MAX_DOCUMENT_NOTE_BATCH_SIZE:
        return DEFAULT_DOCUMENT_NOTE_BATCH_SIZE
    return size


def load_document_note_batch_size(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT document_note_batch_size FROM settings WHERE id = 1").fetchone()
    if row is None:
        return DEFAULT_DOCUMENT_NOTE_BATCH_SIZE
    return normalize_document_note_batch_size(row["document_note_batch_size"])


def load_mystery_resolution_batch_size(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT mystery_resolution_batch_size FROM settings WHERE id = 1").fetchone()
    if row is None:
        return DEFAULT_MYSTERY_RESOLUTION_BATCH_SIZE
    return normalize_mystery_resolution_batch_size(row["mystery_resolution_batch_size"])


def normalize_mystery_resolution_reference_mode(value: object) -> str:
    if not isinstance(value, str):
        return DEFAULT_MYSTERY_RESOLUTION_REFERENCE_MODE
    mode = value.strip().lower()
    if mode not in MYSTERY_RESOLUTION_REFERENCE_MODES:
        return DEFAULT_MYSTERY_RESOLUTION_REFERENCE_MODE
    return mode


def load_mystery_resolution_reference_mode(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT mystery_resolution_reference_mode FROM settings WHERE id = 1").fetchone()
    if row is None:
        return DEFAULT_MYSTERY_RESOLUTION_REFERENCE_MODE
    return normalize_mystery_resolution_reference_mode(row["mystery_resolution_reference_mode"])


def update_document_progress(
    connection: sqlite3.Connection,
    document_id: int,
    step_name: str,
    detail: str = "",
) -> None:
    connection.execute(
        """
        UPDATE documents
        SET progress_step_name = ?, progress_detail = ?, progress_step_index = ?, progress_step_count = ?
        WHERE id = ?
        """,
        (step_name, detail, INGESTION_STEPS.index(step_name) + 1, len(INGESTION_STEPS), document_id),
    )


def _format_progress_detail(current: int, total: int, noun: str) -> str:
    return f"Processing {current}/{total} {noun}"


def _source_progress_noun(kind: str) -> str:
    return "pages" if kind == "pdf" else "sections"


def _delete_document_outputs(connection: sqlite3.Connection, document_id: int) -> None:
    connection.execute("DELETE FROM sources WHERE document_id = ?", (document_id,))
    connection.execute("DELETE FROM notes WHERE document_id = ?", (document_id,))
    connection.execute("DELETE FROM unresolved_mysteries WHERE document_id = ?", (document_id,))


def _reset_document_mystery_resolution_state(connection: sqlite3.Connection, document_id: int) -> None:
    connection.execute(
        "DELETE FROM mystery_refs WHERE mystery_id IN (SELECT id FROM unresolved_mysteries WHERE document_id = ?)",
        (document_id,),
    )
    connection.execute(
        """
        UPDATE unresolved_mysteries
        SET status = 'open',
            resolution_summary = '',
            resolution_note_id = NULL,
            resolution_source_id = NULL,
            resolved_at = NULL
        WHERE document_id = ?
        """,
        (document_id,),
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
            SELECT notes.id AS note_id, note_sources.source_id, notes.note, notes.keywords,
                   sources.content, sources.locator, sources.page_number
            FROM notes_fts
            JOIN notes ON notes_fts.rowid = notes.id
            JOIN note_sources ON note_sources.note_id = notes.id
            JOIN sources ON sources.id = note_sources.source_id
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
        SELECT notes.id AS note_id, note_sources.source_id, notes.note, notes.keywords,
               sources.content, sources.locator, sources.page_number
        FROM notes
        JOIN note_sources ON note_sources.note_id = notes.id
        JOIN sources ON sources.id = note_sources.source_id
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


def _prepare_mystery_resolution(
    document_name: str,
    mystery: dict[str, object],
    candidates: list[dict[str, object]],
) -> PreparedMysteryResolution:
    return PreparedMysteryResolution(
        mystery=mystery,
        request=build_mystery_resolution_request(document_name, mystery, candidates),
        candidate_note_map={
            int(candidate["note_id"]): candidate for candidate in candidates if candidate["note_id"] is not None
        },
        candidate_source_map={
            int(candidate["source_id"]): candidate for candidate in candidates if candidate["source_id"] is not None
        },
    )


def _apply_mystery_resolution_result(
    connection: sqlite3.Connection,
    *,
    prepared: PreparedMysteryResolution,
    resolution,
    include_source_excerpts: bool,
) -> None:
    note_ids = [note_id for note_id in resolution.note_ids if note_id in prepared.candidate_note_map]
    source_ids = (
        [source_id for source_id in resolution.source_ids if source_id in prepared.candidate_source_map]
        if include_source_excerpts
        else []
    )
    for note_id in note_ids:
        source_id = int(prepared.candidate_note_map[note_id]["source_id"])
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
            prepared.mystery["id"],
        ),
    )
    _replace_resolution_refs(
        connection,
        mystery_id=int(prepared.mystery["id"]),
        note_ids=note_ids,
        source_ids=source_ids,
    )


def _flush_mystery_batch(
    connection: sqlite3.Connection,
    *,
    provider,
    document_name: str,
    batch: list[PreparedMysteryResolution],
    include_source_excerpts: bool,
) -> int:
    if not batch:
        return 0
    resolutions = resolve_mysteries(
        provider,
        [item.request for item in batch],
        include_source_excerpts=include_source_excerpts,
    )
    for prepared, resolution in zip(batch, resolutions, strict=True):
        _apply_mystery_resolution_result(
            connection,
            prepared=prepared,
            resolution=resolution,
            include_source_excerpts=include_source_excerpts,
        )
    return len(batch)


def _resolve_document_mysteries(
    connection: sqlite3.Connection,
    *,
    provider,
    document_id: int,
    document_name: str,
) -> None:
    reference_mode = load_mystery_resolution_reference_mode(connection)
    batch_size = load_mystery_resolution_batch_size(connection)
    include_source_excerpts = reference_mode == "notes_and_sources"
    total_mysteries = connection.execute(
        "SELECT COUNT(*) FROM unresolved_mysteries WHERE document_id = ?",
        (document_id,),
    ).fetchone()[0]
    mysteries = connection.execute(
        """
        SELECT unresolved_mysteries.id, unresolved_mysteries.question, unresolved_mysteries.reason, unresolved_mysteries.keywords,
               unresolved_mysteries.note_id, unresolved_mysteries.source_id, sources.locator AS origin_locator,
               sources.page_number AS origin_page_number
        FROM unresolved_mysteries
        JOIN sources ON sources.id = unresolved_mysteries.source_id
        WHERE unresolved_mysteries.document_id = ? AND unresolved_mysteries.status = 'open'
        ORDER BY unresolved_mysteries.id
        """,
        (document_id,),
    ).fetchall()

    open_mysteries = len(mysteries)
    if total_mysteries == 0:
        update_document_progress(connection, document_id, "resolving mysteries", "Processing 0/0 mysteries")
        connection.commit()
        return

    resolved_count = total_mysteries - open_mysteries
    if open_mysteries == 0:
        update_document_progress(
            connection,
            document_id,
            "resolving mysteries",
            _format_progress_detail(total_mysteries, total_mysteries, "mysteries"),
        )
        connection.commit()
        return

    processed_count = resolved_count
    pending_batch: list[PreparedMysteryResolution] = []
    for mystery_row in mysteries:
        mystery = dict(mystery_row)
        search_text = " ".join(
            item for item in [mystery["question"], mystery["reason"], mystery["keywords"]] if item
        ).strip()
        candidates = _search_mystery_candidates(connection, document_id=document_id, search_text=search_text)
        if not candidates:
            processed_count += _flush_mystery_batch(
                connection,
                provider=provider,
                document_name=document_name,
                batch=pending_batch,
                include_source_excerpts=include_source_excerpts,
            )
            pending_batch = []
            update_document_progress(
                connection,
                document_id,
                "resolving mysteries",
                _format_progress_detail(processed_count, total_mysteries, "mysteries"),
            )
            connection.commit()
            connection.execute(
                """
                UPDATE unresolved_mysteries
                SET status = 'open', resolution_summary = 'No matching notes or sources were strong enough to resolve this mystery.',
                    resolution_note_id = NULL, resolution_source_id = NULL, resolved_at = NULL
                WHERE id = ?
                """,
                (mystery["id"],),
            )
            processed_count += 1
            update_document_progress(
                connection,
                document_id,
                "resolving mysteries",
                _format_progress_detail(processed_count, total_mysteries, "mysteries"),
            )
            connection.commit()
            continue

        pending_batch.append(_prepare_mystery_resolution(document_name, mystery, candidates))
        if len(pending_batch) < batch_size:
            continue
        processed_count += _flush_mystery_batch(
            connection,
            provider=provider,
            document_name=document_name,
            batch=pending_batch,
            include_source_excerpts=include_source_excerpts,
        )
        pending_batch = []
        update_document_progress(
            connection,
            document_id,
            "resolving mysteries",
            _format_progress_detail(processed_count, total_mysteries, "mysteries"),
        )
        connection.commit()

    if pending_batch:
        processed_count += _flush_mystery_batch(
            connection,
            provider=provider,
            document_name=document_name,
            batch=pending_batch,
            include_source_excerpts=include_source_excerpts,
        )
        update_document_progress(
            connection,
            document_id,
            "resolving mysteries",
            _format_progress_detail(processed_count, total_mysteries, "mysteries"),
        )
        connection.commit()


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
            progress_step_name, progress_detail, progress_step_index, progress_step_count, error, created_at
        )
        VALUES (?, ?, ?, '', 'processing', ?, '', ?, ?, '', ?)
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


def retry_document_ingestion(connection: sqlite3.Connection, document_id: int) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT id, status, progress_step_name
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Document not found")
    if row["status"] != "failed":
        raise ValueError("Only failed documents can be retried")

    resume_step = row["progress_step_name"] or "parsing document"
    if resume_step in {"resolving mysteries", "finalizing document"}:
        resume_step = "resolving mysteries"
    elif resume_step == "parsing document":
        resume_step = "parsing document"
        _delete_document_outputs(connection, document_id)
        connection.execute("UPDATE documents SET kind = '' WHERE id = ?", (document_id,))

    update_document_progress(connection, document_id, resume_step)
    connection.execute(
        "UPDATE documents SET status = 'processing', error = '' WHERE id = ?",
        (document_id,),
    )
    connection.commit()

    document = connection.execute(
        """
        SELECT id, original_name, kind, status, progress_step_name, progress_detail, progress_step_index, progress_step_count,
               error, created_at,
               (SELECT COUNT(*) FROM sources WHERE sources.document_id = documents.id) AS source_count,
               (SELECT COUNT(*) FROM notes WHERE notes.document_id = documents.id) AS note_count,
               (SELECT COUNT(*) FROM unresolved_mysteries WHERE unresolved_mysteries.document_id = documents.id) AS mystery_count,
               (SELECT COUNT(*) FROM unresolved_mysteries WHERE unresolved_mysteries.document_id = documents.id AND unresolved_mysteries.status = 'open') AS open_mystery_count
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    return dict(document)


def _load_existing_source_rows(connection: sqlite3.Connection, document_id: int) -> dict[tuple[str, int | None], dict[str, object]]:
    rows = connection.execute(
        """
        SELECT sources.id, sources.locator, sources.page_number, sources.image_summary,
               COUNT(notes.id) AS note_count
        FROM sources
        LEFT JOIN note_sources ON note_sources.source_id = sources.id
        LEFT JOIN notes ON notes.id = note_sources.note_id
        WHERE sources.document_id = ?
        GROUP BY sources.id
        ORDER BY sources.id
        """,
        (document_id,),
    ).fetchall()
    return {
        (str(row["locator"]), row["page_number"] if isinstance(row["page_number"], int) else None): dict(row)
        for row in rows
    }


def _load_source_image_descriptions(
    connection: sqlite3.Connection,
    source_ids: list[int],
) -> dict[int, list[str]]:
    if not source_ids:
        return {}
    placeholders = ", ".join("?" for _ in source_ids)
    rows = connection.execute(
        f"""
        SELECT source_id, description
        FROM source_images
        WHERE source_id IN ({placeholders})
        ORDER BY source_id, id
        """,
        source_ids,
    ).fetchall()
    descriptions_by_source: dict[int, list[str]] = {source_id: [] for source_id in source_ids}
    for row in rows:
        description = str(row["description"]).strip()
        if description:
            descriptions_by_source[int(row["source_id"])].append(description)
    return descriptions_by_source


def _persist_batch_generated_note(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    generated_note,
) -> None:
    note_cursor = connection.execute(
        """
        INSERT INTO notes (document_id, source_id, note, keywords, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            document_id,
            generated_note.source_ids[0],
            generated_note.note_text,
            generated_note.keywords,
            utcnow(),
        ),
    )
    note_id = int(note_cursor.lastrowid)
    for ref_rank, source_id in enumerate(generated_note.source_ids, start=1):
        connection.execute(
            """
            INSERT OR IGNORE INTO note_sources (note_id, source_id, ref_rank)
            VALUES (?, ?, ?)
            """,
            (note_id, source_id, ref_rank),
        )
    for mystery in generated_note.mysteries:
        _insert_mystery(
            connection,
            document_id=document_id,
            source_id=mystery.source_id,
            note_id=note_id,
            draft=MysteryDraft(question=mystery.question, reason=mystery.reason, keywords=mystery.keywords),
        )


def _flush_note_batch(
    connection: sqlite3.Connection,
    *,
    provider,
    document_id: int,
    document_name: str,
    batch: list[PendingBatchSource],
) -> tuple[set[int], list[str]]:
    if not batch:
        return set(), []
    source_ids = [item.source_id for item in batch]
    image_descriptions_by_source = _load_source_image_descriptions(connection, source_ids)
    batch_inputs = [
        BatchNoteSourceInput(
            source_id=item.source_id,
            reference_label=build_reference_label(document_name, item.source.locator, item.source.page_number),
            locator=item.source.locator,
            page_number=item.source.page_number,
            content=item.source.content,
            image_descriptions=image_descriptions_by_source.get(item.source_id, []),
        )
        for item in batch
    ]
    parsed = generate_batch_notes(provider, batch_inputs)
    covered_source_ids: set[int] = set()
    for generated_note in parsed.notes:
        covered_source_ids.update(generated_note.source_ids)
        _persist_batch_generated_note(connection, document_id=document_id, generated_note=generated_note)
    missing_locators = [item.source.locator for item in batch if item.source_id in set(parsed.missing_source_ids)]
    return covered_source_ids, missing_locators


def _continue_document_ingestion(connection: sqlite3.Connection, config: AppConfig, *, document_id: int) -> None:
    try:
        document_row = connection.execute(
            """
            SELECT id, original_name, stored_path, media_type, progress_step_name
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
        provider = build_provider(settings)
        resume_step = document_row["progress_step_name"] or "parsing document"
        if resume_step != "resolving mysteries":
            document_note_batch_size = load_document_note_batch_size(connection)
            update_document_progress(connection, document_id, "parsing document")
            connection.commit()
            kind, parsed_sources = parse_document(stored_path, config.images_dir)
            connection.execute("UPDATE documents SET kind = ? WHERE id = ?", (kind, document_id))
            total_sources = len(parsed_sources)
            progress_noun = _source_progress_noun(kind)
            existing_sources = _load_existing_source_rows(connection, document_id)
            completed_source_ids = {int(row["id"]) for row in existing_sources.values() if int(row["note_count"]) > 0}
            update_document_progress(
                connection,
                document_id,
                "generating notes",
                _format_progress_detail(len(completed_source_ids), total_sources, progress_noun),
            )
            connection.commit()

            pending_batch_sources: list[PendingBatchSource] = []
            for source_index, source in enumerate(parsed_sources, start=1):
                source_key = (source.locator, source.page_number)
                existing_source = existing_sources.get(source_key)
                if existing_source is not None and int(existing_source["note_count"]) > 0:
                    continue

                if existing_source is not None:
                    source_id = int(existing_source["id"])
                    connection.execute("DELETE FROM source_images WHERE source_id = ?", (source_id,))
                    connection.execute(
                        """
                        UPDATE sources
                        SET locator = ?, page_number = ?, content = ?, image_summary = '', metadata_json = ?, created_at = ?
                        WHERE id = ?
                        """,
                        (
                            source.locator,
                            source.page_number,
                            source.content,
                            json.dumps(source.metadata),
                            utcnow(),
                            source_id,
                        ),
                    )
                else:
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
                    existing_sources[source_key] = {"id": source_id, "note_count": 0}
                if source.images:
                    update_document_progress(
                        connection,
                        document_id,
                        "describing images",
                        _format_progress_detail(source_index, total_sources, progress_noun),
                    )
                    connection.commit()
                    image_descriptions = describe_images(provider, source)
                else:
                    image_descriptions = []
                update_document_progress(
                    connection,
                    document_id,
                    "generating notes",
                    _format_progress_detail(source_index, total_sources, progress_noun),
                )
                connection.commit()
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
                pending_batch_sources.append(PendingBatchSource(source_id=source_id, source=source))
                existing_sources[source_key] = {"id": source_id, "note_count": 0}
                connection.commit()
                if len(pending_batch_sources) < document_note_batch_size:
                    continue
                covered_source_ids, missing_locators = _flush_note_batch(
                    connection,
                    provider=provider,
                    document_id=document_id,
                    document_name=original_name,
                    batch=pending_batch_sources,
                )
                completed_source_ids.update(covered_source_ids)
                for pending_source in pending_batch_sources:
                    if pending_source.source_id in covered_source_ids:
                        existing_sources[(pending_source.source.locator, pending_source.source.page_number)] = {
                            "id": pending_source.source_id,
                            "note_count": 1,
                        }
                update_document_progress(
                    connection,
                    document_id,
                    "generating notes",
                    _format_progress_detail(len(completed_source_ids), total_sources, progress_noun),
                )
                connection.commit()
                if missing_locators:
                    raise RuntimeError(
                        "Batch note generation did not return notes for source units: " + ", ".join(missing_locators)
                    )
                pending_batch_sources = []

            if pending_batch_sources:
                covered_source_ids, missing_locators = _flush_note_batch(
                    connection,
                    provider=provider,
                    document_id=document_id,
                    document_name=original_name,
                    batch=pending_batch_sources,
                )
                completed_source_ids.update(covered_source_ids)
                for pending_source in pending_batch_sources:
                    if pending_source.source_id in covered_source_ids:
                        existing_sources[(pending_source.source.locator, pending_source.source.page_number)] = {
                            "id": pending_source.source_id,
                            "note_count": 1,
                        }
                update_document_progress(
                    connection,
                    document_id,
                    "generating notes",
                    _format_progress_detail(len(completed_source_ids), total_sources, progress_noun),
                )
                connection.commit()
                if missing_locators:
                    raise RuntimeError(
                        "Batch note generation did not return notes for source units: " + ", ".join(missing_locators)
                    )

            mystery_count = connection.execute(
                "SELECT COUNT(*) FROM unresolved_mysteries WHERE document_id = ?",
                (document_id,),
            ).fetchone()[0]
            update_document_progress(
                connection,
                document_id,
                "resolving mysteries",
                _format_progress_detail(0, mystery_count, "mysteries"),
            )
            connection.commit()

        _resolve_document_mysteries(connection, provider=provider, document_id=document_id, document_name=original_name)
        update_document_progress(connection, document_id, "finalizing document")
        connection.execute(
            "UPDATE documents SET status = 'ready', error = '', progress_step_name = ?, progress_detail = '', progress_step_index = ?, progress_step_count = ? WHERE id = ?",
            ("finalizing document", len(INGESTION_STEPS), len(INGESTION_STEPS), document_id),
        )
        connection.commit()
    except Exception as exc:
        connection.execute("UPDATE documents SET status = 'failed', error = ? WHERE id = ?", (str(exc), document_id))
        connection.commit()
