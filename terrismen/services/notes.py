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
class BatchNoteSourceInput:
    source_id: int
    reference_label: str
    locator: str
    page_number: int | None
    content: str
    image_descriptions: list[str]


@dataclass(slots=True)
class BatchMysteryDraft:
    source_id: int
    question: str
    reason: str
    keywords: str


@dataclass(slots=True)
class BatchGeneratedNote:
    source_ids: list[int]
    note_text: str
    keywords: str
    mysteries: list[BatchMysteryDraft]


@dataclass(slots=True)
class ParsedBatchNotes:
    notes: list[BatchGeneratedNote]
    missing_source_ids: list[int]
    raw_response: str = ""
    error_type: str = ""
    invalid_item_count: int = 0


@dataclass(slots=True)
class MysteryResolution:
    status: str
    summary: str
    note_ids: list[int]
    source_ids: list[int]


@dataclass(slots=True)
class MysteryResolutionCandidate:
    note_id: int
    source_id: int
    reference_label: str
    note: str
    source_excerpt: str


@dataclass(slots=True)
class MysteryResolutionRequest:
    mystery_id: int
    reference_label: str
    question: str
    reason: str
    keywords: str
    candidates: list[MysteryResolutionCandidate]


@dataclass(slots=True)
class MysteryBatchResolution:
    mystery_id: int
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

BATCH_NOTE_SYSTEM_PROMPT = """You create dense retrieval-friendly notes from one or more related document source units.

Requirements:
- Preserve important requirements, technical flows, caveats, thresholds, and edge cases.
- Do not flatten important details into vague summaries.
- Mention image observations when supplied.
- Prefer one note per source unit unless nearby source units are tightly related and clearly benefit from one combined note.
- Every input source_id should appear in at most one returned note.
- If a note covers several source units, list source_ids in primary-to-secondary order and put the main source first.
- Every mystery must name exactly one origin source_id, and that source_id must appear in the same note's source_ids.
- Return JSON only in this exact shape:
  {
    "notes": [
      {
        "source_ids": [101, 102],
        "note": "dense note text",
        "keywords": ["item1", "item2"],
        "mysteries": [
          {
            "source_id": 101,
            "question": "what remains unclear or unresolved?",
            "reason": "why it is still unclear after reading the provided source unit",
            "keywords": ["item1", "item2"]
          }
        ]
      }
    ]
  }
- Use only the provided source_ids exactly as given.
- Return JSON only, with no markdown fences or extra prose.
"""

IMAGE_PROMPT = """Describe this image in a way that helps document note taking.
Focus on labels, diagrams, tables, captions, relationships, and anything that changes the meaning of the surrounding text."""

MYSTERY_RESOLUTION_BATCH_PROMPT = """You review unresolved document questions after the full document has been read.

Return JSON only in this exact shape:
{
  "results": [
    {
      "mystery_id": 101,
      "status": "resolved" or "open",
      "summary": "concise explanation grounded in the provided material",
      "note_ids": [11, 14],
      "source_ids": [21]
    }
  ]
}

Rules:
- Use only the candidate notes and candidate source excerpts provided for each mystery.
- Never invent or rename mystery ids, note ids, or source ids.
- Reference candidate IDs exactly as given for the matching mystery.
- Mark a mystery as resolved only when the provided evidence clearly answers it.
- If the evidence is weak or still incomplete, keep it open and explain what is still missing.
- Each mystery_id must appear at most once in results.
- Return JSON only, with no markdown fences or extra prose.
"""

MYSTERY_RESOLUTION_NOTE_CHAR_LIMIT = 1200
MYSTERY_RESOLUTION_SOURCE_CHAR_LIMIT = 1200
MYSTERY_BATCH_INVALID_SUMMARY = (
    "The model returned an invalid batch response, so this mystery remains open until it can be reviewed again."
)
MYSTERY_ITEM_INVALID_SUMMARY = (
    "The model returned an invalid result for this mystery, so it remains open until it can be reviewed again."
)
MYSTERY_OPEN_DEFAULT_SUMMARY = "The provided reference material still does not clearly resolve this mystery."
MYSTERY_RESOLVED_DEFAULT_SUMMARY = "This mystery was resolved from the provided reference material."


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


def _decode_json_object(text: str) -> dict[str, object]:
    try:
        payload, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    if not stripped:
        return {}
    payload = _decode_json_object(stripped)
    if payload:
        return payload

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced:
        payload = _decode_json_object(fenced.group(1))
        if payload:
            return payload

    for match in re.finditer(r"\{", stripped):
        payload = _decode_json_object(stripped[match.start() :])
        if payload:
            return payload
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
        normalized = _normalize_optional_int(item)
        if normalized is not None:
            resolved.append(normalized)
    return resolved


def _normalize_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


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


def _parse_batch_mysteries(value: object, allowed_source_ids: set[int]) -> list[BatchMysteryDraft]:
    if not isinstance(value, list):
        return []
    mysteries: list[BatchMysteryDraft] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source_id = _normalize_optional_int(item.get("source_id"))
        question = str(item.get("question", "")).strip()
        if source_id is None or source_id not in allowed_source_ids or not question:
            continue
        reason = str(item.get("reason", "")).strip()
        keywords = ", ".join(_normalize_string_list(item.get("keywords", [])))
        mysteries.append(
            BatchMysteryDraft(source_id=source_id, question=question, reason=reason, keywords=keywords)
        )
    return mysteries


def _trim_prompt_text(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def build_batch_note_prompt(sources: list[BatchNoteSourceInput]) -> str:
    payload = {
        "sources": [
            {
                "source_id": source.source_id,
                "reference": source.reference_label,
                "locator": source.locator,
                "page_number": source.page_number,
                "source_text": source.content or "[no text extracted]",
                "image_descriptions": source.image_descriptions,
            }
            for source in sources
        ]
    }
    return (
        "Create one or more retrieval-friendly notes from these source units. "
        "Use every source_id at most once across the returned notes.\n\n"
        "Batch input:\n"
        + json.dumps(payload, indent=2)
    )


def _parse_batch_note_item(
    item: dict[str, object],
    *,
    allowed_source_ids: set[int],
    covered_source_ids: set[int],
) -> BatchGeneratedNote | None:
    source_ids_value = item.get("source_ids")
    if not isinstance(source_ids_value, list):
        return None
    source_ids = _normalize_int_list(source_ids_value)
    if len(source_ids) != len(source_ids_value) or not source_ids:
        return None
    if len(source_ids) != len(set(source_ids)):
        return None
    if any(source_id not in allowed_source_ids or source_id in covered_source_ids for source_id in source_ids):
        return None
    note_body = str(item.get("note", "")).strip()
    if not note_body:
        return None
    keyword_items = _normalize_string_list(item.get("keywords", []))
    mysteries = _parse_batch_mysteries(item.get("mysteries", []), set(source_ids))
    return BatchGeneratedNote(
        source_ids=source_ids,
        note_text=_format_note_text(note_body, keyword_items),
        keywords=", ".join(keyword_items),
        mysteries=mysteries,
    )


def parse_batch_notes_response(response: str, sources: list[BatchNoteSourceInput]) -> ParsedBatchNotes:
    input_source_ids = [source.source_id for source in sources]
    payload = _extract_json_object(response)
    notes_payload = payload.get("notes")
    if not isinstance(notes_payload, list):
        return ParsedBatchNotes(
            notes=[],
            missing_source_ids=input_source_ids,
            raw_response=response,
            error_type="missing_notes_array",
        )

    allowed_source_ids = set(input_source_ids)
    covered_source_ids: set[int] = set()
    parsed_notes: list[BatchGeneratedNote] = []
    invalid_item_count = 0
    for item in notes_payload:
        if not isinstance(item, dict):
            invalid_item_count += 1
            continue
        parsed = _parse_batch_note_item(
            item,
            allowed_source_ids=allowed_source_ids,
            covered_source_ids=covered_source_ids,
        )
        if parsed is None:
            invalid_item_count += 1
            continue
        covered_source_ids.update(parsed.source_ids)
        parsed_notes.append(parsed)
    missing_source_ids = [source_id for source_id in input_source_ids if source_id not in covered_source_ids]
    error_type = ""
    if missing_source_ids:
        error_type = "no_valid_notes" if not parsed_notes else "partial_coverage"
    elif invalid_item_count:
        error_type = "ignored_invalid_items"
    return ParsedBatchNotes(
        notes=parsed_notes,
        missing_source_ids=missing_source_ids,
        raw_response=response,
        error_type=error_type,
        invalid_item_count=invalid_item_count,
    )


def generate_batch_notes(provider: BaseProvider, sources: list[BatchNoteSourceInput]) -> ParsedBatchNotes:
    if not sources:
        return ParsedBatchNotes(notes=[], missing_source_ids=[])
    response = provider.complete(BATCH_NOTE_SYSTEM_PROMPT, build_batch_note_prompt(sources)).strip()
    return parse_batch_notes_response(response, sources)


def build_mystery_resolution_request(
    document_name: str,
    mystery: dict[str, object],
    candidates: list[dict[str, object]],
) -> MysteryResolutionRequest:
    reference_label = build_reference_label(
        document_name,
        str(mystery.get("origin_locator") or mystery.get("locator") or "Unknown reference"),
        mystery.get("origin_page_number") if isinstance(mystery.get("origin_page_number"), int) else None,
    )
    normalized_candidates: list[MysteryResolutionCandidate] = []
    for candidate in candidates:
        note_id = _normalize_optional_int(candidate.get("note_id"))
        source_id = _normalize_optional_int(candidate.get("source_id"))
        if note_id is None or source_id is None:
            continue
        candidate_reference = build_reference_label(
            document_name,
            str(candidate["locator"]),
            candidate["page_number"] if isinstance(candidate["page_number"], int) else None,
        )
        normalized_candidates.append(
            MysteryResolutionCandidate(
                note_id=note_id,
                source_id=source_id,
                reference_label=candidate_reference,
                note=str(candidate.get("note", "")),
                source_excerpt=str(candidate.get("content", "")),
            )
        )
    mystery_id = _normalize_optional_int(mystery.get("id"))
    if mystery_id is None:
        raise ValueError("Mystery id is required for batch resolution.")
    return MysteryResolutionRequest(
        mystery_id=mystery_id,
        reference_label=reference_label,
        question=str(mystery.get("question", "")).strip(),
        reason=str(mystery.get("reason", "")).strip(),
        keywords=str(mystery.get("keywords", "")).strip(),
        candidates=normalized_candidates,
    )


def build_mystery_resolution_batch_prompt(
    requests: list[MysteryResolutionRequest],
    *,
    include_source_excerpts: bool = True,
) -> str:
    mysteries_payload: list[dict[str, object]] = []
    for request in requests:
        candidate_notes = []
        candidate_sources_by_id: dict[int, dict[str, object]] = {}
        for candidate in request.candidates:
            candidate_notes.append(
                {
                    "note_id": candidate.note_id,
                    "source_id": candidate.source_id,
                    "reference": candidate.reference_label,
                    "note": _trim_prompt_text(candidate.note, MYSTERY_RESOLUTION_NOTE_CHAR_LIMIT),
                }
            )
            if include_source_excerpts and candidate.source_id not in candidate_sources_by_id:
                candidate_sources_by_id[candidate.source_id] = {
                    "source_id": candidate.source_id,
                    "reference": candidate.reference_label,
                    "excerpt": _trim_prompt_text(
                        candidate.source_excerpt,
                        MYSTERY_RESOLUTION_SOURCE_CHAR_LIMIT,
                    ),
                }
        mysteries_payload.append(
            {
                "mystery_id": request.mystery_id,
                "reference": request.reference_label,
                "question": request.question,
                "reason": request.reason or "[no reason recorded]",
                "keywords": _normalize_string_list(request.keywords),
                "candidate_notes": candidate_notes,
                "candidate_sources": list(candidate_sources_by_id.values()) if include_source_excerpts else [],
            }
        )
    return (
        "Review each mystery independently and return one result per mystery_id.\n\n"
        "Batch input:\n"
        + json.dumps({"mysteries": mysteries_payload}, indent=2)
    )


def _default_summary_for_status(status: str) -> str:
    return MYSTERY_RESOLVED_DEFAULT_SUMMARY if status == "resolved" else MYSTERY_OPEN_DEFAULT_SUMMARY


def _fallback_batch_results(
    requests: list[MysteryResolutionRequest],
    summary: str,
) -> list[MysteryBatchResolution]:
    return [
        MysteryBatchResolution(
            mystery_id=request.mystery_id,
            status="open",
            summary=summary,
            note_ids=[],
            source_ids=[],
        )
        for request in requests
    ]


def _parse_batch_result_item(
    item: dict[str, object],
    request: MysteryResolutionRequest,
) -> MysteryBatchResolution | None:
    status = str(item.get("status", "")).strip().lower()
    if status not in {"resolved", "open"}:
        return None
    note_ids = _normalize_int_list(item.get("note_ids", []))
    source_ids = _normalize_int_list(item.get("source_ids", []))
    allowed_note_ids = {candidate.note_id for candidate in request.candidates}
    allowed_source_ids = {candidate.source_id for candidate in request.candidates}
    if any(note_id not in allowed_note_ids for note_id in note_ids):
        return None
    if any(source_id not in allowed_source_ids for source_id in source_ids):
        return None
    summary = str(item.get("summary", "")).strip() or _default_summary_for_status(status)
    return MysteryBatchResolution(
        mystery_id=request.mystery_id,
        status=status,
        summary=summary,
        note_ids=list(dict.fromkeys(note_ids)),
        source_ids=list(dict.fromkeys(source_ids)),
    )


def parse_mystery_resolution_batch_response(
    response: str,
    requests: list[MysteryResolutionRequest],
) -> list[MysteryBatchResolution]:
    payload = _extract_json_object(response)
    results_payload = payload.get("results")
    if not isinstance(results_payload, list):
        return _fallback_batch_results(requests, MYSTERY_BATCH_INVALID_SUMMARY)

    request_by_id = {request.mystery_id: request for request in requests}
    parsed_by_id: dict[int, MysteryBatchResolution] = {}
    for item in results_payload:
        if not isinstance(item, dict):
            continue
        mystery_id = _normalize_optional_int(item.get("mystery_id"))
        if mystery_id is None or mystery_id not in request_by_id or mystery_id in parsed_by_id:
            continue
        parsed = _parse_batch_result_item(item, request_by_id[mystery_id])
        if parsed is None:
            parsed_by_id[mystery_id] = MysteryBatchResolution(
                mystery_id=mystery_id,
                status="open",
                summary=MYSTERY_ITEM_INVALID_SUMMARY,
                note_ids=[],
                source_ids=[],
            )
            continue
        parsed_by_id[mystery_id] = parsed

    results: list[MysteryBatchResolution] = []
    for request in requests:
        results.append(
            parsed_by_id.get(
                request.mystery_id,
                MysteryBatchResolution(
                    mystery_id=request.mystery_id,
                    status="open",
                    summary=MYSTERY_ITEM_INVALID_SUMMARY,
                    note_ids=[],
                    source_ids=[],
                ),
            )
        )
    return results


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
    *,
    include_source_excerpts: bool = True,
) -> MysteryResolution:
    request_payload = dict(mystery)
    request_payload.setdefault("id", 1)
    request = build_mystery_resolution_request(document_name, request_payload, candidates)
    response = provider.complete(
        MYSTERY_RESOLUTION_BATCH_PROMPT,
        build_mystery_resolution_batch_prompt([request], include_source_excerpts=include_source_excerpts),
    ).strip()
    result = parse_mystery_resolution_batch_response(response, [request])[0]
    return MysteryResolution(
        status=result.status,
        summary=result.summary,
        note_ids=result.note_ids,
        source_ids=result.source_ids,
    )


def resolve_mysteries(
    provider: BaseProvider,
    requests: list[MysteryResolutionRequest],
    *,
    include_source_excerpts: bool = True,
) -> list[MysteryBatchResolution]:
    if not requests:
        return []
    response = provider.complete(
        MYSTERY_RESOLUTION_BATCH_PROMPT,
        build_mystery_resolution_batch_prompt(requests, include_source_excerpts=include_source_excerpts),
    ).strip()
    return parse_mystery_resolution_batch_response(response, requests)


def extract_keywords(note_text: str) -> str:
    match = re.search(r"^Keywords:\s*(.+)$", note_text, re.MULTILINE)
    return match.group(1).strip() if match else ""
