# terrismen

`terrismen` is a document-grounded note taking and chat application with a browser UI. It ingests uploaded files, reads them page-by-page or chunk-by-chunk, creates dense notes through an LLM, and answers follow-up questions using both the generated notes and the original source excerpts.

## App snapshot

![terrismen app snapshot](assets/terrismen-snapshot.png)

## Features

- Browser UI with a dedicated settings page, guided upload workflow, compact note browsing, and grounded chat
- OpenAI-compatible provider support
- Native Ollama support
- Step-by-step document ingestion progress in the UI while uploads are processing
- Step-by-step chat request progress for retrieval and answer generation
- PDF parsing with exact page references
- DOCX, DOC, XLSX, XLS, and plaintext parsing with stable locators
- Image forwarding to the configured model for multimodal note enrichment
- Page-level unresolved mystery capture for ambiguous or incomplete content
- End-of-document batch mystery review with configurable batch size and reference mode
- Grounded chat flow that cites the referenced source locations
- Local SQLite persistence for documents, sources, notes, and chat history

## How it works

1. Save provider settings in the UI.
2. Upload a document.
3. `terrismen` extracts source units:
   - PDFs become one source per page
   - DOCX, DOC, and plaintext become chunked source units
   - Excel files become sheet/row-range source units
4. If images are found in supported formats, they are sent to the configured model and described.
5. After every source unit in a stable batch finishes image enrichment, the batch is sent to the model to generate retrieval-friendly notes that can cover one or several source units plus unresolved mysteries for any still-ambiguous source.
6. After the full document is read, `terrismen` revisits unresolved mysteries in stable batches, searches indexed notes, optionally includes source excerpts, and stores grounded per-mystery resolutions without forcing the whole batch to succeed or fail together.
7. During chat, `terrismen` searches the stored notes and mystery resolutions, asks the model to pick the most relevant references, then answers from the original source excerpts and chat history.

See [`docs/technical-overview.md`](docs/technical-overview.md) for the full ingestion, storage, mystery-resolution, and grounded-chat architecture, and [`docs/llm-prompts.md`](docs/llm-prompts.md) for the current prompt inventory.

## Requirements

- Python 3.11+
- A compatible model endpoint:
  - OpenAI-compatible: `POST /v1/chat/completions`
  - Ollama: `POST /api/chat`
- Optional: `antiword` for legacy `.doc` ingestion

## Run locally

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install --upgrade pip
pip install -e ".[dev]"
terrismen
```

By default the app listens on `http://127.0.0.1:8000`.

You can override the runtime path and bind address:

```bash
export TERRISMEN_DATA_ROOT=/tmp/terrismen-data
export TERRISMEN_HOST=0.0.0.0
export TERRISMEN_PORT=8000
terrismen
```

The Settings page shows the current data folder and can move it to a new location. If `TERRISMEN_DATA_ROOT` is set, that environment variable stays authoritative and the UI shows the path as locked.

## Mystery resolution settings

The Settings page also controls how the end-of-document mystery pass batches LLM calls:

- `mystery_resolution_batch_size`: default `5`, valid range `1-20`
- `mystery_resolution_reference_mode`: default `notes_only`, optional `notes_and_sources`

Tradeoffs:

- Smaller batch sizes reduce prompt size and timeout risk, but require more model calls.
- Larger batch sizes reduce repeated prompt overhead, but increase context size.
- `notes_only` keeps prompts cheaper by default and derives source refs from the chosen notes.
- `notes_and_sources` includes raw source excerpts in the prompt and lets the model select source refs directly.

If one mystery in a batch comes back malformed, the other valid results in that same batch are still applied. When the batch response is unusable, the affected mysteries stay open with parser-fallback summaries instead of being marked resolved.

## Document note batching setting

The Settings page stores `document_note_batch_size`, a separate batch-size control for batched document-note generation.

- default `5`, valid range `1-20`
- the unit is always **source units**, not only PDF pages:
  - PDF: pages
  - DOCX, DOC, TXT, MD, TEXT: chunks
  - XLSX, XLS: sheet row-group sections
- the control is intentionally separate from `mystery_resolution_batch_size`

Current behavior:

- source units are processed in stable batches for note generation
- every source unit in a batch finishes image enrichment before the batch note call runs
- one generated note can reference several related source units

## Provider examples

### OpenAI-compatible

- Provider: `openai_compatible`
- Base URL: `https://your-provider.example.com`
- Model: provider-specific model name
- API key: provider-specific
- Timeout: default `600` seconds, configurable in Settings

### Ollama

- Provider: `ollama`
- Base URL: `http://localhost:11434`
- Model: `llama3.2-vision` or another installed model
- API key: leave blank
- Timeout: default `600` seconds, configurable in Settings

## Notes on source references

- PDFs use exact page numbers.
- DOCX, DOC, Excel, and plaintext use stable locators when real page numbers are not exposed by the format.
- Chat citations are rendered from the stored reference labels so answers can point back to the originating material.

## Development

Set up the local development environment:

```bash
make dev-setup
```

Run the test suite:

```bash
make test
```

If `pytest` on your `PATH` points at a different Python than the project's virtualenv, prefer:

```bash
./.venv/bin/python -m pytest
```

Run the app locally:

```bash
make run
```

Enable debug logging for LLM round-trips:

```bash
DEBUG=1 make run
```

When debug mode is enabled, startup prints the debug log path. The log records one JSON line per LLM request start/end/timeout with duration, provider/model/endpoint, caller file and line, and ingestion or chat context such as the current document, step, source batch, or mystery batch.

### Database schema changes

`terrismen` applies SQLite schema migrations automatically through `init_db(...)` at startup and in tests.

- Future schema changes should be added as new numbered migrations in `terrismen/db.py`.
- The current schema is the supported baseline for databases created before migration metadata existed.
- Older schema shapes outside that baseline are not upgraded automatically and should be migrated manually before startup.
