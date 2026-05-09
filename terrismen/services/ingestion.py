from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from terrismen.config import AppConfig
from terrismen.db import utcnow
from terrismen.llm import ProviderSettings, build_provider
from terrismen.llm.base import ProviderError
from terrismen.services.notes import describe_images, generate_note
from terrismen.services.parsers import ParserError, parse_document


def file_extension(name: str) -> str:
    return Path(name).suffix.lower()


def allowed_extension(name: str) -> bool:
    return file_extension(name) in {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md", ".text"}


def load_provider_settings(connection: sqlite3.Connection) -> ProviderSettings:
    row = connection.execute(
        "SELECT provider_type, base_url, model, api_key, temperature FROM settings WHERE id = 1"
    ).fetchone()
    return ProviderSettings(
        provider_type=row["provider_type"],
        base_url=row["base_url"],
        model=row["model"],
        api_key=row["api_key"],
        temperature=row["temperature"],
    )


def ingest_document(
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
        INSERT INTO documents (original_name, stored_path, media_type, kind, status, error, created_at)
        VALUES (?, ?, ?, '', 'processing', '', ?)
        """,
        (original_name, str(stored_path), media_type or "application/octet-stream", utcnow()),
    )
    document_id = int(cursor.lastrowid)
    connection.commit()

    try:
        kind, parsed_sources = parse_document(stored_path, config.images_dir)
        provider = build_provider(settings)
        connection.execute("UPDATE documents SET kind = ? WHERE id = ?", (kind, document_id))

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
            note_text, keywords = generate_note(provider, original_name, source, image_descriptions)
            connection.execute(
                """
                INSERT INTO notes (document_id, source_id, note, keywords, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, source_id, note_text, keywords, utcnow()),
            )

        connection.execute("UPDATE documents SET status = 'ready', error = '' WHERE id = ?", (document_id,))
        connection.commit()
        return document_id
    except Exception as exc:
        connection.execute("UPDATE documents SET status = 'failed', error = ? WHERE id = ?", (str(exc), document_id))
        connection.commit()
        raise
