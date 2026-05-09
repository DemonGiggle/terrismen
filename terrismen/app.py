from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from terrismen.config import AppConfig, load_config
from terrismen.db import connect, init_db, row_to_dict
from terrismen.models import ChatRequest, ProviderSettingsPayload
from terrismen.services.chat import answer_question, save_message
from terrismen.services.ingestion import ingest_document
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "static" / "index.html")


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
        SELECT documents.id, documents.original_name, documents.kind, documents.status, documents.error, documents.created_at,
               COUNT(DISTINCT sources.id) AS source_count, COUNT(DISTINCT notes.id) AS note_count
        FROM documents
        LEFT JOIN sources ON sources.document_id = documents.id
        LEFT JOIN notes ON notes.document_id = documents.id
        GROUP BY documents.id
        ORDER BY documents.id DESC
        """
    ).fetchall()
    return [row_to_dict(row) for row in rows if row is not None]


@app.get("/api/documents/{document_id}")
def get_document(document_id: int, connection=Depends(get_connection)) -> dict[str, object]:
    document = connection.execute(
        """
        SELECT id, original_name, stored_path, media_type, kind, status, error, created_at
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
def upload_document(file: UploadFile = File(...), connection=Depends(get_connection)) -> dict[str, object]:
    try:
        document_id = ingest_document(
            connection,
            config,
            original_name=file.filename or "upload.bin",
            media_type=file.content_type or "application/octet-stream",
            blob=file.file.read(),
        )
    except (ParserError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return get_document(document_id, connection)


@app.post("/api/chat")
def chat(payload: ChatRequest, connection=Depends(get_connection)) -> dict[str, object]:
    save_message(connection, "user", payload.message, [])
    try:
        return answer_question(connection, payload.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def main() -> None:
    uvicorn.run("terrismen.app:app", host=config.host, port=config.port, reload=False)


if __name__ == "__main__":
    main()
