const state = {
  documents: [],
  selectedDocumentId: null,
  messages: [],
};

const elements = {
  status: document.querySelector("#status-pill"),
  settingsForm: document.querySelector("#settings-form"),
  uploadForm: document.querySelector("#upload-form"),
  uploadInput: document.querySelector("#upload-input"),
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

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || `Request failed: ${response.status}`);
  }
  return payload;
}

function renderDocuments() {
  if (!state.documents.length) {
    elements.documents.className = "document-list empty";
    elements.documents.textContent = "No documents yet.";
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
            <article class="source-card">
              <div class="split-header">
                <strong>${escapeHtml(source.locator)}</strong>
                ${source.page_number ? `<span class="tag">Ref ${source.page_number}</span>` : ""}
              </div>
              <div class="meta">${escapeHtml(source.keywords || "No keywords extracted")}</div>
              <h3>Note</h3>
              <pre>${escapeHtml(source.note || "No note generated")}</pre>
              <h3>Source excerpt</h3>
              <pre>${escapeHtml(source.content || "[no text extracted]")}</pre>
              ${
                source.image_summary
                  ? `<h3>Image summary</h3><pre>${escapeHtml(source.image_summary)}</pre>`
                  : ""
              }
            </article>
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

async function loadSettings() {
  const settings = await api("/api/settings");
  elements.settingsForm.provider_type.value = settings.provider_type || "openai_compatible";
  elements.settingsForm.base_url.value = settings.base_url || "";
  elements.settingsForm.model.value = settings.model || "";
  elements.settingsForm.api_key.value = settings.api_key || "";
  elements.settingsForm.temperature.value = settings.temperature ?? 0.2;
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

elements.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Saving settings...");
  try {
    const formData = new FormData(elements.settingsForm);
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        provider_type: formData.get("provider_type"),
        base_url: formData.get("base_url"),
        model: formData.get("model"),
        api_key: formData.get("api_key"),
        temperature: Number(formData.get("temperature")),
      }),
    });
    setStatus("Settings saved");
  } catch (error) {
    setStatus(error.message);
  }
});

elements.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!elements.uploadInput.files?.length) {
    setStatus("Choose a file first");
    return;
  }
  setStatus("Uploading and taking notes...");
  try {
    const formData = new FormData();
    formData.append("file", elements.uploadInput.files[0]);
    const documentItem = await api("/api/upload", {
      method: "POST",
      body: formData,
    });
    state.selectedDocumentId = documentItem.id;
    elements.uploadForm.reset();
    await loadDocuments();
    await openDocument(documentItem.id);
    setStatus(`Finished ${documentItem.original_name}`);
  } catch (error) {
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

await Promise.all([loadSettings(), loadDocuments(), loadMessages()]);
setStatus("Ready");
