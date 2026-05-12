import { api, escapeHtml, renderMarkdown, typesetMath } from "./shared.js?v=asset-math-render-20260511";

const PAGE_SIZE = 10;
const documentId = Number(window.location.pathname.match(/\/documents\/(\d+)\/notes/)?.[1]);

const state = {
  noteType: "normal",
  page: 1,
  totalPages: 0,
};

const elements = {
  title: document.querySelector("#notes-title"),
  subtitle: document.querySelector("#notes-subtitle"),
  filter: document.querySelector("#note-type-filter"),
  count: document.querySelector("#notes-count"),
  list: document.querySelector("#notes-list"),
  previous: document.querySelector("#notes-prev"),
  next: document.querySelector("#notes-next"),
  pageLabel: document.querySelector("#notes-page-label"),
};

window.addEventListener("terrismen:math-ready", () => {
  if (elements.list.childElementCount) {
    typesetMath(elements.list);
  }
});

function renderEmpty(payload) {
  const label = payload.note_type === "mystery"
    ? "unresolved questions"
    : payload.note_type === "malformed"
      ? "malformed notes"
      : "notes";
  const description = payload.note_type === "mystery"
    ? "Questions found during processing will appear here."
    : payload.note_type === "malformed"
      ? "Malformed model outputs that could not be stored as normal notes will appear here."
      : "Notes will appear here after processing.";
  elements.list.innerHTML = `
    <div class="empty-state">
      <strong>No ${label} yet.</strong>
      <p>${description}</p>
    </div>
  `;
}

function stripTrailingKeywordsLine(note) {
  return String(note || "").replace(/\n+Keywords:\s*[^\n]+?\s*$/i, "").trimEnd();
}

function renderReferenceTags(references) {
  if (!Array.isArray(references) || references.length < 2) {
    return "";
  }
  return `
    <div class="meta">
      References:
      ${references.map((reference) => `<span class="tag">${escapeHtml(reference.reference_label)}</span>`).join(" ")}
    </div>
  `;
}

function renderNormalNote(item) {
  return `
    <article class="note-card">
      <div class="split-header">
        <strong>${escapeHtml(item.reference_label)}</strong>
        <span class="tag">Note</span>
      </div>
      ${renderReferenceTags(item.references)}
      <div class="markdown-content">${renderMarkdown(stripTrailingKeywordsLine(item.note))}</div>
      ${item.keywords ? `<div class="meta">Keywords: ${escapeHtml(item.keywords)}</div>` : ""}
    </article>
  `;
}

function renderMysteryNote(item) {
  return `
    <article class="note-card mystery-card">
      <div class="split-header">
        <strong>${escapeHtml(item.reference_label)}</strong>
        <span class="tag">${escapeHtml(item.status)}</span>
      </div>
      <h3>${escapeHtml(item.question)}</h3>
      ${item.reason ? `<p>${escapeHtml(item.reason)}</p>` : ""}
      ${item.resolution_summary ? `<p><strong>Resolution:</strong> ${escapeHtml(item.resolution_summary)}</p>` : ""}
      ${item.keywords ? `<div class="meta">Keywords: ${escapeHtml(item.keywords)}</div>` : ""}
    </article>
  `;
}

function renderMalformedNote(item) {
  return `
    <article class="note-card mystery-card">
      <div class="split-header">
        <strong>${escapeHtml(item.reference_label)}</strong>
        <span class="tag">${escapeHtml(item.error_type || "malformed")}</span>
      </div>
      <p>${escapeHtml(item.error_detail || "The model response could not be stored as a normal note.")}</p>
      <div class="meta">Source ID: ${escapeHtml(String(item.source_id))}</div>
      ${item.raw_response ? `<details><summary>Raw model response</summary><pre>${escapeHtml(item.raw_response)}</pre></details>` : ""}
    </article>
  `;
}

function renderPayload(payload) {
  elements.title.textContent = payload.document.original_name;
  elements.subtitle.textContent = `${payload.document.status} document`;
  elements.count.textContent = `${payload.total} ${
    payload.note_type === "mystery" ? "questions" : payload.note_type === "malformed" ? "malformed notes" : "notes"
  }`;
  state.totalPages = payload.total_pages;
  elements.pageLabel.textContent = payload.total_pages ? `Page ${payload.page} of ${payload.total_pages}` : "Page 0 of 0";
  elements.previous.disabled = payload.page <= 1;
  elements.next.disabled = payload.total_pages === 0 || payload.page >= payload.total_pages;

  if (!payload.items.length) {
    renderEmpty(payload);
    return;
  }
  elements.list.innerHTML = payload.items.map((item) => (
    payload.note_type === "mystery" ? renderMysteryNote(item) : payload.note_type === "malformed" ? renderMalformedNote(item) : renderNormalNote(item)
  )).join("");
  typesetMath(elements.list);
}

async function loadNotes() {
  if (!documentId) {
    elements.list.innerHTML = '<div class="empty-state"><strong>Missing document id.</strong></div>';
    return;
  }
  elements.count.textContent = "Loading…";
  elements.list.innerHTML = '<div class="empty-state">Loading notes…</div>';
  try {
    const payload = await api(`/api/documents/${documentId}/notes?note_type=${state.noteType}&page=${state.page}&page_size=${PAGE_SIZE}`);
    renderPayload(payload);
  } catch (error) {
    elements.count.textContent = "Error";
    elements.list.innerHTML = `<div class="empty-state"><strong>${escapeHtml(error.message)}</strong></div>`;
  }
}

elements.filter.addEventListener("change", () => {
  state.noteType = elements.filter.value;
  state.page = 1;
  loadNotes();
});

elements.previous.addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    loadNotes();
  }
});

elements.next.addEventListener("click", () => {
  if (state.page < state.totalPages) {
    state.page += 1;
    loadNotes();
  }
});

loadNotes();
