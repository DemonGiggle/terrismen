import { api, getSettingsState, summarizeSettings } from "./shared.js?v=asset-ui-fixes-20260510";

const elements = {
  status: document.querySelector("#status-pill"),
  settingsForm: document.querySelector("#settings-form"),
  settingsSummary: document.querySelector("#settings-summary"),
  settingsIndicator: document.querySelector("#settings-indicator"),
  dataRootSummary: document.querySelector("#data-root-summary"),
  dataRootHint: document.querySelector("#data-root-hint"),
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
  elements.settingsForm.mystery_resolution_batch_size.value = settings.mystery_resolution_batch_size ?? 5;
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
        mystery_resolution_batch_size: Number(formData.get("mystery_resolution_batch_size")),
      }),
    });
    populateSettingsForm(savedSettings);
    renderSettingsSummary(savedSettings);
    setStatus("Settings saved");
  } catch (error) {
    setStatus(error.message);
  }
});

try {
  await loadSettings();
  setStatus("Ready");
} catch (error) {
  setStatus(error.message);
}
