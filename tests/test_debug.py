from __future__ import annotations

import json
from pathlib import Path

import httpx

from terrismen.config import load_config
from terrismen.debug import configure_debug_logging, llm_operation_context, reset_debug_logging
from terrismen.llm.base import ProviderError, ProviderSettings
from terrismen.llm.ollama import OllamaProvider


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload


def build_settings() -> ProviderSettings:
    return ProviderSettings(
        provider_type="ollama",
        base_url="http://localhost:11434",
        model="llama3.2",
        api_key="",
        temperature=0.2,
        llm_timeout_seconds=12.0,
    )


def read_debug_events(log_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_load_config_sets_debug_log_path_from_app_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("TERRISMEN_APP_CONFIG", str(tmp_path / "settings" / "config.json"))

    config = load_config()

    assert config.debug_enabled is True
    assert config.debug_log_path == (tmp_path / "settings" / "terrismen-debug.log").resolve()


def test_provider_debug_log_records_caller_context_and_duration(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "terrismen-debug.log"
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("TERRISMEN_DEBUG_LOG", str(log_path))
    reset_debug_logging()
    configure_debug_logging(log_path)
    provider = OllamaProvider(build_settings())
    monkeypatch.setattr(
        provider._client,
        "post",
        lambda endpoint, headers=None, json=None: FakeResponse(200, {"message": {"content": "ok"}}),
    )

    with llm_operation_context(workflow="document_ingestion", document_id=17, step="generating notes", batch_size=3):
        provider.complete("system prompt", "user prompt")

    events = read_debug_events(log_path)

    assert [event["event"] for event in events] == ["llm_request_start", "llm_request_end"]
    assert events[0]["workflow"] == "document_ingestion"
    assert events[0]["document_id"] == 17
    assert events[0]["step"] == "generating notes"
    assert events[0]["batch_size"] == 3
    assert events[0]["caller_path"].endswith("tests/test_debug.py")
    assert isinstance(events[0]["caller_line"], int)
    assert events[1]["status_code"] == 200
    assert events[1]["duration_ms"] >= 0


def test_provider_debug_log_records_timeout_before_failure(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "terrismen-debug.log"
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("TERRISMEN_DEBUG_LOG", str(log_path))
    reset_debug_logging()
    configure_debug_logging(log_path)
    provider = OllamaProvider(build_settings())
    monkeypatch.setattr(
        provider._client,
        "post",
        lambda endpoint, headers=None, json=None: (_ for _ in ()).throw(httpx.ReadTimeout("")),
    )

    try:
        with llm_operation_context(
            workflow="document_ingestion",
            document_id=21,
            step="resolving mysteries",
            mystery_ids=[8, 9],
            batch_size=2,
        ):
            provider.complete("system prompt", "user prompt")
    except ProviderError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("provider.complete should raise ProviderError on timeout")

    events = read_debug_events(log_path)

    assert [event["event"] for event in events] == ["llm_request_start", "llm_request_timeout"]
    assert events[1]["document_id"] == 21
    assert events[1]["step"] == "resolving mysteries"
    assert events[1]["mystery_ids"] == [8, 9]
    assert events[1]["error_type"] == "ReadTimeout"
    assert "timed out" in events[1]["error_message"]
