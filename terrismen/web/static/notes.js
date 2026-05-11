import { api, escapeHtml, renderMarkdown } from "./shared.js?v=asset-notes-markdown-20260511";

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

function renderEmpty(payload) {
  elements.list.innerHTML = `
    <div class="empty-state">
      <strong>No ${payload.note_type === "mystery" ? "unresolved questions" : "notes"} yet.</strong>
      <p>${payload.note_type === "mystery" ? "Questions found during processing will appear here." : "Notes will appear here after processing."}</p>
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
      <div class="markdown-content">${renderMarkdown(item.note)}</div>
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

function renderPayload(payload) {
  elements.title.textContent = payload.document.original_name;
  elements.subtitle.textContent = `${payload.document.status} document`;
  elements.count.textContent = `${payload.total} ${payload.note_type === "mystery" ? "questions" : "notes"}`;
  state.totalPages = payload.total_pages;
  elements.pageLabel.textContent = payload.total_pages ? `Page ${payload.page} of ${payload.total_pages}` : "Page 0 of 0";
  elements.previous.disabled = payload.page <= 1;
  elements.next.disabled = payload.total_pages === 0 || payload.page >= payload.total_pages;

  if (!payload.items.length) {
    renderEmpty(payload);
    return;
  }
  elements.list.innerHTML = payload.items.map((item) => (
    payload.note_type === "mystery" ? renderMysteryNote(item) : renderNormalNote(item)
  )).join("");
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
