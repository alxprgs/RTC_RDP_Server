from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
import time
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import Request, Response

from server.core.config import Settings
from server.core.context import REQUEST_ID

try:
    from InquirerPy import inquirer
except Exception:
    inquirer = None


log = logging.getLogger("motor-bridge")
serial_log = logging.getLogger("motor-bridge.serial")

LOG_PROFILES: dict[str, dict[str, Any]] = {
    "DEFAULT": {
        "title": "Обычные логи (INFO), без body, без serial",
        "log_level": "INFO",
        "log_request_body": False,
        "serial_log": False,
        "max_body_preview": 800,
        "serial_max_preview": 200,
    },
    "HTTP_DEBUG": {
        "title": "HTTP отладка (INFO + тело запросов), serial выкл",
        "log_level": "INFO",
        "log_request_body": True,
        "serial_log": False,
        "max_body_preview": 1200,
        "serial_max_preview": 200,
    },
    "SERIAL_DEBUG": {
        "title": "Serial отладка (INFO + serial), тело выкл",
        "log_level": "INFO",
        "log_request_body": False,
        "serial_log": True,
        "max_body_preview": 800,
        "serial_max_preview": 400,
    },
    "FULL_DEBUG": {
        "title": "Полная отладка (DEBUG + тело + serial)",
        "log_level": "DEBUG",
        "log_request_body": True,
        "serial_log": True,
        "max_body_preview": 2000,
        "serial_max_preview": 600,
    },
    "QUIET": {
        "title": "Тихий режим (WARNING), без body, без serial",
        "log_level": "WARNING",
        "log_request_body": False,
        "serial_log": False,
        "max_body_preview": 800,
        "serial_max_preview": 200,
    },
}


@dataclass
class LoggingRuntime:
    log_level: str
    log_request_body: bool
    serial_log: bool
    max_body_preview: int
    serial_max_preview: int


def setup_base_logging(settings: Settings) -> None:
    level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _normalize_profile(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = v.strip().upper().replace("-", "_")
    return v or None


def _apply_logging_runtime(runtime: LoggingRuntime) -> None:
    lvl = getattr(logging, runtime.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(lvl)
    for h in root.handlers:
        try:
            h.setLevel(lvl)
        except Exception:
            pass

    log.setLevel(lvl)
    if runtime.serial_log:
        serial_log.setLevel(lvl)
    else:
        serial_log.setLevel(max(lvl, logging.INFO))

    log.info(
        "Logging profile applied: level=%s, request_body=%s, serial_log=%s, max_body=%d, max_serial=%d",
        runtime.log_level,
        runtime.log_request_body,
        runtime.serial_log,
        runtime.max_body_preview,
        runtime.serial_max_preview,
    )


async def _pick_log_profile_interactive() -> LoggingRuntime:
    if inquirer is None:
        raise RuntimeError("InquirerPy не установлен. Установи: pip install InquirerPy")

    choices = [{"name": f"{k} — {v['title']}", "value": k} for k, v in LOG_PROFILES.items()] + [
        {"name": "CUSTOM — Настроить вручную", "value": "CUSTOM"}
    ]

    picked = await inquirer.select(
        message="Настройки логов (профиль):",
        choices=choices,
        default="DEFAULT",
    ).execute_async()

    if picked != "CUSTOM":
        p = LOG_PROFILES[picked]
        return LoggingRuntime(
            log_level=p["log_level"],
            log_request_body=bool(p["log_request_body"]),
            serial_log=bool(p["serial_log"]),
            max_body_preview=int(p["max_body_preview"]),
            serial_max_preview=int(p["serial_max_preview"]),
        )

    lvl = await inquirer.select(
        message="Уровень логов:",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    ).execute_async()

    flags = await inquirer.checkbox(
        message="Что включить?",
        choices=[
            {"name": "Логировать тело HTTP запросов (POST/PUT/PATCH)", "value": "body"},
            {"name": "Подробные Serial логи (TX/RX/тайминги)", "value": "serial"},
        ],
        default=[],
    ).execute_async()

    body_on = "body" in flags
    serial_on = "serial" in flags

    max_body = 800
    max_serial = 200

    if body_on:
        raw = await inquirer.text(
            message="MAX_BODY_PREVIEW (сколько символов тела логировать):",
            default="800",
        ).execute_async()
        try:
            max_body = int(raw)
        except Exception:
            max_body = 800

    if serial_on:
        raw = await inquirer.text(
            message="SERIAL_MAX_PREVIEW (сколько символов команды показывать):",
            default="200",
        ).execute_async()
        try:
            max_serial = int(raw)
        except Exception:
            max_serial = 200

    return LoggingRuntime(
        log_level=str(lvl).upper(),
        log_request_body=bool(body_on),
        serial_log=bool(serial_on),
        max_body_preview=int(max(50, min(max_body, 50_000))),
        serial_max_preview=int(max(20, min(max_serial, 50_000))),
    )


async def ensure_logging_config_on_boot(settings: Settings) -> LoggingRuntime:
    """
    - если LOG_PROFILE задан → применяем профиль
    - если LOG_PROFILE не задан и есть TTY → показываем меню
    - если не TTY → берём env/manual defaults
    """
    profile_key = _normalize_profile(settings.log_profile or os.getenv("LOG_PROFILE"))
    if profile_key:
        preset = LOG_PROFILES.get(profile_key)
        if not preset:
            raise RuntimeError(
                f"Unknown LOG_PROFILE={profile_key}. Available: {', '.join(LOG_PROFILES.keys())}"
            )
        runtime = LoggingRuntime(
            log_level=preset["log_level"],
            log_request_body=bool(preset["log_request_body"]),
            serial_log=bool(preset["serial_log"]),
            max_body_preview=int(preset["max_body_preview"]),
            serial_max_preview=int(preset["serial_max_preview"]),
        )
        _apply_logging_runtime(runtime)
        return runtime

    if sys.stdin and sys.stdin.isatty():
        runtime = await _pick_log_profile_interactive()
        _apply_logging_runtime(runtime)
        log.info(
            "Чтобы не спрашивать при старте, задай в .env: LOG_PROFILE=DEFAULT (варианты: %s)",
            ", ".join(LOG_PROFILES.keys()),
        )
        return runtime

    # non-interactive fallback
    runtime = LoggingRuntime(
        log_level=(settings.log_level or "INFO").upper(),
        log_request_body=bool(settings.log_request_body),
        serial_log=bool(settings.serial_log),
        max_body_preview=int(max(50, min(int(settings.max_body_preview), 50_000))),
        serial_max_preview=int(max(20, min(int(settings.serial_max_preview), 50_000))),
    )
    _apply_logging_runtime(runtime)
    return runtime


async def request_logging_middleware(request: Request, call_next):
    # request-id
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = REQUEST_ID.set(rid)

    settings: Settings = request.app.state.settings
    runtime: LoggingRuntime = getattr(request.app.state, "logging_runtime", LoggingRuntime(
        log_level="INFO", log_request_body=False, serial_log=False, max_body_preview=800, serial_max_preview=200
    ))

    client_host = "-"
    client_port = "-"
    if request.client:
        client_host = request.client.host
        client_port = str(request.client.port)

    method = request.method
    path = request.url.path
    query = request.url.query
    ua = request.headers.get("user-agent", "-")
    ct = request.headers.get("content-type", "-")
    cl = request.headers.get("content-length", "-")

    body_preview = None
    if runtime.log_request_body and method in ("POST", "PUT", "PATCH"):
        try:
            body_bytes = await request.body()

            async def receive():
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            request._receive = receive  # type: ignore[attr-defined]

            if body_bytes:
                text = body_bytes.decode("utf-8", errors="replace").strip()
                if len(text) > runtime.max_body_preview:
                    text = text[: runtime.max_body_preview] + "…(truncated)"
                body_preview = text
        except Exception:
            body_preview = "<failed to read body>"

    start = time.perf_counter()
    target = f"{path}?{query}" if query else path

    if body_preview is not None:
        log.info(
            "→ %s %s | from=%s:%s | rid=%s | ua=%s | ct=%s | cl=%s | body=%s",
            method, target, client_host, client_port, rid, ua, ct, cl, body_preview
        )
    else:
        log.info(
            "→ %s %s | from=%s:%s | rid=%s | ua=%s | ct=%s | cl=%s",
            method, target, client_host, client_port, rid, ua, ct, cl
        )

    try:
        response: Response = await call_next(request)
    except Exception:
        dur_ms = (time.perf_counter() - start) * 1000.0
        log.exception(
            "✖ %s %s | from=%s:%s | rid=%s | took=%.1fms",
            method, target, client_host, client_port, rid, dur_ms
        )
        REQUEST_ID.reset(token)
        raise

    dur_ms = (time.perf_counter() - start) * 1000.0
    response.headers["X-Request-Id"] = rid

    log.info(
        "← %s %s | %s | rid=%s | took=%.1fms",
        method, target, response.status_code, rid, dur_ms
    )

    REQUEST_ID.reset(token)
    return response
