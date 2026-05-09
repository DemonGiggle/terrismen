import { api, getSettingsState, summarizeSettings } from "./shared.js";

const elements = {
  status: document.querySelector("#status-pill"),
  settingsForm: document.querySelector("#settings-form"),
  settingsSummary: document.querySelector("#settings-summary"),
  settingsIndicator: document.querySelector("#settings-indicator"),
};

function setStatus(text) {
  elements.status.textContent = text;
}

function renderSettingsSummary(settings) {
  const state = getSettingsState(settings);
  elements.settingsIndicator.textContent = state.label;
  elements.settingsIndicator.className = state.className;
  elements.settingsSummary.textContent = summarizeSettings(settings);
}

function populateSettingsForm(settings) {
  elements.settingsForm.provider_type.value = settings.provider_type || "openai_compatible";
  elements.settingsForm.base_url.value = settings.base_url || "";
  elements.settingsForm.model.value = settings.model || "";
  elements.settingsForm.api_key.value = settings.api_key || "";
  elements.settingsForm.temperature.value = settings.temperature ?? 0.2;
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
        provider_type: formData.get("provider_type"),
        base_url: formData.get("base_url"),
        model: formData.get("model"),
        api_key: formData.get("api_key"),
        temperature: Number(formData.get("temperature")),
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
