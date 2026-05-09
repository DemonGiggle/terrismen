from __future__ import annotations

import re

from terrismen.llm.base import BaseProvider
from terrismen.services.parsers import ParsedSource


NOTE_SYSTEM_PROMPT = """You create dense study notes from source material.

Requirements:
- Preserve important spec requirements, technical flows, caveats, thresholds, and edge cases.
- Do not flatten important details into vague summaries.
- Mention image observations when supplied.
- End with a line formatted exactly as: Keywords: item1, item2, item3
"""

IMAGE_PROMPT = """Describe this image in a way that helps document note taking.
Focus on labels, diagrams, tables, captions, relationships, and anything that changes the meaning of the surrounding text."""


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


def generate_note(provider: BaseProvider, document_name: str, source: ParsedSource, image_descriptions: list[str]) -> tuple[str, str]:
    reference_label = build_reference_label(document_name, source.locator, source.page_number)
    image_block = "\n".join(f"- {item}" for item in image_descriptions) if image_descriptions else "- None"
    user_prompt = (
        f"Reference: {reference_label}\n"
        f"Locator: {source.locator}\n"
        f"Source text:\n{source.content or '[no text extracted]'}\n\n"
        f"Image descriptions:\n{image_block}\n\n"
        "Write note-taking output that is useful for later retrieval and question answering."
    )
    note_text = provider.complete(NOTE_SYSTEM_PROMPT, user_prompt)
    keywords = extract_keywords(note_text)
    return note_text.strip(), keywords


def extract_keywords(note_text: str) -> str:
    match = re.search(r"^Keywords:\s*(.+)$", note_text, re.MULTILINE)
    return match.group(1).strip() if match else ""
