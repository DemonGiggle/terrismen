from __future__ import annotations

import inspect
import itertools
import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Iterator


DEBUG_ENV_VAR = "DEBUG"
DEBUG_LOG_ENV_VAR = "TERRISMEN_DEBUG_LOG"
DEBUG_LOGGER_NAME = "terrismen.debug"
DEFAULT_DEBUG_LOG_NAME = "terrismen-debug.log"
_DEBUG_FALSE_VALUES = {"0", "false", "no", "off"}
_context: ContextVar[dict[str, object]] = ContextVar("terrismen_debug_context", default={})
_request_counter = itertools.count(1)
_logger_lock = Lock()
_configured_log_path: Path | None = None


def debug_enabled_from_env() -> bool:
    raw = os.getenv(DEBUG_ENV_VAR, "")
    if not raw.strip():
        return False
    return raw.strip().lower() not in _DEBUG_FALSE_VALUES


def resolve_debug_log_path(*, data_root: Path | None = None, app_config_path: Path | None = None) -> Path:
    configured_path = os.getenv(DEBUG_LOG_ENV_VAR, "").strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    if app_config_path is not None:
        return (app_config_path.parent / DEFAULT_DEBUG_LOG_NAME).resolve()
    if data_root is not None:
        return (data_root / DEFAULT_DEBUG_LOG_NAME).resolve()
    return (Path.home() / ".config" / "terrismen" / DEFAULT_DEBUG_LOG_NAME).resolve()


def configure_debug_logging(log_path: Path | None = None) -> Path | None:
    global _configured_log_path

    if not debug_enabled_from_env():
        return None
    resolved_path = (log_path or resolve_debug_log_path()).expanduser().resolve()
    with _logger_lock:
        logger = logging.getLogger(DEBUG_LOGGER_NAME)
        if _configured_log_path == resolved_path and logger.handlers:
            return resolved_path
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(resolved_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _configured_log_path = resolved_path
    return resolved_path


def reset_debug_logging() -> None:
    global _configured_log_path

    with _logger_lock:
        logger = logging.getLogger(DEBUG_LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        _configured_log_path = None


@contextmanager
def llm_operation_context(**values: object) -> Iterator[None]:
    previous = dict(_context.get())
    merged = previous | {key: value for key, value in values.items() if value is not None}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def current_llm_operation_context() -> dict[str, object]:
    return dict(_context.get())


def next_llm_request_id() -> int:
    return next(_request_counter)


def find_llm_caller() -> dict[str, object]:
    frame = inspect.currentframe()
    if frame is None:
        return {}
    caller = frame.f_back
    while caller is not None:
        module_name = str(caller.f_globals.get("__name__", ""))
        if not module_name.startswith("logging") and not module_name.startswith("terrismen.debug") and not module_name.startswith(
            "terrismen.llm"
        ):
            return {
                "caller_path": str(Path(caller.f_code.co_filename).resolve()),
                "caller_line": caller.f_lineno,
                "caller_function": caller.f_code.co_name,
            }
        caller = caller.f_back
    return {}


def log_debug_event(event: str, **payload: object) -> None:
    if not debug_enabled_from_env():
        return
    configure_debug_logging()
    logger = logging.getLogger(DEBUG_LOGGER_NAME)
    if not logger.handlers:
        return
    record = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": event,
        **payload,
    }
    logger.info(json.dumps(record, sort_keys=True, default=str))
