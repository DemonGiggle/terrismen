from __future__ import annotations

import json
import re
from dataclasses import dataclass

from terrismen.llm.base import BaseProvider
from terrismen.services.parsers import ParsedSource


@dataclass(slots=True)
class MysteryDraft:
    question: str
    reason: str
    keywords: str


@dataclass(slots=True)
class GeneratedNote:
    note_text: str
    keywords: str
    mysteries: list[MysteryDraft]


@dataclass(slots=True)
class MysteryResolution:
    status: str
    summary: str
    note_ids: list[int]
    source_ids: list[int]


NOTE_SYSTEM_PROMPT = """You create dense study notes from source material.

Requirements:
- Preserve important spec requirements, technical flows, caveats, thresholds, and edge cases.
- Do not flatten important details into vague summaries.
- Mention image observations when supplied.
- Return JSON only in this shape:
  {
    "note": "dense note text",
    "keywords": ["item1", "item2"],
    "mysteries": [
      {
        "question": "what remains unclear or unresolved?",
        "reason": "why it is still unclear after reading this page",
        "keywords": ["item1", "item2"]
      }
    ]
  }
- Only include mysteries when the source truly leaves an ambiguity, missing definition, unresolved reference, conflicting statement, or unclear diagram detail.
- Do not invent mysteries if the content is already clear.
"""

IMAGE_PROMPT = """Describe this image in a way that helps document note taking.
Focus on labels, diagrams, tables, captions, relationships, and anything that changes the meaning of the surrounding text."""

MYSTERY_RESOLUTION_PROMPT = """You review unresolved document questions after the full document has been read.

Return JSON only in this shape:
{
  "status": "resolved" or "open",
  "summary": "concise explanation grounded in the provided material",
  "note_ids": [1, 2],
  "source_ids": [3, 4]
}

Rules:
- Use only the candidate notes and source excerpts provided.
- Mark the mystery as resolved only when the provided evidence clearly answers it.
- If the evidence is weak or still incomplete, keep it open and explain what is still missing.
- Reference candidate IDs exactly as given.
"""


def build_reference_label(document_name: str, locator: str, page_number: int | None) -> str:
    if page_number is not None and locator.startswith("Page "):
        return f"{document_name} - Page {page_number}"
    return f"{document_name} - {locator}"


def describe_images(provider: BaseProvider, source: ParsedSource) -> list[str]:
    descriptions: list[str] = []
    for image_path, image in source.images:
        prompt = (
            f"Document locator: {source.locator}\n"
            f"Surrounding text:\n{source.content[:2000] or '[no extracted text]'}\n\n"
            f"Describe the image faithfully."
        )
        description = provider.complete(IMAGE_PROMPT, prompt, images=[image])
        descriptions.append(f"{image_path.name}: {description.strip()}")
    return descriptions


def _extract_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced:
        try:
            payload = json.loads(fenced.group(1))
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            pass

    inline = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not inline:
        return {}
    try:
        payload = json.loads(inline.group(0))
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _normalize_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        parts = [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
        return list(dict.fromkeys(parts))
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return list(dict.fromkeys(parts))
    return []


def _normalize_int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    resolved: list[int] = []
    for item in value:
        if isinstance(item, int):
            resolved.append(item)
    return resolved


def _format_note_text(note_body: str, keywords: list[str]) -> str:
    note = note_body.strip()
    if keywords:
        return f"{note}\nKeywords: {', '.join(keywords)}"
    return note


def _parse_mysteries(value: object) -> list[MysteryDraft]:
    if not isinstance(value, list):
        return []
    mysteries: list[MysteryDraft] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        reason = str(item.get("reason", "")).strip()
        keywords = ", ".join(_normalize_string_list(item.get("keywords", [])))
        mysteries.append(MysteryDraft(question=question, reason=reason, keywords=keywords))
    return mysteries


def generate_note(
    provider: BaseProvider,
    document_name: str,
    source: ParsedSource,
    image_descriptions: list[str],
) -> GeneratedNote:
    reference_label = build_reference_label(document_name, source.locator, source.page_number)
    image_block = "\n".join(f"- {item}" for item in image_descriptions) if image_descriptions else "- None"
    user_prompt = (
        f"Reference: {reference_label}\n"
        f"Locator: {source.locator}\n"
        f"Source text:\n{source.content or '[no text extracted]'}\n\n"
        f"Image descriptions:\n{image_block}\n\n"
        "Write note-taking output that is useful for later retrieval and question answering."
    )
    response = provider.complete(NOTE_SYSTEM_PROMPT, user_prompt).strip()
    payload = _extract_json_object(response)
    if not payload:
        note_text = response
        return GeneratedNote(note_text=note_text, keywords=extract_keywords(note_text), mysteries=[])

    note_body = str(payload.get("note", "")).strip() or response
    keyword_items = _normalize_string_list(payload.get("keywords", []))
    note_text = _format_note_text(note_body, keyword_items)
    return GeneratedNote(
        note_text=note_text,
        keywords=", ".join(keyword_items),
        mysteries=_parse_mysteries(payload.get("mysteries", [])),
    )


def resolve_mystery(
    provider: BaseProvider,
    document_name: str,
    mystery: dict[str, object],
    candidates: list[dict[str, object]],
) -> MysteryResolution:
    reference_label = build_reference_label(
        document_name,
        str(mystery.get("origin_locator") or mystery.get("locator") or "Unknown reference"),
        mystery.get("origin_page_number") if isinstance(mystery.get("origin_page_number"), int) else None,
    )
    candidate_lines = []
    for candidate in candidates:
        candidate_reference = build_reference_label(
            document_name,
            str(candidate["locator"]),
            candidate["page_number"] if isinstance(candidate["page_number"], int) else None,
        )
        candidate_lines.append(
            f"note_id={candidate['note_id']} | source_id={candidate['source_id']} | reference={candidate_reference}\n"
            f"Note:\n{candidate['note']}\n\nSource excerpt:\n{candidate['content']}"
        )

    response = provider.complete(
        MYSTERY_RESOLUTION_PROMPT,
        (
            f"Original mystery reference: {reference_label}\n"
            f"Mystery question: {mystery['question']}\n"
            f"Why it was uncertain: {mystery.get('reason', '') or '[no reason recorded]'}\n"
            f"Keywords: {mystery.get('keywords', '') or '[none]'}\n\n"
            f"Candidate material:\n\n" + "\n\n---\n\n".join(candidate_lines)
        ),
    ).strip()
    payload = _extract_json_object(response)
    status = str(payload.get("status", "open")).strip().lower()
    if status not in {"resolved", "open"}:
        status = "open"
    summary = str(payload.get("summary", "")).strip() or response
    return MysteryResolution(
        status=status,
        summary=summary,
        note_ids=_normalize_int_list(payload.get("note_ids", [])),
        source_ids=_normalize_int_list(payload.get("source_ids", [])),
    )


def extract_keywords(note_text: str) -> str:
    match = re.search(r"^Keywords:\s*(.+)$", note_text, re.MULTILINE)
    return match.group(1).strip() if match else ""
