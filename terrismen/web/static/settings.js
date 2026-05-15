import { api, getSettingsState, summarizeSettings } from "./shared.js?v=asset-split-think-level-20260515";

const elements = {
  status: document.querySelector("#status-pill"),
  settingsForm: document.querySelector("#settings-form"),
  settingsSummary: document.querySelector("#settings-summary"),
  settingsIndicator: document.querySelector("#settings-indicator"),
  dataRootSummary: document.querySelector("#data-root-summary"),
  dataRootHint: document.querySelector("#data-root-hint"),
  ingestionThinkLevelHint: document.querySelector("#ingestion-think-level-hint"),
  chatThinkLevelHint: document.querySelector("#chat-think-level-hint"),
};

function setStatus(text) {
  elements.status.textContent = text;
}

function renderSettingsSummary(settings) {
  const state = getSettingsState(settings);
  elements.settingsIndicator.textContent = state.label;
  elements.settingsIndicator.className = state.className;
  elements.settingsSummary.textContent = summarizeSettings(settings);
  elements.dataRootSummary.textContent = `Current data path: ${settings.data_root || "Not configured"}`;
  elements.dataRootHint.textContent = settings.data_root_locked
    ? "This path is locked by the TERRISMEN_DATA_ROOT environment variable."
    : "Changing the data folder moves the current database and uploads to the new location.";
}

function populateSettingsForm(settings) {
  elements.settingsForm.data_root.value = settings.data_root || "";
  elements.settingsForm.data_root.readOnly = Boolean(settings.data_root_locked);
  elements.settingsForm.provider_type.value = settings.provider_type || "openai_compatible";
  elements.settingsForm.base_url.value = settings.base_url || "";
  elements.settingsForm.model.value = settings.model || "";
  elements.settingsForm.api_key.value = settings.api_key || "";
  elements.settingsForm.temperature.value = settings.temperature ?? 0.2;
  elements.settingsForm.llm_timeout_seconds.value = settings.llm_timeout_seconds ?? 600;
  const legacyThinkLevel = settings.think_level || "off";
  elements.settingsForm.ingestion_think_level.value = settings.ingestion_think_level || legacyThinkLevel;
  elements.settingsForm.chat_think_level.value = settings.chat_think_level || legacyThinkLevel;
  elements.settingsForm.document_note_batch_size.value = settings.document_note_batch_size ?? 5;
  elements.settingsForm.mystery_resolution_batch_size.value = settings.mystery_resolution_batch_size ?? 5;
  elements.settingsForm.mystery_resolution_reference_mode.value =
    settings.mystery_resolution_reference_mode || "notes_only";
  syncThinkLevelControls();
}

function syncThinkLevelControls() {
  const isOllama = elements.settingsForm.provider_type.value === "ollama";
  elements.settingsForm.ingestion_think_level.disabled = !isOllama;
  elements.settingsForm.chat_think_level.disabled = !isOllama;
  if (!isOllama) {
    elements.ingestionThinkLevelHint.textContent =
      "Available only for Ollama. OpenAI-compatible providers keep their current request shape.";
    elements.chatThinkLevelHint.textContent =
      "Available only for Ollama. OpenAI-compatible providers keep their current request shape.";
    return;
  }
  elements.ingestionThinkLevelHint.textContent =
    "Used for document note generation and mystery resolution. Most Ollama models treat any non-off value as thinking enabled; GPT-OSS honors low, medium, and high.";
  elements.chatThinkLevelHint.textContent =
    "Used for grounded chat source selection and final answers. Most Ollama models treat any non-off value as thinking enabled; GPT-OSS honors low, medium, and high.";
}

async function loadSettings() {
  const settings = await api("/api/settings");
  populateSettingsForm(settings);
  renderSettingsSummary(settings);
}

elements.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Saving settings...");
  try {
    const formData = new FormData(elements.settingsForm);
    const savedSettings = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        data_root: formData.get("data_root"),
        provider_type: formData.get("provider_type"),
        base_url: formData.get("base_url"),
        model: formData.get("model"),
        api_key: formData.get("api_key"),
        temperature: Number(formData.get("temperature")),
        llm_timeout_seconds: Number(formData.get("llm_timeout_seconds")),
        ingestion_think_level: elements.settingsForm.ingestion_think_level.value,
        chat_think_level: elements.settingsForm.chat_think_level.value,
        document_note_batch_size: Number(formData.get("document_note_batch_size")),
        mystery_resolution_batch_size: Number(formData.get("mystery_resolution_batch_size")),
        mystery_resolution_reference_mode: formData.get("mystery_resolution_reference_mode"),
      }),
    });
    populateSettingsForm(savedSettings);
    renderSettingsSummary(savedSettings);
    setStatus("Settings saved");
  } catch (error) {
    setStatus(error.message);
  }
});

elements.settingsForm.provider_type.addEventListener("change", syncThinkLevelControls);

try {
  await loadSettings();
  setStatus("Ready");
} catch (error) {
  setStatus(error.message);
}
