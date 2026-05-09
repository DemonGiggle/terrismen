from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from terrismen.services.parsers import parse_document


def test_parse_text_creates_chunk_locators(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    kind, sources = parse_document(file_path, tmp_path / "images")

    assert kind == "text"
    assert len(sources) == 1
    assert sources[0].locator.startswith("Chunk 1")
    assert "alpha" in sources[0].content


def test_parse_xlsx_extracts_sheet_rows(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Specs"
    sheet.append(["Requirement", "Value"])
    sheet.append(["Timeout", 30])
    sheet.append(["Retries", 3])
    file_path = tmp_path / "specs.xlsx"
    workbook.save(file_path)

    kind, sources = parse_document(file_path, tmp_path / "images")

    assert kind == "xlsx"
    assert len(sources) == 1
    assert sources[0].locator == "Sheet Specs rows 1-3"
    assert "Requirement | Value" in sources[0].content
