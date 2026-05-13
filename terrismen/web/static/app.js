import { api, escapeHtml, getSettingsState, renderMarkdown, renderPlainText, summarizeSettings, typesetMath } from "./shared.js?v=asset-math-render-20260511";

const state = {
  documents: [],
  selectedDocumentId: null,
  checkedDocumentIds: new Set(),
  hasInitializedDocumentSelection: false,
  messages: [],
  documentRefreshTimer: null,
  documentRefreshInFlight: false,
  activeChatRequestId: null,
  chatRequestTimer: null,
  chatRequestInFlight: false,
  deletingDocumentIds: new Set(),
  resumingDocumentIds: new Set(),
};

const elements = {
  status: document.querySelector("#status-pill"),
  settingsSummary: document.querySelector("#settings-summary"),
  uploadForm: document.querySelector("#upload-form"),
  uploadInput: document.querySelector("#upload-input"),
  uploadSelection: document.querySelector("#upload-selection"),
  uploadSelectionName: document.querySelector("#upload-selection-name"),
  uploadSelectionMeta: document.querySelector("#upload-selection-meta"),
  uploadSubmit: document.querySelector("#upload-submit"),
  uploadFeedback: document.querySelector("#upload-feedback"),
  documents: document.querySelector("#documents"),
  sourceSelectionSummary: document.querySelector("#source-selection-summary"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  chatSubmit: document.querySelector("#chat-submit"),
  chatProgress: document.querySelector("#chat-progress"),
  chatScopeLabel: document.querySelector("#chat-scope-label"),
  chatLog: document.querySelector("#chat-log"),
  clearChat: document.querySelector("#clear-chat"),
};

window.addEventListener("terrismen:math-ready", () => {
  if (!elements.chatLog.classList.contains("empty")) {
    typesetMath(elements.chatLog);
  }
});

function setStatus(text) {
  elements.status.textContent = text;
}

function renderSettingsSummary(settings) {
  const settingsState = getSettingsState(settings);
  elements.settingsSummary.className = settingsState.isConfigured ? "meta" : "setup-notice";
  elements.settingsSummary.textContent = summarizeSettings(settings);
}

function isDocumentReady(documentItem) {
  return documentItem.status === "ready";
}

function getReadyCheckedDocumentIds() {
  const readyIds = new Set(state.documents.filter(isDocumentReady).map((documentItem) => documentItem.id));
  return [...state.checkedDocumentIds].filter((documentId) => readyIds.has(documentId));
}

function describeDocumentProgress(documentItem) {
  if (!documentItem.progress_step_name || !documentItem.progress_step_index || !documentItem.progress_step_count) {
    return "";
  }
  const prefix = documentItem.status === "failed" ? "Failed at" : "Step";
  return `${prefix} ${documentItem.progress_step_index}/${documentItem.progress_step_count}: ${documentItem.progress_step_name}`;
}

function describeDocumentProgressDetail(documentItem) {
  return documentItem.progress_detail || "";
}

function renderDocuments() {
  renderChatScope();
  if (!state.documents.length) {
    elements.documents.className = "document-list empty";
    elements.documents.textContent = "No documents yet.";
    return;
  }

  elements.documents.className = "document-list";
  elements.documents.innerHTML = state.documents
    .map((documentItem) => {
      const active = documentItem.id === state.selectedDocumentId ? " active" : "";
      const progress = describeDocumentProgress(documentItem);
      const progressDetail = describeDocumentProgressDetail(documentItem);
      const isReady = isDocumentReady(documentItem);
      const isUsedForChat = isReady && state.checkedDocumentIds.has(documentItem.id);
      return `
        <article class="document-card${active}" data-document-id="${documentItem.id}">
          <label class="document-select-row">
            <input
              type="checkbox"
              class="document-source-checkbox"
              data-document-checkbox-id="${documentItem.id}"
              ${isUsedForChat ? "checked" : ""}
              ${isReady ? "" : "disabled"}
              aria-label="Use ${escapeAttribute(documentItem.original_name)} for chat"
            >
            <span class="document-card-main">
              <span class="split-header">
                <strong>${escapeHtml(documentItem.original_name)}</strong>
              </span>
              ${progress ? `<span class="meta">${escapeHtml(progress)}</span>` : ""}
              ${progressDetail ? `<span class="meta">${escapeHtml(progressDetail)}</span>` : ""}
               <span class="meta">${escapeHtml(documentItem.kind || "pending")} • ${documentItem.source_count} sources • ${documentItem.note_count} notes • ${documentItem.malformed_note_count || 0} malformed notes • ${documentItem.mystery_count || 0} mysteries${documentItem.open_mystery_count ? ` (${documentItem.open_mystery_count} open)` : ""}</span>
              ${documentItem.error ? `<span class="meta">${escapeHtml(documentItem.error)}</span>` : ""}
            </span>
          </label>
          <div class="document-card-actions">
            ${
              documentItem.source_count || documentItem.note_count || documentItem.malformed_note_count || documentItem.mystery_count
                ? `<a class="button-link secondary compact-action" href="/documents/${documentItem.id}/notes" data-document-action="view">View</a>`
                : ""
            }
            ${
              documentItem.status === "failed" || (documentItem.status === "ready" && documentItem.malformed_note_count)
                ? `<button class="secondary compact-action" type="button" data-document-action="retry">Retry</button>`
                : ""
            }
            ${
              documentItem.status === "processing"
                ? `<button class="secondary compact-action" type="button" data-document-action="resume" ${state.resumingDocumentIds.has(documentItem.id) ? "disabled" : ""}>${state.resumingDocumentIds.has(documentItem.id) ? "Resuming..." : "Force resume"}</button>`
                : ""
            }
            <button class="secondary compact-action" type="button" data-document-action="delete" ${state.deletingDocumentIds.has(documentItem.id) ? "disabled" : ""}>${state.deletingDocumentIds.has(documentItem.id) ? "Deleting..." : "Delete"}</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderReferenceTags(references) {
  if (!references?.length) {
    return "";
  }
  return `<div class="citations">${references
    .map((reference) => `<span class="tag">${escapeHtml(reference.reference_label)}</span>`)
    .join("")}</div>`;
}

function summarizeSourceNote(note) {
  const preview = String(note || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!preview) {
    return "No note generated";
  }

  return preview.length > 160 ? `${preview.slice(0, 157)}...` : preview;
}

function renderMessages() {
  if (!state.messages.length) {
    elements.chatLog.className = "chat-log empty";
    elements.chatLog.textContent = "Ask a question to begin.";
    return;
  }

  elements.chatLog.className = "chat-log";
  elements.chatLog.innerHTML = state.messages
    .map(
      (message) => `
        <article class="message ${message.role}">
          <div class="split-header">
            <strong>${message.role === "assistant" ? "Assistant" : "You"}</strong>
            <span class="meta">${new Date(message.created_at).toLocaleString()}</span>
          </div>
          <div class="${message.role === "assistant" ? "markdown-content" : ""}">${
            message.role === "assistant" ? renderMarkdown(message.content) : renderPlainText(message.content)
          }</div>
          ${
            message.citations?.length
              ? `<div class="citations">${message.citations
                  .map((citation) => `<span class="tag">${escapeHtml(citation.reference_label)}</span>`)
                  .join("")}</div>`
              : ""
          }
        </article>
      `,
    )
    .join("");
  elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
  typesetMath(elements.chatLog);
}

function describeChatProgress(request) {
  const prefix = request.status === "failed" ? "Failed at" : "Step";
  return `${prefix} ${request.progress_step_index}/${request.progress_step_count}: ${request.progress_step_name}`;
}

function renderChatProgress(request) {
  if (!request) {
    elements.chatProgress.hidden = true;
    elements.chatProgress.className = "chat-progress empty";
    elements.chatProgress.textContent = "";
    return;
  }

  elements.chatProgress.hidden = false;
  elements.chatProgress.className = "chat-progress";
  if (request.status === "failed") {
    elements.chatProgress.textContent = [describeChatProgress(request), request.error || null].filter(Boolean).join(" • ");
    return;
  }
  if (request.status === "completed") {
    elements.chatProgress.textContent = "Answer ready";
    return;
  }
  elements.chatProgress.textContent = describeChatProgress(request);
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

async function loadSettingsSummary() {
  const settings = await api("/api/settings");
  renderSettingsSummary(settings);
}

async function loadDocuments() {
  state.documents = await api("/api/documents");
  syncSelectedDocument();
  renderDocuments();
  syncDocumentPolling();
}

function syncSelectedDocument() {
  if (!state.documents.length) {
    state.selectedDocumentId = null;
    state.checkedDocumentIds.clear();
    state.hasInitializedDocumentSelection = false;
    return;
  }
  const availableIds = new Set(state.documents.map((documentItem) => documentItem.id));
  state.checkedDocumentIds = new Set([...state.checkedDocumentIds].filter((documentId) => availableIds.has(documentId)));
  if (!state.hasInitializedDocumentSelection) {
    for (const documentItem of state.documents) {
      if (isDocumentReady(documentItem)) {
        state.checkedDocumentIds.add(documentItem.id);
      }
    }
    state.hasInitializedDocumentSelection = true;
  }
  if (!state.documents.some((documentItem) => documentItem.id === state.selectedDocumentId)) {
    state.selectedDocumentId = state.documents[0].id;
  }
}

function selectDocument(documentId) {
  state.selectedDocumentId = Number(documentId);
  renderDocuments();
}

async function deleteDocument(documentId) {
  const documentItem = state.documents.find((item) => item.id === documentId);
  const label = documentItem?.original_name || "this document";
  if (!window.confirm(`Delete ${label} and all processed notes, mysteries, and source files?`)) {
    return;
  }

  state.deletingDocumentIds.add(documentId);
  renderDocuments();
  setStatus(`Deleting ${label}...`);
  try {
    await api(`/api/documents/${documentId}`, { method: "DELETE" });
    state.checkedDocumentIds.delete(documentId);
    if (state.selectedDocumentId === documentId) {
      state.selectedDocumentId = null;
    }
    await loadDocuments();
    setStatus(`Deleted ${label}`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    state.deletingDocumentIds.delete(documentId);
    renderDocuments();
  }
}

async function retryDocument(documentId) {
  const documentItem = state.documents.find((item) => item.id === documentId);
  const label = documentItem?.original_name || "this document";
  setStatus(`Retrying ${label}...`);
  try {
    const payload = await api(`/api/documents/${documentId}/retry`, { method: "POST" });
    state.selectedDocumentId = payload.id;
    await loadDocuments();
    setStatus(`Restarted ${label}`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function resumeDocument(documentId) {
  const documentItem = state.documents.find((item) => item.id === documentId);
  const label = documentItem?.original_name || "this document";
  if (!window.confirm(`Force resume ${label}? Use this only when processing got stuck after a restart.`)) {
    return;
  }

  state.resumingDocumentIds.add(documentId);
  renderDocuments();
  setStatus(`Resuming ${label}...`);
  try {
    const payload = await api(`/api/documents/${documentId}/resume`, { method: "POST" });
    state.selectedDocumentId = payload.id;
    await loadDocuments();
    setStatus(`Resumed ${label}`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    state.resumingDocumentIds.delete(documentId);
    renderDocuments();
  }
}

async function loadMessages() {
  state.messages = await api("/api/messages");
  renderMessages();
}

function formatFileSize(size) {
  if (!Number.isFinite(size) || size <= 0) {
    return "Size unavailable";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function setUploadFeedback(text) {
  elements.uploadFeedback.textContent = text;
}

function hasProcessingDocuments() {
  return state.documents.some((documentItem) => documentItem.status === "processing");
}

function setChatBusy(isBusy) {
  const lockedForIngestion = hasProcessingDocuments();
  const isDisabled = isBusy || lockedForIngestion;
  elements.chatInput.disabled = isDisabled;
  elements.chatSubmit.disabled = isDisabled;
  elements.clearChat.disabled = isBusy;
  elements.chatSubmit.textContent = isBusy ? "Working..." : "Ask";
  if (lockedForIngestion && !isBusy) {
    elements.chatProgress.hidden = false;
    elements.chatProgress.className = "chat-progress";
    elements.chatProgress.textContent = "Chat will unlock when processing finishes.";
  } else if (!lockedForIngestion && !isBusy && state.activeChatRequestId === null) {
    renderChatProgress(null);
  }
}

function getCheckedDocumentIds() {
  return getReadyCheckedDocumentIds();
}

function renderChatScope() {
  const count = getReadyCheckedDocumentIds().length;
  if (!state.documents.length) {
    elements.chatScopeLabel.textContent = "Add documents to start";
    elements.sourceSelectionSummary.textContent = "0 selected";
    return;
  }
  const readyCount = state.documents.filter(isDocumentReady).length;
  elements.chatScopeLabel.textContent = count === 1 ? "Using 1 document" : `Using ${count} documents`;
  elements.sourceSelectionSummary.textContent = `${count} of ${readyCount} selected`;
}

function setUploadBusy(isBusy) {
  const hasFile = Boolean(elements.uploadInput.files?.length);
  elements.uploadSubmit.disabled = isBusy || !hasFile;
  elements.uploadSubmit.hidden = !isBusy && !hasFile;
  elements.uploadInput.disabled = isBusy;
  elements.uploadSubmit.textContent = isBusy ? "Starting processing..." : "Start Process";
}

function renderUploadSelection() {
  const selectedFile = elements.uploadInput.files?.[0] ?? null;
  if (!selectedFile) {
    elements.uploadSelection.className = "upload-selection empty";
    elements.uploadSelectionName.textContent = "";
    elements.uploadSelectionMeta.textContent = "";
    setUploadFeedback("");
    setUploadBusy(false);
    return;
  }

  elements.uploadSelection.className = "upload-selection";
  elements.uploadSelectionName.textContent = selectedFile.name;
  elements.uploadSelectionMeta.textContent = `${formatFileSize(selectedFile.size)} • Ready to process`;
  setUploadFeedback("Ready.");
  setUploadBusy(false);
}

function syncDocumentPolling() {
  const processing = hasProcessingDocuments();
  setChatBusy(state.activeChatRequestId !== null);
  if (processing && state.documentRefreshTimer === null) {
    state.documentRefreshTimer = window.setInterval(refreshProcessingDocuments, 1500);
    return;
  }
  if (!processing && state.documentRefreshTimer !== null) {
    window.clearInterval(state.documentRefreshTimer);
    state.documentRefreshTimer = null;
  }
}

async function refreshProcessingDocuments() {
  if (state.documentRefreshInFlight) {
    return;
  }
  state.documentRefreshInFlight = true;
  try {
    await loadDocuments();
  } finally {
    state.documentRefreshInFlight = false;
  }
}

function syncChatPolling() {
  if (state.activeChatRequestId !== null && state.chatRequestTimer === null) {
    state.chatRequestTimer = window.setInterval(refreshActiveChatRequest, 1200);
    return;
  }
  if (state.activeChatRequestId === null && state.chatRequestTimer !== null) {
    window.clearInterval(state.chatRequestTimer);
    state.chatRequestTimer = null;
  }
}

async function refreshActiveChatRequest() {
  if (state.chatRequestInFlight || state.activeChatRequestId === null) {
    return;
  }
  state.chatRequestInFlight = true;
  try {
    const request = await api(`/api/chat/${state.activeChatRequestId}`);
    renderChatProgress(request);
    if (request.status === "completed") {
      state.activeChatRequestId = null;
      syncChatPolling();
      await loadMessages();
      setChatBusy(false);
      setStatus("Answer ready");
      return;
    }
    if (request.status === "failed") {
      state.activeChatRequestId = null;
      syncChatPolling();
      await loadMessages();
      setChatBusy(false);
      setStatus(request.error || "Chat request failed");
      return;
    }
  } finally {
    state.chatRequestInFlight = false;
  }
}

window.addEventListener("beforeunload", () => {
  if (state.documentRefreshTimer !== null) {
    window.clearInterval(state.documentRefreshTimer);
  }
  if (state.chatRequestTimer !== null) {
    window.clearInterval(state.chatRequestTimer);
  }
});

elements.uploadInput.addEventListener("change", () => {
  renderUploadSelection();
});

elements.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!elements.uploadInput.files?.length) {
    setStatus("Choose a file first");
    return;
  }
  const selectedFile = elements.uploadInput.files[0];
  setUploadBusy(true);
  setUploadFeedback(`Starting processing for ${selectedFile.name}...`);
  setStatus("Uploading and taking notes...");
  try {
    const formData = new FormData();
    formData.append("file", selectedFile);
    const documentItem = await api("/api/upload", {
      method: "POST",
      body: formData,
    });
    state.selectedDocumentId = documentItem.id;
    elements.uploadForm.reset();
    renderUploadSelection();
    await loadDocuments();
    state.checkedDocumentIds.add(documentItem.id);
    renderDocuments();
    setUploadFeedback(`Processing ${documentItem.original_name}...`);
    setStatus(`Started ${documentItem.original_name}`);
  } catch (error) {
    setUploadBusy(false);
    setUploadFeedback(error.message);
    setStatus(error.message);
  }
});

elements.documents.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-document-id]");
  if (!target) {
    return;
  }
  const action = event.target.closest("[data-document-action]");
  if (action) {
    if (action.dataset.documentAction === "delete") {
      await deleteDocument(Number(target.dataset.documentId));
    } else if (action.dataset.documentAction === "retry") {
      await retryDocument(Number(target.dataset.documentId));
    } else if (action.dataset.documentAction === "resume") {
      await resumeDocument(Number(target.dataset.documentId));
    }
    return;
  }
  if (event.target.closest(".document-select-row")) {
    return;
  }
  selectDocument(target.dataset.documentId);
});

elements.documents.addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-document-checkbox-id]");
  if (!checkbox) {
    return;
  }
  const documentId = Number(checkbox.dataset.documentCheckboxId);
  if (checkbox.checked) {
    state.checkedDocumentIds.add(documentId);
  } else {
    state.checkedDocumentIds.delete(documentId);
  }
  const selectedCount = getReadyCheckedDocumentIds().length;
  setStatus(`${selectedCount} source${selectedCount === 1 ? "" : "s"} selected`);
  renderDocuments();
});

elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.chatInput.value.trim();
  if (!message) {
    return;
  }
  if (hasProcessingDocuments()) {
    setStatus("Chat will unlock when processing finishes");
    setChatBusy(false);
    return;
  }
  setChatBusy(true);
  setStatus("Starting chat...");
  try {
    const request = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, document_ids: getCheckedDocumentIds() }),
    });
    elements.chatInput.value = "";
    state.activeChatRequestId = request.id;
    renderChatProgress(request);
    syncChatPolling();
    await loadMessages();
    setStatus(describeChatProgress(request));
  } catch (error) {
    setChatBusy(false);
    setStatus(error.message);
    await loadMessages();
  }
});

elements.clearChat.addEventListener("click", async () => {
  setStatus("Clearing chat...");
  try {
    state.activeChatRequestId = null;
    syncChatPolling();
    setChatBusy(false);
    renderChatProgress(null);
    await api("/api/messages", { method: "DELETE" });
    state.messages = [];
    renderMessages();
    setStatus("Chat cleared");
  } catch (error) {
    setStatus(error.message);
  }
});

try {
  await Promise.all([loadSettingsSummary(), loadDocuments(), loadMessages()]);
  renderUploadSelection();
  renderChatProgress(null);
  setStatus("Ready");
} catch (error) {
  setStatus(error.message);
}
