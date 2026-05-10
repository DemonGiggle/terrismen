from __future__ import annotations

import sqlite3
from pathlib import Path


def delete_document(connection: sqlite3.Connection, document_id: int) -> bool:
    row = connection.execute(
        "SELECT id, stored_path FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        return False

    file_paths = [Path(row["stored_path"])]
    image_rows = connection.execute(
        """
        SELECT source_images.image_path
        FROM source_images
        JOIN sources ON sources.id = source_images.source_id
        WHERE sources.document_id = ?
        """,
        (document_id,),
    ).fetchall()
    file_paths.extend(Path(image_row["image_path"]) for image_row in image_rows)

    connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    connection.commit()

    for file_path in file_paths:
        try:
            if file_path.exists() and file_path.is_file():
                file_path.unlink()
        except OSError:
            # Database cleanup is authoritative; file cleanup should not leave the UI stuck.
            continue
    return True
