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
          <div class="meta">${escapeHtml(documentItem.kind || "pending")} • ${documentItem.source_count} sources • ${documentItem.note_count} notes</div>
          ${documentItem.error ? `<div class="meta">${escapeHtml(documentItem.error)}</div>` : ""}
        </button>
      `;
    })
    .join("");
}

function renderDocumentDetail(documentItem) {
  if (!documentItem) {
    elements.documentMeta.textContent = "";
    elements.detail.className = "detail empty";
    elements.detail.textContent = "Select a document to inspect its notes and source references.";
    return;
  }

  elements.documentMeta.textContent = `${documentItem.kind || "unknown"} • ${documentItem.status}`;
  if (!documentItem.sources?.length) {
    elements.detail.className = "detail empty";
    elements.detail.textContent = "This document does not have extracted sources yet.";
    return;
  }

  elements.detail.className = "detail";
  elements.detail.innerHTML = documentItem.sources
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
          <div>${escapeHtml(message.content).replace(/\n/g, "<br>")}</div>
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
  if (state.selectedDocumentId) {
    await openDocument(state.selectedDocumentId);
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
