export async function api(path, options = {}) {
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

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function getSettingsState(settings) {
  return isSettingsConfigured(settings)
    ? { label: "Configured", className: "tag tag-success" }
    : { label: "Needs setup", className: "tag tag-warning" };
}

export function summarizeSettings(settings) {
  const missing = [];
  if (!settings.provider_type) {
    missing.push("provider");
  }
  if (!settings.base_url) {
    missing.push("base URL");
  }
  if (!settings.model) {
    missing.push("model");
  }
  if (missing.length) {
    return `Missing ${missing.join(", ")} before upload and chat are ready.`;
  }

  return [
    formatProviderType(settings.provider_type),
    settings.model,
    settings.base_url,
    settings.api_key ? "API key saved" : "No API key saved",
    `${Math.round(settings.llm_timeout_seconds ?? 600)}s timeout`,
  ].join(" • ");
}

function isSettingsConfigured(settings) {
  return Boolean(settings.provider_type && settings.base_url && settings.model);
}

function formatProviderType(providerType) {
  if (providerType === "openai_compatible") {
    return "OpenAI-compatible";
  }
  if (providerType === "ollama") {
    return "Ollama";
  }
  return "Unknown provider";
}
