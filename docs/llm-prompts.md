# LLM prompts and prompt workflow

`terrismen` currently has **5 prompt constants** that are sent to the LLM as the **system** message.

## Where prompts are sent

All prompt constants flow through `BaseProvider.complete(system_prompt, user_prompt, ...)`.

- `terrismen/llm/openai_compatible.py` sends `system_prompt` as `{"role": "system", "content": system_prompt}` to `/v1/chat/completions`.
- `terrismen/llm/ollama.py` sends `system_prompt` as `{"role": "system", "content": system_prompt}` to `/api/chat`.

That means the prompt constants below are not just internal strings; each one becomes the system prompt for a model call.

## Prompt inventory

| Prompt | File | Used by | When it is sent |
| --- | --- | --- | --- |
| `NOTE_SYSTEM_PROMPT` | `terrismen/services/notes.py` | `generate_note(...)` | Once per parsed source unit during ingestion, after parsing and after optional image descriptions are prepared |
| `IMAGE_PROMPT` | `terrismen/services/notes.py` | `describe_images(...)` | Once per extracted image during ingestion, before note generation for that source unit |
| `MYSTERY_RESOLUTION_PROMPT` | `terrismen/services/notes.py` | `resolve_mystery(...)` | Once per unresolved mystery during the end-of-document resolution pass |
| `REFERENCE_PICKER_PROMPT` | `terrismen/services/chat.py` | `_pick_source_ids(...)` | Once per chat request, after candidate notes and mystery matches are retrieved |
| `ANSWER_PROMPT` | `terrismen/services/chat.py` | `answer_question(...)` | Once per chat request, after the relevant source IDs are selected and source blocks are loaded |

## Workflow and data flow

### Ingestion flow

```text
User uploads document
        |
        v
/api/upload (FastAPI)
        |
        v
ingest_document(...)
        |
        +--> parse_document(...)
        |         |
        |         v
        |    parsed source units
        |
        +--> for each source unit
        |         |
        |         +--> extracted images?
        |         |         |
        |         |         +--> yes --> describe_images(...)
        |         |                       system: IMAGE_PROMPT
        |         |                       user: locator + nearby text + image bytes
        |         |
        |         +--> generate_note(...)
        |                   system: NOTE_SYSTEM_PROMPT
        |                   user: reference + source text + image descriptions
        |                   |
        |                   v
        |              note + keywords + mystery drafts
        |                   |
        |                   +--> store notes
        |                   +--> store unresolved mysteries
        |
        +--> _resolve_document_mysteries(...)
                  |
                  +--> search candidate notes/source excerpts
                  |
                  +--> resolve_mystery(...)
                            system: MYSTERY_RESOLUTION_PROMPT
                            user: original mystery + candidate note/source evidence
                            |
                            v
                       resolved/open status + referenced note/source IDs
                            |
                            v
                       store resolution state and refs
```

### Chat flow

```text
User asks question
        |
        v
/api/chat (FastAPI)
        |
        v
answer_question(...)
        |
        +--> recent_messages(...)
        |
        +--> search_candidate_notes(...)
        |         |
        |         v
        |    matching notes + mystery resolutions
        |
        +--> _pick_source_ids(...)
        |         system: REFERENCE_PICKER_PROMPT
        |         user: question + recent history + candidate notes
        |         |
        |         v
        |    selected source_ids
        |
        +--> _load_sources(...)
        |
        +--> provider.complete(...)
                  system: ANSWER_PROMPT
                  user: question + recent history + supporting material
                  |
                  v
             grounded answer with citations
```

## Current prompts

### `NOTE_SYSTEM_PROMPT`

```text
You create dense study notes from source material.

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
```

### `IMAGE_PROMPT`

```text
Describe this image in a way that helps document note taking.
Focus on labels, diagrams, tables, captions, relationships, and anything that changes the meaning of the surrounding text.
```

### `MYSTERY_RESOLUTION_PROMPT`

```text
You review unresolved document questions after the full document has been read.

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
```

### `REFERENCE_PICKER_PROMPT`

```text
You choose which source references are relevant for answering a user question.
Return JSON only in the shape {"source_ids":[1,2,3]}.
Only include source IDs that are clearly relevant.
```

### `ANSWER_PROMPT`

```text
You answer only from the supplied source excerpts and notes.
- Give a helpful, direct answer.
- Cite claims inline with square brackets using the supplied reference labels.
- If the sources are insufficient, say so plainly.
```
