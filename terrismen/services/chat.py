from __future__ import annotations

import json
import re
import sqlite3
from collections import OrderedDict

from terrismen.db import utcnow
from terrismen.llm import build_provider
from terrismen.llm.base import ProviderError
from terrismen.services.ingestion import load_provider_settings
from terrismen.services.notes import build_reference_label


REFERENCE_PICKER_PROMPT = """You choose which source references are relevant for answering a user question.
Return JSON only in the shape {"source_ids":[1,2,3]}.
Only include source IDs that are clearly relevant."""

ANSWER_PROMPT = """You answer only from the supplied source excerpts and notes.
- Give a helpful, direct answer.
- Cite claims inline with square brackets using the supplied reference labels.
- If the sources are insufficient, say so plainly.
"""


def _question_terms(question: str) -> list[str]:
    return [term for term in re.findall(r"[A-Za-z0-9_]{2,}", question.lower()) if term not in {"the", "and", "for"}]


def _fts_query(question: str) -> str | None:
    terms = list(OrderedDict.fromkeys(_question_terms(question)))
    if not terms:
        return None
    return " OR ".join(terms[:10])


def recent_messages(connection: sqlite3.Connection, limit: int = 8) -> list[sqlite3.Row]:
    rows = connection.execute(
        "SELECT id, role, content, citations_json, created_at FROM messages ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(reversed(rows))


def search_candidate_notes(connection: sqlite3.Connection, question: str, limit: int = 6) -> list[sqlite3.Row]:
    fts_query = _fts_query(question)
    if fts_query:
        rows = connection.execute(
            """
            SELECT notes.id, notes.note, notes.keywords, notes.source_id, sources.content, sources.locator, sources.page_number,
                   documents.original_name
            FROM notes_fts
            JOIN notes ON notes_fts.rowid = notes.id
            JOIN sources ON sources.id = notes.source_id
            JOIN documents ON documents.id = notes.document_id
            WHERE notes_fts MATCH ?
            ORDER BY bm25(notes_fts)
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        if rows:
            return rows
    like_pattern = f"%{question[:120]}%"
    return connection.execute(
        """
        SELECT notes.id, notes.note, notes.keywords, notes.source_id, sources.content, sources.locator, sources.page_number,
               documents.original_name
        FROM notes
        JOIN sources ON sources.id = notes.source_id
        JOIN documents ON documents.id = notes.document_id
        WHERE notes.note LIKE ? OR sources.content LIKE ?
        ORDER BY notes.id DESC
        LIMIT ?
        """,
        (like_pattern, like_pattern, limit),
    ).fetchall()


def _extract_json_block(text: str) -> dict[str, object]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        return json.loads(match.group(0))


def _pick_source_ids(provider, question: str, history: list[sqlite3.Row], candidates: list[sqlite3.Row]) -> list[int]:
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


def _load_sources(connection: sqlite3.Connection, source_ids: list[int]) -> list[sqlite3.Row]:
    if not source_ids:
        return []
    placeholders = ", ".join("?" for _ in source_ids)
    return connection.execute(
        f"""
        SELECT sources.id, sources.locator, sources.page_number, sources.content, sources.image_summary,
               notes.note, documents.original_name
        FROM sources
        JOIN notes ON notes.source_id = sources.id
        JOIN documents ON documents.id = sources.document_id
        WHERE sources.id IN ({placeholders})
        ORDER BY sources.id
        """,
        source_ids,
    ).fetchall()


def save_message(connection: sqlite3.Connection, role: str, content: str, citations: list[dict[str, object]] | None = None) -> int:
    cursor = connection.execute(
        "INSERT INTO messages (role, content, citations_json, created_at) VALUES (?, ?, ?, ?)",
        (role, content, json.dumps(citations or []), utcnow()),
    )
    connection.commit()
    return int(cursor.lastrowid)


def answer_question(connection: sqlite3.Connection, question: str) -> dict[str, object]:
    settings = load_provider_settings(connection)
    if not settings.is_configured():
        raise ProviderError("Configure a provider before starting chat.")
    provider = build_provider(settings)
    history = recent_messages(connection)
    candidates = search_candidate_notes(connection, question)
    picked_source_ids = _pick_source_ids(provider, question, history, candidates)
    if not picked_source_ids:
        picked_source_ids = list(OrderedDict.fromkeys(candidate["source_id"] for candidate in candidates[:4]))
    sources = _load_sources(connection, picked_source_ids)
    if not sources:
        answer = "I could not find relevant notes or source excerpts for that question yet."
        assistant_id = save_message(connection, "assistant", answer, [])
        return {"id": assistant_id, "content": answer, "citations": []}

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
    assistant_id = save_message(connection, "assistant", answer, citations)
    return {"id": assistant_id, "content": answer, "citations": citations}
