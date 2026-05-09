import { api, getSettingsState, summarizeSettings } from "./shared.js";

const state = {
  documents: [],
  selectedDocumentId: null,
  messages: [],
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
  detail: document.querySelector("#document-detail"),
  documentMeta: document.querySelector("#document-meta"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
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

function renderDocuments() {
  if (!state.documents.length) {
    elements.documents.className = "document-list empty document-empty-state";
    elements.documents.innerHTML = `
      <strong>No documents yet</strong>
      <p class="meta">Use the upload action above to add your first file and start generating notes.</p>
    `;
    return;
  }

  elements.documents.className = "document-list";
  elements.documents.innerHTML = state.documents
    .map((documentItem) => {
      const active = documentItem.id === state.selectedDocumentId ? " active" : "";
      return `
        <button class="document-card${active}" data-document-id="${documentItem.id}">
          <div class="split-header">
            <strong>${escapeHtml(documentItem.original_name)}</strong>
            <span class="tag">${escapeHtml(documentItem.status)}</span>
          </div>
          <div class="meta">${escapeHtml(documentItem.kind || "pending")} • ${documentItem.source_count} sources • ${documentItem.note_count} notes • ${documentItem.mystery_count || 0} mysteries${documentItem.open_mystery_count ? ` (${documentItem.open_mystery_count} open)` : ""}</div>
          ${documentItem.error ? `<div class="meta">${escapeHtml(documentItem.error)}</div>` : ""}
        </button>
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

function renderMysteries(mysteries) {
  if (!mysteries?.length) {
    return "";
  }
  return `
    <section class="detail-section">
      <div class="split-header">
        <h3>Unresolved mysteries</h3>
        <span class="meta">${mysteries.length} tracked</span>
      </div>
      ${mysteries
        .map(
          (mystery) => `
            <article class="mystery-card ${escapeHtml(mystery.status)}">
              <div class="split-header">
                <strong>${escapeHtml(mystery.question)}</strong>
                <span class="tag">${escapeHtml(mystery.status)}</span>
              </div>
              <div class="meta">Origin ${escapeHtml(mystery.origin_reference_label)}</div>
              ${mystery.keywords ? `<div class="meta">${escapeHtml(mystery.keywords)}</div>` : ""}
              <h3>Why it was uncertain</h3>
              <pre>${escapeHtml(mystery.reason || "No reason recorded")}</pre>
              ${
                mystery.resolution_summary
                  ? `<h3>${mystery.status === "resolved" ? "Resolution" : "Current review"}</h3><pre>${escapeHtml(mystery.resolution_summary)}</pre>`
                  : ""
              }
              ${renderReferenceTags(mystery.references)}
            </article>
          `,
        )
        .join("")}
    </section>
  `;
}

function renderSources(sources) {
  if (!sources?.length) {
    return "";
  }
  return `
    <section class="detail-section">
      <div class="split-header">
        <h3>Source notes</h3>
        <span class="meta">${sources.length} extracted units</span>
      </div>
      ${sources
        .map(
          (source) => `
            <details class="source-card source-card-collapsible">
              <summary class="source-summary">
                <div class="source-summary-copy">
                  <div class="split-header">
                    <strong>${escapeHtml(source.locator)}</strong>
                    ${source.page_number ? `<span class="tag">Ref ${source.page_number}</span>` : ""}
                  </div>
                  <div class="source-preview">${escapeHtml(summarizeSourceNote(source.note))}</div>
                  <div class="meta">${escapeHtml(source.keywords || "No keywords extracted")}</div>
                </div>
                <span class="tag source-toggle-label">
                  <span class="source-toggle-closed">Open note</span>
                  <span class="source-toggle-open">Close note</span>
                </span>
              </summary>
              <div class="source-expanded">
                <h3>Full note</h3>
                <pre>${escapeHtml(source.note || "No note generated")}</pre>
                <h3>Source excerpt</h3>
                <pre>${escapeHtml(source.content || "[no text extracted]")}</pre>
                ${
                  source.image_summary
                    ? `<h3>Image summary</h3><pre>${escapeHtml(source.image_summary)}</pre>`
                    : ""
                }
              </div>
            </details>
          `,
        )
        .join("")}
    </section>
  `;
}

function renderDocumentDetail(documentItem) {
  if (!documentItem) {
    elements.documentMeta.textContent = "";
    elements.detail.className = "detail empty";
    elements.detail.textContent = "Select a document to inspect its notes and source references.";
    return;
  }

  const sources = documentItem.sources || [];
  const mysteries = documentItem.mysteries || [];
  elements.documentMeta.textContent = `${documentItem.kind || "unknown"} • ${documentItem.status} • ${sources.length} sources • ${mysteries.length} mysteries`;
  if (!sources.length && !mysteries.length) {
    elements.detail.className = "detail empty";
    elements.detail.textContent = "This document does not have extracted notes or mysteries yet.";
    return;
  }

  elements.detail.className = "detail";
  elements.detail.innerHTML = [renderMysteries(mysteries), renderSources(sources)]
    .filter(Boolean)
    .join("");
}

function renderMessages() {
  if (!state.messages.length) {
    elements.chatLog.className = "chat-log empty";
    elements.chatLog.textContent = "Chat history will appear here.";
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
  renderDocuments();
  const targetDocumentId = state.selectedDocumentId ?? state.documents[0]?.id ?? null;
  if (targetDocumentId !== null) {
    await openDocument(targetDocumentId);
  } else {
    renderDocumentDetail(null);
  }
}

async function openDocument(documentId) {
  state.selectedDocumentId = Number(documentId);
  renderDocuments();
  const documentItem = await api(`/api/documents/${documentId}`);
  renderDocumentDetail(documentItem);
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

function setUploadBusy(isBusy) {
  const hasFile = Boolean(elements.uploadInput.files?.length);
  elements.uploadSubmit.disabled = isBusy || !hasFile;
  elements.uploadInput.disabled = isBusy;
  elements.uploadSubmit.textContent = isBusy ? "Starting processing..." : "Start processing document";
}

function renderUploadSelection() {
  const selectedFile = elements.uploadInput.files?.[0] ?? null;
  if (!selectedFile) {
    elements.uploadSelection.className = "upload-selection empty";
    elements.uploadSelectionName.textContent = "No file selected yet";
    elements.uploadSelectionMeta.textContent = "Pick a file to enable processing.";
    setUploadFeedback("Choose a file first to begin processing.");
    setUploadBusy(false);
    return;
  }

  elements.uploadSelection.className = "upload-selection";
  elements.uploadSelectionName.textContent = selectedFile.name;
  elements.uploadSelectionMeta.textContent = `${formatFileSize(selectedFile.size)} • Ready to process`;
  setUploadFeedback("Ready to start processing.");
  setUploadBusy(false);
}

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
    await openDocument(documentItem.id);
    setUploadFeedback(`Finished ${documentItem.original_name}. Review notes or ask grounded questions.`);
    setStatus(`Finished ${documentItem.original_name}`);
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
  await openDocument(target.dataset.documentId);
});

elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.chatInput.value.trim();
  if (!message) {
    return;
  }
  setStatus("Asking...");
  try {
    const userMessage = {
      id: `local-user-${Date.now()}`,
      role: "user",
      content: message,
      created_at: new Date().toISOString(),
      citations: [],
    };
    state.messages.push(userMessage);
    renderMessages();
    elements.chatInput.value = "";
    const assistant = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    state.messages = await api("/api/messages");
    if (!state.messages.find((item) => item.id === assistant.id)) {
      state.messages.push({ ...assistant, role: "assistant", created_at: new Date().toISOString() });
    }
    renderMessages();
    setStatus("Answer ready");
  } catch (error) {
    setStatus(error.message);
    await loadMessages();
  }
});

elements.clearChat.addEventListener("click", async () => {
  setStatus("Clearing chat...");
  try {
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
  setStatus("Ready");
} catch (error) {
  setStatus(error.message);
}
