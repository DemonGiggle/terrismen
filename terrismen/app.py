from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from terrismen.config import AppConfig, load_config
from terrismen.db import connect, init_db, row_to_dict
from terrismen.models import ChatRequest, ProviderSettingsPayload
from terrismen.services.chat import continue_chat_request, create_chat_request, get_chat_request
from terrismen.services.ingestion import continue_document_ingestion, create_document_ingestion
from terrismen.services.notes import build_reference_label
from terrismen.services.parsers import ParserError


config = load_config()
init_db(config.database_path)

app = FastAPI(title="terrismen")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")


def get_connection() -> Iterator:
    connection = connect(config.database_path)
    try:
        yield connection
    finally:
        connection.close()


def serialize_message(row) -> dict[str, object]:
    payload = row_to_dict(row) or {}
    payload["citations"] = payload.pop("citations_json", [])
    return payload


def serialize_mystery_refs(connection, mystery_ids: list[int], document_name: str) -> dict[int, list[dict[str, object]]]:
    if not mystery_ids:
        return {}
    placeholders = ", ".join("?" for _ in mystery_ids)
    rows = connection.execute(
        f"""
        SELECT mystery_refs.mystery_id, mystery_refs.relation_type, mystery_refs.ref_rank, mystery_refs.why_relevant, mystery_refs.note_id,
               COALESCE(mystery_refs.source_id, note_sources.id) AS source_id,
               COALESCE(source_refs.locator, note_sources.locator) AS locator,
               COALESCE(source_refs.page_number, note_sources.page_number) AS page_number
        FROM mystery_refs
        LEFT JOIN notes reference_notes ON reference_notes.id = mystery_refs.note_id
        LEFT JOIN sources note_sources ON note_sources.id = reference_notes.source_id
        LEFT JOIN sources source_refs ON source_refs.id = mystery_refs.source_id
        WHERE mystery_refs.mystery_id IN ({placeholders})
        ORDER BY mystery_refs.mystery_id, mystery_refs.relation_type, mystery_refs.ref_rank, mystery_refs.id
        """,
        mystery_ids,
    ).fetchall()

    grouped: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        item = dict(row)
        item["reference_label"] = build_reference_label(document_name, item["locator"], item["page_number"])
        grouped.setdefault(int(item["mystery_id"]), []).append(item)
    return grouped


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "static" / "index.html")


@app.get("/settings")
def settings_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "static" / "settings.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/settings")
def get_settings(connection=Depends(get_connection)) -> dict[str, object]:
    row = connection.execute(
        "SELECT provider_type, base_url, model, api_key, temperature FROM settings WHERE id = 1"
    ).fetchone()
    return row_to_dict(row) or {}


@app.put("/api/settings")
def update_settings(payload: ProviderSettingsPayload, connection=Depends(get_connection)) -> dict[str, object]:
    connection.execute(
        """
        UPDATE settings
        SET provider_type = ?, base_url = ?, model = ?, api_key = ?, temperature = ?
        WHERE id = 1
        """,
        (payload.provider_type, payload.base_url, payload.model, payload.api_key, payload.temperature),
    )
    connection.commit()
    return get_settings(connection)


@app.get("/api/documents")
def list_documents(connection=Depends(get_connection)) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT documents.id, documents.original_name, documents.kind, documents.status,
               documents.progress_step_name, documents.progress_step_index, documents.progress_step_count,
               documents.error, documents.created_at,
               (SELECT COUNT(*) FROM sources WHERE sources.document_id = documents.id) AS source_count,
               (SELECT COUNT(*) FROM notes WHERE notes.document_id = documents.id) AS note_count,
               (SELECT COUNT(*) FROM unresolved_mysteries WHERE unresolved_mysteries.document_id = documents.id) AS mystery_count,
               (SELECT COUNT(*) FROM unresolved_mysteries WHERE unresolved_mysteries.document_id = documents.id AND unresolved_mysteries.status = 'open') AS open_mystery_count
        FROM documents
        ORDER BY documents.id DESC
        """
    ).fetchall()
    return [row_to_dict(row) for row in rows if row is not None]


@app.get("/api/documents/{document_id}")
def get_document(document_id: int, connection=Depends(get_connection)) -> dict[str, object]:
    document = connection.execute(
        """
        SELECT id, original_name, stored_path, media_type, kind, status,
               progress_step_name, progress_step_index, progress_step_count,
               error, created_at
        FROM documents WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    sources = connection.execute(
        """
        SELECT sources.id, sources.locator, sources.page_number, sources.content, sources.image_summary, sources.metadata_json,
               notes.note, notes.keywords
        FROM sources
        LEFT JOIN notes ON notes.source_id = sources.id
        WHERE sources.document_id = ?
        ORDER BY sources.id
        """,
        (document_id,),
    ).fetchall()
    payload = row_to_dict(document) or {}
    payload["sources"] = [row_to_dict(row) for row in sources if row is not None]
    mysteries = connection.execute(
        """
        SELECT unresolved_mysteries.id, unresolved_mysteries.question, unresolved_mysteries.reason, unresolved_mysteries.keywords,
               unresolved_mysteries.status, unresolved_mysteries.resolution_summary, unresolved_mysteries.created_at,
               unresolved_mysteries.resolved_at, origin_sources.locator AS origin_locator, origin_sources.page_number AS origin_page_number
        FROM unresolved_mysteries
        JOIN sources origin_sources ON origin_sources.id = unresolved_mysteries.source_id
        WHERE unresolved_mysteries.document_id = ?
        ORDER BY unresolved_mysteries.id
        """,
        (document_id,),
    ).fetchall()
    mystery_refs = serialize_mystery_refs(connection, [int(row["id"]) for row in mysteries], payload["original_name"])
    payload["mysteries"] = []
    for row in mysteries:
        item = dict(row)
        item["origin_reference_label"] = build_reference_label(
            payload["original_name"],
            item["origin_locator"],
            item["origin_page_number"],
        )
        item["references"] = mystery_refs.get(int(item["id"]), [])
        payload["mysteries"].append(item)
    return payload


@app.get("/api/messages")
def list_messages(connection=Depends(get_connection)) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT id, role, content, citations_json, created_at FROM messages ORDER BY id ASC"
    ).fetchall()
    return [serialize_message(row) for row in rows if row is not None]


@app.delete("/api/messages")
def clear_messages(connection=Depends(get_connection)) -> dict[str, bool]:
    connection.execute("DELETE FROM messages")
    connection.commit()
    return {"cleared": True}


@app.post("/api/upload")
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    connection=Depends(get_connection),
) -> dict[str, object]:
    try:
        document_id = create_document_ingestion(
            connection,
            config,
            original_name=file.filename or "upload.bin",
            media_type=file.content_type or "application/octet-stream",
            blob=file.file.read(),
        )
        background_tasks.add_task(continue_document_ingestion, config, document_id)
    except (ParserError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return get_document(document_id, connection)


@app.post("/api/chat")
def chat(payload: ChatRequest, background_tasks: BackgroundTasks, connection=Depends(get_connection)) -> dict[str, object]:
    try:
        request_payload = create_chat_request(connection, payload.message, payload.document_ids)
        background_tasks.add_task(continue_chat_request, config, request_payload["id"])
        return request_payload
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/chat/{request_id}")
def chat_request_status(request_id: str, connection=Depends(get_connection)) -> dict[str, object]:
    payload = get_chat_request(connection, request_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Chat request not found")
    return payload


def main() -> None:
    uvicorn.run("terrismen.app:app", host=config.host, port=config.port, reload=False)


if __name__ == "__main__":
    main()
