import { api, getSettingsState, summarizeSettings } from "./shared.js";

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
};

const elements = {
  status: document.querySelector("#status-pill"),
  settingsSummary: document.querySelector("#settings-summary"),
  settingsIndicator: document.querySelector("#settings-indicator"),
  uploadForm: document.querySelector("#upload-form"),
  uploadInput: document.querySelector("#upload-input"),
  uploadSelection: document.querySelector("#upload-selection"),
  uploadSelectionName: document.querySelector("#upload-selection-name"),
  uploadSelectionMeta: document.querySelector("#upload-selection-meta"),
  uploadSubmit: document.querySelector("#upload-submit"),
  uploadFeedback: document.querySelector("#upload-feedback"),
  documents: document.querySelector("#documents"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  chatSubmit: document.querySelector("#chat-submit"),
  chatProgress: document.querySelector("#chat-progress"),
  chatScopeLabel: document.querySelector("#chat-scope-label"),
  chatLog: document.querySelector("#chat-log"),
  clearChat: document.querySelector("#clear-chat"),
};

function setStatus(text) {
  elements.status.textContent = text;
}

function renderSettingsSummary(settings) {
  const settingsState = getSettingsState(settings);
  elements.settingsIndicator.textContent = settingsState.label;
  elements.settingsIndicator.className = settingsState.className;
  elements.settingsSummary.textContent = summarizeSettings(settings);
}

function describeDocumentState(status) {
  return status === "ready" ? "complete" : status;
}

function describeDocumentProgress(documentItem) {
  if (!documentItem.progress_step_name || !documentItem.progress_step_index || !documentItem.progress_step_count) {
    return "";
  }
  const prefix = documentItem.status === "failed" ? "Failed at" : "Step";
  return `${prefix} ${documentItem.progress_step_index}/${documentItem.progress_step_count}: ${documentItem.progress_step_name}`;
}

function renderDocuments() {
  renderChatScope();
  if (!state.documents.length) {
    elements.documents.className = "document-list empty document-empty-state";
    elements.documents.innerHTML = `
      <strong>No documents yet</strong>
      <p class="meta">Add one to start chatting.</p>
    `;
    return;
  }

  elements.documents.className = "document-list";
  elements.documents.innerHTML = state.documents
    .map((documentItem) => {
      const active = documentItem.id === state.selectedDocumentId ? " active" : "";
      const progress = describeDocumentProgress(documentItem);
      return `
        <article class="document-card${active}" data-document-id="${documentItem.id}">
          <label class="document-select-row">
            <input
              type="checkbox"
              class="document-source-checkbox"
              data-document-checkbox-id="${documentItem.id}"
              ${state.checkedDocumentIds.has(documentItem.id) ? "checked" : ""}
              aria-label="Use ${escapeAttribute(documentItem.original_name)} for chat"
            >
            <span class="document-card-main">
              <span class="split-header">
                <strong>${escapeHtml(documentItem.original_name)}</strong>
                <span class="tag">${escapeHtml(describeDocumentState(documentItem.status))}</span>
              </span>
              ${progress ? `<span class="meta">${escapeHtml(progress)}</span>` : ""}
              <span class="meta">${escapeHtml(documentItem.kind || "pending")} • ${documentItem.source_count} sources • ${documentItem.note_count} notes • ${documentItem.mystery_count || 0} mysteries${documentItem.open_mystery_count ? ` (${documentItem.open_mystery_count} open)` : ""}</span>
              ${documentItem.error ? `<span class="meta">${escapeHtml(documentItem.error)}</span>` : ""}
            </span>
          </label>
          <div class="document-card-actions">
            <a class="button-link secondary compact-action" href="/documents/${documentItem.id}/notes" data-document-action="view">View</a>
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

function renderPlainText(value) {
  return escapeHtml(value).replace(/\n/g, "<br>");
}

function renderMarkdown(value) {
  const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fenceMatch = line.match(/^```(\w+)?\s*$/);
    if (fenceMatch) {
      index += 1;
      const codeLines = [];
      while (index < lines.length && !lines[index].match(/^```\s*$/)) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      const language = fenceMatch[1] ? ` data-language="${escapeAttribute(fenceMatch[1])}"` : "";
      blocks.push(`<pre class="code-block"${language}><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length + 2;
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderInlineMarkdown(quoteLines.join("\n")).replace(/\n/g, "<br>")}</blockquote>`);
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      blocks.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }

    const paragraphLines = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].match(/^```(\w+)?\s*$/) &&
      !lines[index].match(/^(#{1,3})\s+(.+)$/) &&
      !/^>\s?/.test(lines[index]) &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !/^\s*\d+[.)]\s+/.test(lines[index])
    ) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${renderInlineMarkdown(paragraphLines.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }

  return blocks.join("");
}

function renderInlineMarkdown(value) {
  const links = [];
  let html = replaceMarkdownLinks(escapeHtml(value), links);
  html = renderInlineStyles(html);
  return html.replace(/\u0000LINK(\d+)\u0000/g, (_, linkIndex) => links[Number(linkIndex)]);
}

function renderInlineStyles(value) {
  const codeSpans = [];
  const html = value
    .replace(/`([^`]+)`/g, (_, code) => {
      codeSpans.push(`<code>${code}</code>`);
      return `\u0000CODE${codeSpans.length - 1}\u0000`;
    })
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/_([^_]+)_/g, "<em>$1</em>");

  return html.replace(/\u0000CODE(\d+)\u0000/g, (_, codeIndex) => codeSpans[Number(codeIndex)]);
}

function replaceMarkdownLinks(value, links) {
  let result = "";
  let index = 0;

  while (index < value.length) {
    const linkStart = value.indexOf("[", index);
    if (linkStart === -1) {
      result += value.slice(index);
      break;
    }

    result += value.slice(index, linkStart);
    const parsedLink = parseMarkdownLink(value, linkStart);
    if (!parsedLink) {
      result += value[linkStart];
      index = linkStart + 1;
      continue;
    }

    const decodedUrl = decodeHtmlEntities(parsedLink.url);
    if (!isSafeUrl(decodedUrl)) {
      result += parsedLink.label;
      index = parsedLink.end;
      continue;
    }

    links.push(
      `<a href="${escapeAttribute(decodedUrl)}" target="_blank" rel="noopener noreferrer">${renderInlineStyles(parsedLink.label)}</a>`,
    );
    result += `\u0000LINK${links.length - 1}\u0000`;
    index = parsedLink.end;
  }

  return result;
}

function parseMarkdownLink(value, startIndex) {
  const labelEnd = value.indexOf("]", startIndex + 1);
  if (labelEnd === -1 || value[labelEnd + 1] !== "(") {
    return null;
  }

  let urlEnd = labelEnd + 2;
  let depth = 1;
  while (urlEnd < value.length) {
    const character = value[urlEnd];
    if (/\s/.test(character)) {
      return null;
    }
    if (character === "(") {
      depth += 1;
    } else if (character === ")") {
      depth -= 1;
      if (depth === 0) {
        break;
      }
    }
    urlEnd += 1;
  }

  if (depth !== 0) {
    return null;
  }

  return {
    label: value.slice(startIndex + 1, labelEnd),
    url: value.slice(labelEnd + 2, urlEnd),
    end: urlEnd + 1,
  };
}

function decodeHtmlEntities(value) {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = value;
  return textarea.value;
}

function isSafeUrl(value) {
  try {
    const parsed = new URL(value, window.location.origin);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol);
  } catch {
    return false;
  }
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
      if (documentItem.status === "ready") {
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

async function loadMessages() {
  state.messages = await api("/api/messages");
  renderMessages();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
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
  return [...state.checkedDocumentIds];
}

function renderChatScope() {
  const count = state.checkedDocumentIds.size;
  if (!state.documents.length) {
    elements.chatScopeLabel.textContent = "Add documents to start";
    return;
  }
  elements.chatScopeLabel.textContent = count === 1 ? "Using 1 document" : `Using ${count} documents`;
}

function setUploadBusy(isBusy) {
  const hasFile = Boolean(elements.uploadInput.files?.length);
  elements.uploadSubmit.disabled = isBusy || !hasFile;
  elements.uploadInput.disabled = isBusy;
  elements.uploadSubmit.textContent = isBusy ? "Starting processing..." : "Add Doc";
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
    }
    return;
  }
  const checkbox = event.target.closest("[data-document-checkbox-id]");
  if (checkbox) {
    const documentId = Number(checkbox.dataset.documentCheckboxId);
    if (checkbox.checked) {
      state.checkedDocumentIds.add(documentId);
    } else {
      state.checkedDocumentIds.delete(documentId);
    }
    setStatus(`${state.checkedDocumentIds.size} source${state.checkedDocumentIds.size === 1 ? "" : "s"} selected`);
    renderDocuments();
    return;
  }
  selectDocument(target.dataset.documentId);
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
