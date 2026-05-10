from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import OrderedDict
from typing import Final

from terrismen.config import AppConfig
from terrismen.db import connect, utcnow
from terrismen.llm import build_provider
from terrismen.llm.base import ProviderError
from terrismen.services.ingestion import load_provider_settings
from terrismen.services.notes import build_reference_label
from terrismen.services.retrieval import build_fts_query


REFERENCE_PICKER_PROMPT = """You choose which source references are relevant for answering a user question.
Return JSON only in the shape {"source_ids":[1,2,3]}.
Only include source IDs that are clearly relevant."""

ANSWER_PROMPT = """You answer only from the supplied source excerpts and notes.
- Do not use outside knowledge, prior assumptions, or unstated inferences.
- Give a helpful, direct answer that stays strictly grounded in the supplied material.
- Every factual claim must include an inline citation in square brackets using the supplied reference labels.
- Never invent citations or mention a source that was not provided.
- If the supplied material does not clearly answer the question, say that you do not know from the provided sources and briefly state what is missing.
"""

CHAT_PROGRESS_STEPS: Final[tuple[str, ...]] = (
    "saving user message",
    "loading recent history",
    "searching candidate notes",
    "picking source references",
    "loading source blocks",
    "generating final answer",
)

def recent_messages(connection: sqlite3.Connection, limit: int = 8) -> list[sqlite3.Row]:
    rows = connection.execute(
        "SELECT id, role, content, citations_json, created_at FROM messages ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(reversed(rows))


def _document_scope_clause(document_ids: list[int], column: str) -> tuple[str, list[int]]:
    if not document_ids:
        return "", []
    placeholders = ", ".join("?" for _ in document_ids)
    return f" AND {column} IN ({placeholders})", document_ids


def _search_note_rows(connection: sqlite3.Connection, question: str, limit: int, document_ids: list[int]) -> list[dict[str, object]]:
    fts_query = build_fts_query(question)
    scope_clause, scope_values = _document_scope_clause(document_ids, "notes.document_id")
    if fts_query:
        rows = connection.execute(
            f"""
            SELECT notes.id, notes.note, notes.keywords, notes.source_id, sources.content, sources.locator, sources.page_number,
                   documents.original_name
            FROM notes_fts
            JOIN notes ON notes_fts.rowid = notes.id
            JOIN sources ON sources.id = notes.source_id
            JOIN documents ON documents.id = notes.document_id
            WHERE notes_fts MATCH ?{scope_clause}
            ORDER BY bm25(notes_fts)
            LIMIT ?
            """,
            [fts_query, *scope_values, limit],
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]
    like_pattern = f"%{question[:120]}%"
    rows = connection.execute(
        f"""
        SELECT notes.id, notes.note, notes.keywords, notes.source_id, sources.content, sources.locator, sources.page_number,
               documents.original_name
        FROM notes
        JOIN sources ON sources.id = notes.source_id
        JOIN documents ON documents.id = notes.document_id
        WHERE (notes.note LIKE ? OR sources.content LIKE ?){scope_clause}
        ORDER BY notes.id DESC
        LIMIT ?
        """,
        [like_pattern, like_pattern, *scope_values, limit],
    ).fetchall()
    return [dict(row) for row in rows]


def _search_mystery_rows(connection: sqlite3.Connection, question: str, limit: int, document_ids: list[int]) -> list[dict[str, object]]:
    fts_query = build_fts_query(question)
    scope_clause, scope_values = _document_scope_clause(document_ids, "unresolved_mysteries.document_id")
    if fts_query:
        rows = connection.execute(
            f"""
            SELECT unresolved_mysteries.id,
                   CASE
                       WHEN unresolved_mysteries.status = 'resolved' THEN
                           'Mystery resolution: ' || unresolved_mysteries.resolution_summary || '\nOriginal mystery: ' || unresolved_mysteries.question
                       ELSE
                           'Unresolved mystery: ' || unresolved_mysteries.question || '\nWhy uncertain: ' || unresolved_mysteries.reason
                   END AS note,
                   unresolved_mysteries.keywords,
                   COALESCE(unresolved_mysteries.resolution_source_id, unresolved_mysteries.source_id) AS source_id,
                   COALESCE(resolution_sources.content, origin_sources.content) AS content,
                   COALESCE(resolution_sources.locator, origin_sources.locator) AS locator,
                   COALESCE(resolution_sources.page_number, origin_sources.page_number) AS page_number,
                   documents.original_name
            FROM unresolved_mysteries_fts
            JOIN unresolved_mysteries ON unresolved_mysteries_fts.rowid = unresolved_mysteries.id
            JOIN documents ON documents.id = unresolved_mysteries.document_id
            LEFT JOIN sources origin_sources ON origin_sources.id = unresolved_mysteries.source_id
            LEFT JOIN sources resolution_sources ON resolution_sources.id = unresolved_mysteries.resolution_source_id
            WHERE unresolved_mysteries_fts MATCH ?{scope_clause}
            ORDER BY CASE unresolved_mysteries.status WHEN 'resolved' THEN 0 ELSE 1 END, bm25(unresolved_mysteries_fts)
            LIMIT ?
            """,
            [fts_query, *scope_values, limit],
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]

    like_pattern = f"%{question[:120]}%"
    rows = connection.execute(
        f"""
        SELECT unresolved_mysteries.id,
               CASE
                   WHEN unresolved_mysteries.status = 'resolved' THEN
                       'Mystery resolution: ' || unresolved_mysteries.resolution_summary || '\nOriginal mystery: ' || unresolved_mysteries.question
                   ELSE
                       'Unresolved mystery: ' || unresolved_mysteries.question || '\nWhy uncertain: ' || unresolved_mysteries.reason
               END AS note,
               unresolved_mysteries.keywords,
               COALESCE(unresolved_mysteries.resolution_source_id, unresolved_mysteries.source_id) AS source_id,
               COALESCE(resolution_sources.content, origin_sources.content) AS content,
               COALESCE(resolution_sources.locator, origin_sources.locator) AS locator,
               COALESCE(resolution_sources.page_number, origin_sources.page_number) AS page_number,
               documents.original_name
        FROM unresolved_mysteries
        JOIN documents ON documents.id = unresolved_mysteries.document_id
        LEFT JOIN sources origin_sources ON origin_sources.id = unresolved_mysteries.source_id
        LEFT JOIN sources resolution_sources ON resolution_sources.id = unresolved_mysteries.resolution_source_id
        WHERE (unresolved_mysteries.question LIKE ?
           OR unresolved_mysteries.reason LIKE ?
           OR unresolved_mysteries.resolution_summary LIKE ?){scope_clause}
        ORDER BY CASE unresolved_mysteries.status WHEN 'resolved' THEN 0 ELSE 1 END, unresolved_mysteries.id DESC
        LIMIT ?
        """,
        [like_pattern, like_pattern, like_pattern, *scope_values, limit],
    ).fetchall()
    return [dict(row) for row in rows]


def search_candidate_notes(connection: sqlite3.Connection, question: str, limit: int = 6, document_ids: list[int] | None = None) -> list[dict[str, object]]:
    scope = document_ids or []
    combined = _search_note_rows(connection, question, limit, scope) + _search_mystery_rows(connection, question, limit, scope)
    deduped: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for row in combined:
        key = (row.get("source_id"), row.get("note"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped


def _extract_json_block(text: str) -> dict[str, object]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        return json.loads(match.group(0))


def _pick_source_ids(provider, question: str, history: list[sqlite3.Row], candidates: list[dict[str, object]]) -> list[int]:
    if not candidates:
        return []
    candidate_lines = []
    for candidate in candidates:
        reference = build_reference_label(candidate["original_name"], candidate["locator"], candidate["page_number"])
        candidate_lines.append(
            f"source_id={candidate['source_id']} | reference={reference}\nNote:\n{candidate['note'][:1500]}"
        )
    history_block = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])
    response = provider.complete(
        REFERENCE_PICKER_PROMPT,
        (
            f"Question:\n{question}\n\n"
            f"Recent chat history:\n{history_block or '[none]'}\n\n"
            f"Candidate notes:\n\n" + "\n\n".join(candidate_lines)
        ),
    )
    payload = _extract_json_block(response)
    values = payload.get("source_ids", [])
    if not isinstance(values, list):
        return []
    source_ids: list[int] = []
    for value in values:
        if isinstance(value, int):
            source_ids.append(value)
    return source_ids


def _load_sources(connection: sqlite3.Connection, source_ids: list[int], document_ids: list[int] | None = None) -> list[sqlite3.Row]:
    if not source_ids:
        return []
    placeholders = ", ".join("?" for _ in source_ids)
    scope_clause, scope_values = _document_scope_clause(document_ids or [], "sources.document_id")
    return connection.execute(
        f"""
        SELECT sources.id, sources.locator, sources.page_number, sources.content, sources.image_summary,
               notes.note, documents.original_name
        FROM sources
        JOIN notes ON notes.source_id = sources.id
        JOIN documents ON documents.id = sources.document_id
        WHERE sources.id IN ({placeholders}){scope_clause}
        ORDER BY sources.id
        """,
        [*source_ids, *scope_values],
    ).fetchall()


def save_message(connection: sqlite3.Connection, role: str, content: str, citations: list[dict[str, object]] | None = None) -> int:
    cursor = connection.execute(
        "INSERT INTO messages (role, content, citations_json, created_at) VALUES (?, ?, ?, ?)",
        (role, content, json.dumps(citations or []), utcnow()),
    )
    connection.commit()
    return int(cursor.lastrowid)


def update_chat_request_progress(connection: sqlite3.Connection, request_id: str, step_name: str) -> None:
    connection.execute(
        """
        UPDATE chat_requests
        SET progress_step_name = ?, progress_step_index = ?, progress_step_count = ?
        WHERE id = ?
        """,
        (step_name, CHAT_PROGRESS_STEPS.index(step_name) + 1, len(CHAT_PROGRESS_STEPS), request_id),
    )


def get_chat_request(connection: sqlite3.Connection, request_id: str) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT id, question, selected_document_ids_json, status, progress_step_name, progress_step_index, progress_step_count,
               error, user_message_id, assistant_message_id, created_at, completed_at
        FROM chat_requests
        WHERE id = ?
        """,
        (request_id,),
    ).fetchone()
    return row_to_chat_request(row) if row is not None else None


def row_to_chat_request(row: sqlite3.Row) -> dict[str, object]:
    payload = dict(row)
    payload["selected_document_ids"] = json.loads(payload.pop("selected_document_ids_json") or "[]")
    return payload


def create_chat_request(connection: sqlite3.Connection, question: str, document_ids: list[int] | None = None) -> dict[str, object]:
    request_id = uuid.uuid4().hex
    connection.execute(
        """
        INSERT INTO chat_requests (
            id, question, selected_document_ids_json, status, progress_step_name, progress_step_index, progress_step_count,
            error, user_message_id, assistant_message_id, created_at, completed_at
        )
        VALUES (?, ?, ?, 'processing', ?, ?, ?, '', NULL, NULL, ?, NULL)
        """,
        (
            request_id,
            question,
            json.dumps(document_ids or []),
            "saving user message",
            CHAT_PROGRESS_STEPS.index("saving user message") + 1,
            len(CHAT_PROGRESS_STEPS),
            utcnow(),
        ),
    )
    user_message_id = save_message(connection, "user", question, [])
    connection.execute(
        """
        UPDATE chat_requests
        SET user_message_id = ?, progress_step_name = ?, progress_step_index = ?, progress_step_count = ?
        WHERE id = ?
        """,
        (
            user_message_id,
            "loading recent history",
            CHAT_PROGRESS_STEPS.index("loading recent history") + 1,
            len(CHAT_PROGRESS_STEPS),
            request_id,
        ),
    )
    connection.commit()
    return get_chat_request(connection, request_id) or {}


def continue_chat_request(config: AppConfig, request_id: str) -> None:
    with connect(config.database_path) as connection:
        _continue_chat_request(connection, request_id)


def _continue_chat_request(connection: sqlite3.Connection, request_id: str) -> None:
    request = get_chat_request(connection, request_id)
    if request is None:
        return
    question = str(request["question"])
    document_ids = [int(value) for value in request.get("selected_document_ids", [])]
    try:
        if not document_ids:
            answer = "Select at least one document source before asking a grounded question."
            assistant_id = save_message(connection, "assistant", answer, [])
            connection.execute(
                """
                UPDATE chat_requests
                SET status = 'completed', assistant_message_id = ?, completed_at = ?,
                    progress_step_name = ?, progress_step_index = ?, progress_step_count = ?, error = ''
                WHERE id = ?
                """,
                (
                    assistant_id,
                    utcnow(),
                    "generating final answer",
                    len(CHAT_PROGRESS_STEPS),
                    len(CHAT_PROGRESS_STEPS),
                    request_id,
                ),
            )
            connection.commit()
            return

        settings = load_provider_settings(connection)
        if not settings.is_configured():
            raise ProviderError("Configure a provider before starting chat.")

        update_chat_request_progress(connection, request_id, "loading recent history")
        connection.commit()
        history = recent_messages(connection)
        provider = build_provider(settings)

        update_chat_request_progress(connection, request_id, "searching candidate notes")
        connection.commit()
        candidates = search_candidate_notes(connection, question, document_ids=document_ids)

        update_chat_request_progress(connection, request_id, "picking source references")
        connection.commit()
        picked_source_ids = _pick_source_ids(provider, question, history, candidates)
        if not picked_source_ids:
            picked_source_ids = list(OrderedDict.fromkeys(candidate["source_id"] for candidate in candidates[:4]))

        update_chat_request_progress(connection, request_id, "loading source blocks")
        connection.commit()
        sources = _load_sources(connection, picked_source_ids, document_ids)

        update_chat_request_progress(connection, request_id, "generating final answer")
        connection.commit()
        answer, citations = _generate_answer(provider, history, question, sources)
        assistant_id = save_message(connection, "assistant", answer, citations)
        connection.execute(
            """
            UPDATE chat_requests
            SET status = 'completed', assistant_message_id = ?, completed_at = ?,
                progress_step_name = ?, progress_step_index = ?, progress_step_count = ?, error = ''
            WHERE id = ?
            """,
            (
                assistant_id,
                utcnow(),
                "generating final answer",
                len(CHAT_PROGRESS_STEPS),
                len(CHAT_PROGRESS_STEPS),
                request_id,
            ),
        )
        connection.commit()
    except Exception as exc:
        connection.execute(
            "UPDATE chat_requests SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
            (str(exc), utcnow(), request_id),
        )
        connection.commit()


def _generate_answer(
    provider,
    history: list[sqlite3.Row],
    question: str,
    sources: list[sqlite3.Row],
) -> tuple[str, list[dict[str, object]]]:
    if not sources:
        return "I could not find relevant notes or source excerpts for that question yet.", []

    source_blocks = []
    citations: list[dict[str, object]] = []
    for source in sources:
        reference_label = build_reference_label(source["original_name"], source["locator"], source["page_number"])
        source_blocks.append(
            f"Reference: {reference_label}\n"
            f"Note:\n{source['note']}\n\n"
            f"Source excerpt:\n{source['content'] or '[no text extracted]'}\n\n"
            f"Image summary:\n{source['image_summary'] or '[none]'}"
        )
        citations.append(
            {
                "source_id": source["id"],
                "document_name": source["original_name"],
                "locator": source["locator"],
                "page_number": source["page_number"],
                "reference_label": reference_label,
            }
        )

    history_block = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])
    answer = provider.complete(
        ANSWER_PROMPT,
        (
            f"Question:\n{question}\n\n"
            f"Recent chat history:\n{history_block or '[none]'}\n\n"
            f"Supporting material:\n\n" + "\n\n---\n\n".join(source_blocks)
        ),
    )
    return answer, citations


def answer_question(connection: sqlite3.Connection, question: str, document_ids: list[int] | None = None) -> dict[str, object]:
    settings = load_provider_settings(connection)
    if not settings.is_configured():
        raise ProviderError("Configure a provider before starting chat.")
    provider = build_provider(settings)
    history = recent_messages(connection)
    candidates = search_candidate_notes(connection, question, document_ids=document_ids)
    picked_source_ids = _pick_source_ids(provider, question, history, candidates)
    if not picked_source_ids:
        picked_source_ids = list(OrderedDict.fromkeys(candidate["source_id"] for candidate in candidates[:4]))
    sources = _load_sources(connection, picked_source_ids, document_ids)
    answer, citations = _generate_answer(provider, history, question, sources)
    assistant_id = save_message(connection, "assistant", answer, citations)
    return {"id": assistant_id, "content": answer, "citations": citations}
