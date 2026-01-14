import asyncio
import time
import os
import sys
import logging
import uuid
import contextvars
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional, Literal, List, Sequence

import serial
from serial.tools import list_ports
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

try:
    from InquirerPy import inquirer
except Exception:
    inquirer = None

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("motor-bridge")
serial_log = logging.getLogger("motor-bridge.serial")

LOG_REQUEST_BODY = os.getenv("LOG_REQUEST_BODY", "0") == "1"
MAX_BODY_PREVIEW = int(os.getenv("MAX_BODY_PREVIEW", "800"))

SERIAL_LOG = os.getenv("SERIAL_LOG", "0") == "1"
SERIAL_MAX_PREVIEW = int(os.getenv("SERIAL_MAX_PREVIEW", "200"))

WS_PING_INTERVAL = float(os.getenv("WS_PING_INTERVAL", "5"))
WS_PING_TIMEOUT = float(os.getenv("WS_PING_TIMEOUT", "15"))
WS_MAX_RATE_HZ = float(os.getenv("WS_MAX_RATE_HZ", "30"))
WS_STOP_ON_CLOSE = os.getenv("WS_STOP_ON_CLOSE", "1") == "1"

ARDUINO_BAUD = int(os.getenv("ARDUINO_BAUD", "115200"))

REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

LOG_PROFILE_ENV = os.getenv("LOG_PROFILE")
LOG_LEVEL_ENV = os.getenv("LOG_LEVEL")
LOG_REQUEST_BODY_ENV = os.getenv("LOG_REQUEST_BODY")
SERIAL_LOG_ENV = os.getenv("SERIAL_LOG")
MAX_BODY_PREVIEW_ENV = os.getenv("MAX_BODY_PREVIEW")
SERIAL_MAX_PREVIEW_ENV = os.getenv("SERIAL_MAX_PREVIEW")


def _normalize_log_profile(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = v.strip().upper().replace("-", "_")
    return v or None


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


def _apply_logging_runtime(
    *,
    log_level: str,
    log_request_body: bool,
    serial_log_enabled: bool,
    max_body_preview: int,
    serial_max_preview: int,
) -> None:
    """
    Применяет настройки логов после basicConfig().
    Важно: serial-логи не должны “вылезать” даже если общий уровень DEBUG, когда SERIAL_LOG выключен.
    """
    global LOG_LEVEL, LOG_REQUEST_BODY, SERIAL_LOG, MAX_BODY_PREVIEW, SERIAL_MAX_PREVIEW

    log_level = (log_level or "INFO").upper()
    lvl = getattr(logging, log_level, logging.INFO)

    LOG_LEVEL = log_level
    LOG_REQUEST_BODY = bool(log_request_body)
    SERIAL_LOG = bool(serial_log_enabled)

    MAX_BODY_PREVIEW = int(max(50, min(int(max_body_preview), 50_000)))
    SERIAL_MAX_PREVIEW = int(max(20, min(int(serial_max_preview), 50_000)))

    root = logging.getLogger()
    root.setLevel(lvl)
    for h in root.handlers:
        try:
            h.setLevel(lvl)
        except Exception:
            pass

    log.setLevel(lvl)
    if SERIAL_LOG:
        serial_log.setLevel(lvl)
    else:
        serial_log.setLevel(max(lvl, logging.INFO))

    log.info(
        "Logging profile applied: level=%s, request_body=%s, serial_log=%s, max_body=%d, max_serial=%d",
        LOG_LEVEL,
        LOG_REQUEST_BODY,
        SERIAL_LOG,
        MAX_BODY_PREVIEW,
        SERIAL_MAX_PREVIEW,
    )


async def _pick_log_profile_interactive() -> dict[str, Any]:
    if inquirer is None:
        raise RuntimeError("InquirerPy не установлен. Установи: pip install InquirerPy")

    choices = [
        {"name": f"{k} — {v['title']}", "value": k}
        for k, v in LOG_PROFILES.items()
    ] + [{"name": "CUSTOM — Настроить вручную", "value": "CUSTOM"}]

    picked = await inquirer.select(
        message="Настройки логов (профиль):",
        choices=choices,
        default="DEFAULT",
    ).execute_async()

    if picked != "CUSTOM":
        return LOG_PROFILES[picked]

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

    return {
        "title": "CUSTOM",
        "log_level": lvl,
        "log_request_body": body_on,
        "serial_log": serial_on,
        "max_body_preview": max_body,
        "serial_max_preview": max_serial,
    }


async def ensure_logging_config_on_boot() -> None:
    """
    Логика как с ServoPwr:
    - если LOG_PROFILE задан → применяем его
    - если LOG_PROFILE не задан и есть TTY → показываем меню
    - если не TTY → применяем env/default (и “зажимаем” serial logger, чтобы не шумел)
    """
    profile_key = _normalize_log_profile(LOG_PROFILE_ENV)
    if profile_key:
        preset = LOG_PROFILES.get(profile_key)
        if not preset:
            raise RuntimeError(
                f"Unknown LOG_PROFILE={profile_key}. Available: {', '.join(LOG_PROFILES.keys())}"
            )
        _apply_logging_runtime(
            log_level=preset["log_level"],
            log_request_body=preset["log_request_body"],
            serial_log_enabled=preset["serial_log"],
            max_body_preview=preset["max_body_preview"],
            serial_max_preview=preset["serial_max_preview"],
        )
        return

    if sys.stdin and sys.stdin.isatty():
        preset = await _pick_log_profile_interactive()
        _apply_logging_runtime(
            log_level=preset["log_level"],
            log_request_body=preset["log_request_body"],
            serial_log_enabled=preset["serial_log"],
            max_body_preview=preset["max_body_preview"],
            serial_max_preview=preset["serial_max_preview"],
        )
        log.info(
            "Чтобы не спрашивать при старте, задай в .env: LOG_PROFILE=DEFAULT (варианты: %s)",
            ", ".join(LOG_PROFILES.keys()),
        )
        return

    _apply_logging_runtime(
        log_level=(LOG_LEVEL_ENV or LOG_LEVEL or "INFO"),
        log_request_body=(LOG_REQUEST_BODY_ENV == "1")
        if LOG_REQUEST_BODY_ENV is not None
        else LOG_REQUEST_BODY,
        serial_log_enabled=(SERIAL_LOG_ENV == "1") if SERIAL_LOG_ENV is not None else SERIAL_LOG,
        max_body_preview=int(MAX_BODY_PREVIEW_ENV) if MAX_BODY_PREVIEW_ENV else MAX_BODY_PREVIEW,
        serial_max_preview=int(SERIAL_MAX_PREVIEW_ENV)
        if SERIAL_MAX_PREVIEW_ENV
        else SERIAL_MAX_PREVIEW,
    )


IGNORE_LINE_PREFIXES_UPPER: tuple[str, ...] = (
    "OK READY",
    "OK PINS",
    "OK CMDS",
    "OK SERVO_PWR?",
    "OK START",
)


def _slog(level: str, msg: str, *args):
    if not SERIAL_LOG:
        serial_log.debug(msg, *args)
        return

    if level == "info":
        serial_log.info(msg, *args)
    elif level == "warning":
        serial_log.warning(msg, *args)
    elif level == "error":
        serial_log.error(msg, *args)
    else:
        serial_log.info(msg, *args)


def _sanitize_outgoing_line(s: str) -> str:
    """
    Оставляем только печатный ASCII + пробелы.
    Это убивает любые странные байты/символы, которые могут появиться в начале строки.
    """
    s = (s or "").strip()
    s = s.lstrip("\ufeff\uFFFD")
    s = "".join(ch for ch in s if ch == " " or 33 <= ord(ch) <= 126)
    s = " ".join(s.split())
    return s


def _normalize_servo_pwr_mode(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = v.strip().upper()
    if v in ("ARDUINO", "EXTERNAL"):
        return v
    return None


async def _pick_servo_pwr_mode_interactive() -> str:
    if inquirer is None:
        raise RuntimeError("InquirerPy не установлен. Установи: pip install InquirerPy")

    mode = await inquirer.select(
        message="Тип питания сервоприводов:",
        choices=[
            {
                "name": "Питание от Arduino (USB/5V с платы) — безопасные лимиты, плавное движение",
                "value": "ARDUINO",
            },
            {
                "name": "Внешнее питание (отдельный 5V БП/аккум) — полный диапазон 0..180",
                "value": "EXTERNAL",
            },
        ],
        default="ARDUINO",
    ).execute_async()

    return mode


def _looks_like_arduino(p) -> bool:
    text = " ".join(
        [
            str(getattr(p, "description", "") or ""),
            str(getattr(p, "manufacturer", "") or ""),
            str(getattr(p, "product", "") or ""),
            str(getattr(p, "hwid", "") or ""),
        ]
    ).lower()

    keywords = [
        "arduino",
        "ch340",
        "wch",
        "cp210",
        "silicon labs",
        "ftdi",
        "usb serial",
        "usb-serial",
        "acm",
        "serial",
    ]
    return any(k in text for k in keywords)


def find_arduino_port(prefer_vid_pid: Optional[List[tuple[int, int]]] = None) -> str:
    env_port = os.getenv("ARDUINO_PORT")
    if env_port:
        return env_port

    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("Serial порты не найдены. Проверь подключение Arduino/драйверы/права доступа.")

    for p in ports:
        log.info(
            "Serial port found: device=%s desc=%s manuf=%s hwid=%s vid=%s pid=%s",
            p.device,
            p.description,
            p.manufacturer,
            p.hwid,
            p.vid,
            p.pid,
        )

    if prefer_vid_pid:
        for p in ports:
            if p.vid is None or p.pid is None:
                continue
            for (vid, pid) in prefer_vid_pid:
                if p.vid == vid and p.pid == pid:
                    return p.device

    scored = []
    for p in ports:
        score = 0
        if _looks_like_arduino(p):
            score += 10
        if sys.platform.startswith("linux"):
            if (p.device or "").startswith("/dev/ttyACM"):
                score += 5
            if (p.device or "").startswith("/dev/ttyUSB"):
                score += 3
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1].device


def clamp(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def deadzone(v: int, dz: int) -> int:
    return 0 if abs(v) < dz else v


def mix_tank(x: int, y: int) -> tuple[int, int]:
    a = y + x
    b = y - x
    return clamp(a, -255, 255), clamp(b, -255, 255)


class SerialProtocolError(RuntimeError):
    def __init__(self, sent: str, reply: str):
        super().__init__(f"Arduino replied with error. sent={sent!r} reply={reply!r}")
        self.sent = sent
        self.reply = reply


def _infer_expect_prefixes_upper(cmd_line: str) -> list[str]:
    clean = (cmd_line or "").strip()
    if not clean:
        return ["OK"]
    name = clean.split()[0].strip().upper()

    if name == "PING":
        return ["OK PONG"]
    if name == "SERVOPWR":
        return ["OK SERVO_PWR"]

    if name in ("SETAENGINE", "SETBENGINE", "SETALLENGINE"):
        return [f"OK {name}"]

    if name in ("SETSERVOA", "SETSERVOB", "SETSERVOALL"):
        return [f"OK {name}"]

    return ["OK"]


class SerialManager:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None
        self._lock = asyncio.Lock()
        self._rx_buf = bytearray()

    def connect(self) -> None:
        rid = REQUEST_ID.get()
        if self._ser and self._ser.is_open:
            return

        _slog("info", "CONNECT port=%s baud=%s timeout=%.2fs | rid=%s", self.port, self.baudrate, self.timeout, rid)
        self._ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=0.05,
            write_timeout=self.timeout,
        )

        time.sleep(2.2)

        try:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
        except Exception:
            pass

        self._rx_buf.clear()
        _slog("info", "CONNECTED port=%s | rid=%s", self.port, rid)

    def close(self) -> None:
        rid = REQUEST_ID.get()
        if self._ser:
            try:
                was_open = self._ser.is_open
                self._ser.close()
                _slog("info", "CLOSE port=%s was_open=%s | rid=%s", self.port, was_open, rid)
            except Exception as e:
                _slog("warning", "CLOSE FAILED port=%s | rid=%s | err=%s", self.port, rid, repr(e))
        self._ser = None
        self._rx_buf.clear()

    def _readline_buffered_sync(self, deadline: float, max_line: int = 256) -> str:
        """
        Читает из serial байты и собирает строку ДО \n.
        В отличие от readline() НЕ возвращает “обрывки”.
        """
        if not self._ser:
            raise RuntimeError("Serial not connected")

        while time.monotonic() < deadline:
            nl = self._rx_buf.find(b"\n")
            if nl != -1:
                raw = self._rx_buf[:nl]
                del self._rx_buf[: nl + 1]
                raw = raw.replace(b"\r", b"").strip()
                if not raw:
                    continue
                return raw.decode("utf-8", errors="replace")

            chunk = self._ser.read(64)
            if chunk:
                self._rx_buf.extend(chunk)
                if len(self._rx_buf) > 4096:
                    self._rx_buf = self._rx_buf[-1024:]

            if len(self._rx_buf) > max_line:
                self._rx_buf.clear()
                return "ERR LineTooLong"

        return ""

    def _drain_lines_sync(self, seconds: float = 1.0, max_lines: int = 200) -> list[str]:
        self.connect()
        rid = REQUEST_ID.get()
        end = time.monotonic() + max(0.0, seconds)
        lines: list[str] = []
        while time.monotonic() < end and len(lines) < max_lines:
            s = self._readline_buffered_sync(deadline=time.monotonic() + 0.10)
            if s:
                lines.append(s)

        if lines:
            preview = lines[:10]
            more = len(lines) - len(preview)
            _slog(
                "info",
                "DRAIN got=%d lines (showing %d)%s | rid=%s",
                len(lines),
                len(preview),
                f" +{more} more" if more > 0 else "",
                rid,
            )
            for i, ln in enumerate(preview, 1):
                _slog("info", "  drain[%d]: %r | rid=%s", i, ln, rid)
        return lines

    def _wait_relevant_reply_sync(
        self,
        sent_line: str,
        expect_prefixes_upper: Sequence[str],
        max_wait_s: float,
        max_lines: int,
    ) -> str:
        rid = REQUEST_ID.get()
        end = time.monotonic() + max_wait_s
        seen: list[str] = []

        while time.monotonic() < end and len(seen) < max_lines:
            s = self._readline_buffered_sync(deadline=time.monotonic() + 0.40)
            if not s:
                continue

            s_up = s.upper()

            if any(s_up.startswith(p) for p in IGNORE_LINE_PREFIXES_UPPER):
                _slog("info", "← RX(ignore) %r | rid=%s", s, rid)
                continue

            if s_up.startswith("ERR"):
                _slog("warning", "← RX(err) %r | rid=%s", s, rid)
                raise SerialProtocolError(sent=sent_line, reply=s)

            seen.append(s)

            if any(s_up.startswith(exp) for exp in expect_prefixes_upper):
                _slog("info", "← RX(match) %r | rid=%s", s, rid)
                return s

            _slog("warning", "← RX(unexpected) %r (expect=%s) | rid=%s", s, list(expect_prefixes_upper), rid)

        raise TimeoutError(
            f"Timeout waiting reply. sent={sent_line!r} expect={list(expect_prefixes_upper)!r} "
            f"seen={seen[:10]}{' ...' if len(seen) > 10 else ''}"
        )

    def _send_cmd_sync(
        self,
        line: str,
        expect_prefixes_upper: Optional[Sequence[str]] = None,
        max_wait_s: float = 2.5,
        pre_drain_s: float = 0.0,
        max_lines: int = 80,
    ) -> str:
        self.connect()
        if not self._ser:
            raise RuntimeError("Serial not connected")

        clean = _sanitize_outgoing_line(line)
        if not clean:
            raise ValueError("Empty command")

        payload = (clean + "\n").encode("ascii", errors="strict")

        if expect_prefixes_upper is None:
            expect_prefixes_upper = _infer_expect_prefixes_upper(clean)

        rid = REQUEST_ID.get()
        preview = clean if len(clean) <= SERIAL_MAX_PREVIEW else clean[:SERIAL_MAX_PREVIEW] + "…(truncated)"

        if pre_drain_s > 0:
            try:
                self._drain_lines_sync(seconds=pre_drain_s)
            except Exception:
                pass

        t0 = time.perf_counter()
        _slog("info", "→ TX %r (%d bytes) expect=%s | rid=%s", preview, len(payload), list(expect_prefixes_upper), rid)

        self._ser.write(payload)
        self._ser.flush()

        reply = self._wait_relevant_reply_sync(
            sent_line=clean,
            expect_prefixes_upper=expect_prefixes_upper,
            max_wait_s=max_wait_s,
            max_lines=max_lines,
        )

        dt = (time.perf_counter() - t0) * 1000.0
        _slog("info", "✓ CMD OK sent=%r reply=%r | rid=%s | took=%.1fms", preview, reply, rid, dt)
        return reply

    async def send_cmd(
        self,
        line: str,
        expect_prefixes_upper: Optional[Sequence[str]] = None,
        max_wait_s: float = 2.5,
        pre_drain_s: float = 0.0,
        close_on_error: bool = True,
    ) -> str:
        async with self._lock:
            try:
                return await asyncio.to_thread(
                    self._send_cmd_sync,
                    line,
                    expect_prefixes_upper,
                    max_wait_s,
                    pre_drain_s,
                )
            except Exception:
                if close_on_error:
                    self.close()
                raise

    async def send_cmds(self, lines: list[str], max_wait_s_each: float = 2.5) -> list[str]:
        async with self._lock:
            replies: list[str] = []
            try:
                for line in lines:
                    exp = _infer_expect_prefixes_upper(line)
                    replies.append(
                        await asyncio.to_thread(self._send_cmd_sync, line, exp, max_wait_s_each, 0.0)
                    )
                return replies
            except Exception:
                self.close()
                raise

async def ensure_servo_power_mode_on_boot(serial_mgr: SerialManager) -> str:
    mode = _normalize_servo_pwr_mode(os.getenv("SERVO_PWR_MODE"))

    if mode is None:
        if sys.stdin and sys.stdin.isatty():
            mode = await _pick_servo_pwr_mode_interactive()
        else:
            raise RuntimeError(
                "SERVO_PWR_MODE не задан, а интерактивного терминала нет.\n"
                "Задай в .env: SERVO_PWR_MODE=ARDUINO или SERVO_PWR_MODE=EXTERNAL"
            )

    serial_mgr.connect()

    try:
        await asyncio.to_thread(serial_mgr._drain_lines_sync, 2.0)
    except Exception:
        pass

    last_err: Exception | None = None
    for _ in range(6):
        try:
            await serial_mgr.send_cmd(
                "PING",
                expect_prefixes_upper=["OK PONG"],
                max_wait_s=2.5,
                pre_drain_s=0.0,
                close_on_error=False,
            )
            last_err = None
            break
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.25)

    if last_err is not None:
        raise RuntimeError(f"Arduino не отвечает стабильно на PING: {last_err}") from last_err

    cmd = f"ServoPwr {mode}"
    last_err = None
    for _ in range(6):
        try:
            reply = await serial_mgr.send_cmd(
                cmd,
                expect_prefixes_upper=["OK SERVO_PWR"],
                max_wait_s=3.0,
                pre_drain_s=0.0,
                close_on_error=False,
            )
            log.info("Servo power mode set: %s (reply=%s)", mode, reply)
            return mode
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.25)

    raise RuntimeError(f"Не удалось установить ServoPwr: {last_err}") from last_err

SERIAL_PORT: Optional[str] = None
serial_mgr: Optional[SerialManager] = None

SERVO_PWR_MODE_ACTIVE: Optional[str] = None


def _need_serial_mgr() -> SerialManager:
    if serial_mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")
    return serial_mgr

@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_logging_config_on_boot()

    global SERIAL_PORT, serial_mgr, SERVO_PWR_MODE_ACTIVE

    try:
        SERIAL_PORT = find_arduino_port()
    except Exception as e:
        raise RuntimeError(
            f"Не удалось авто-найти порт Arduino: {e}. "
            f"Задай ARDUINO_PORT (например COM11 или /dev/ttyACM0)."
        )

    serial_mgr = SerialManager(SERIAL_PORT, baudrate=ARDUINO_BAUD, timeout=1.0)

    log.info("Starting Arduino Motor Bridge (serial_port=%s)", SERIAL_PORT)
    try:
        SERVO_PWR_MODE_ACTIVE = await ensure_servo_power_mode_on_boot(serial_mgr)
        yield
    finally:
        try:
            serial_mgr.close()
        except Exception:
            pass
        log.info("Stopped Arduino Motor Bridge (serial closed)")


app = FastAPI(title="Arduino Motor Bridge", lifespan=lifespan)


@app.middleware("http")
async def incoming_debug(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = REQUEST_ID.set(rid)

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
    if LOG_REQUEST_BODY and method in ("POST", "PUT", "PATCH"):
        try:
            body_bytes = await request.body()

            async def receive():
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            request._receive = receive  # type: ignore[attr-defined]

            if body_bytes:
                text = body_bytes.decode("utf-8", errors="replace").strip()
                if len(text) > MAX_BODY_PREVIEW:
                    text = text[:MAX_BODY_PREVIEW] + "…(truncated)"
                body_preview = text
        except Exception:
            body_preview = "<failed to read body>"

    start = time.perf_counter()
    target = f"{path}?{query}" if query else path

    if body_preview is not None:
        log.info(
            "→ %s %s | from=%s:%s | rid=%s | ua=%s | ct=%s | cl=%s | body=%s",
            method,
            target,
            client_host,
            client_port,
            rid,
            ua,
            ct,
            cl,
            body_preview,
        )
    else:
        log.info(
            "→ %s %s | from=%s:%s | rid=%s | ua=%s | ct=%s | cl=%s",
            method,
            target,
            client_host,
            client_port,
            rid,
            ua,
            ct,
            cl,
        )

    try:
        response: Response = await call_next(request)
    except Exception:
        dur_ms = (time.perf_counter() - start) * 1000.0
        log.exception(
            "✖ %s %s | from=%s:%s | rid=%s | took=%.1fms",
            method,
            target,
            client_host,
            client_port,
            rid,
            dur_ms,
        )
        REQUEST_ID.reset(token)
        raise

    dur_ms = (time.perf_counter() - start) * 1000.0
    response.headers["X-Request-Id"] = rid

    log.info(
        "← %s %s | %s | rid=%s | took=%.1fms",
        method,
        target,
        response.status_code,
        rid,
        dur_ms,
    )

    REQUEST_ID.reset(token)
    return response

CmdName = Literal["SetAEngine", "SetBEngine", "SetAllEngine"]


class MotorCommandIn(BaseModel):
    cmd: CmdName
    speed: int = Field(ge=-255, le=255)


class MotorCommandOut(BaseModel):
    sent: str
    reply: str


ServoCmdName = Literal["SetServoA", "SetServoB", "SetServoAll"]


class ServoCommandIn(BaseModel):
    cmd: ServoCmdName
    deg: int = Field(ge=0, le=180, description="Позиция сервопривода (0..180)")


class ServoCommandOut(BaseModel):
    sent: str
    reply: str


ServoPowerMode = Literal["ARDUINO", "EXTERNAL"]


class ServoPowerIn(BaseModel):
    mode: ServoPowerMode


class ServoPowerOut(BaseModel):
    mode: ServoPowerMode
    sent: str
    reply: str


class JoystickIn(BaseModel):
    x: int = Field(ge=-255, le=255, description="Turn: left(-) .. right(+)")
    y: int = Field(ge=-255, le=255, description="Throttle: back(-) .. forward(+)")
    deadzone: int = Field(default=20, ge=0, le=80, description="Deadzone around center")
    scale: float = Field(default=1.0, ge=0.0, le=1.0)


class JoystickOut(BaseModel):
    input: dict
    motor_a: int
    motor_b: int
    sent: list[str]
    replies: list[str]


class ActionIn(BaseModel):
    action: str
    power: int = Field(default=160, ge=0, le=255, description="Сила действия (0..255)")
    duration_ms: int = Field(default=0, ge=0, le=10_000, description="Сколько держать, 0 = без таймера")


class ActionOut(BaseModel):
    action: str
    sent: list[str]
    replies: list[str]

def _a_b(a: int, b: int) -> list[str]:
    return [f"SetAEngine {a}", f"SetBEngine {b}"]


ACTIONS: dict[str, dict[str, Any]] = {
    "stop": {"title": "Стоп", "build": lambda p: _a_b(0, 0)},
    "forward": {"title": "Вперёд", "build": lambda p: _a_b(p, p)},
    "backward": {"title": "Назад", "build": lambda p: _a_b(-p, -p)},
    "turn_left": {"title": "Поворот влево", "build": lambda p: _a_b(int(p * 0.4), p)},
    "turn_right": {"title": "Поворот вправо", "build": lambda p: _a_b(p, int(p * 0.4))},
    "spin_left": {"title": "Разворот влево", "build": lambda p: _a_b(-p, p)},
    "spin_right": {"title": "Разворот вправо", "build": lambda p: _a_b(p, -p)},
    "slow_mode": {"title": "Медленный режим", "build": lambda p: _a_b(int(p * 0.3), int(p * 0.3))},
}


async def run_action(action: str, power: int) -> tuple[list[str], list[str]]:
    power = clamp(power, 0, 255)

    meta = ACTIONS.get(action)
    if not meta:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    build: Callable[[int], list[str]] = meta["build"]
    lines = build(power)

    mgr = _need_serial_mgr()
    replies = await mgr.send_cmds(lines, max_wait_s_each=2.5)
    return lines, replies


async def process_joystick(data: JoystickIn) -> JoystickOut:
    x = deadzone(data.x, data.deadzone)
    y = deadzone(data.y, data.deadzone)

    x = int(round(x * data.scale))
    y = int(round(y * data.scale))

    a, b = mix_tank(x, y)
    lines = [f"SetAEngine {a}", f"SetBEngine {b}"]

    mgr = _need_serial_mgr()
    replies = await mgr.send_cmds(lines, max_wait_s_each=2.5)

    return JoystickOut(
        input={"x": data.x, "y": data.y, "deadzone": data.deadzone, "scale": data.scale},
        motor_a=a,
        motor_b=b,
        sent=lines,
        replies=replies,
    )

@app.get("/health")
async def health():
    try:
        mgr = _need_serial_mgr()
        reply = await mgr.send_cmd("PING", expect_prefixes_upper=["OK PONG"], max_wait_s=2.0, pre_drain_s=0.0)
        return {"ok": True, "arduino": reply, "servo_pwr": SERVO_PWR_MODE_ACTIVE}
    except HTTPException as e:
        return {"ok": False, "error": e.detail}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/motor", response_model=MotorCommandOut)
async def motor(cmd: MotorCommandIn):
    line = f"{cmd.cmd} {cmd.speed}"
    try:
        mgr = _need_serial_mgr()
        exp = _infer_expect_prefixes_upper(line)
        reply = await mgr.send_cmd(line, expect_prefixes_upper=exp, max_wait_s=2.5)
        return MotorCommandOut(sent=line, reply=reply)
    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/joystick", response_model=JoystickOut)
async def joystick(data: JoystickIn):
    try:
        return await process_joystick(data)
    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/servo/power")
async def get_servo_power_mode():
    return {
        "mode": SERVO_PWR_MODE_ACTIVE,
        "hint": "Set via POST /servo/power or env SERVO_PWR_MODE at boot",
    }


@app.post("/servo/power", response_model=ServoPowerOut)
async def set_servo_power_mode(data: ServoPowerIn):
    """
    Меняет режим питания сервоприводов на лету:
    - ARDUINO: безопасные лимиты, плавное движение (режим экономии тока)
    - EXTERNAL: полный диапазон 0..180
    """
    global SERVO_PWR_MODE_ACTIVE
    try:
        mgr = _need_serial_mgr()
        line = f"ServoPwr {data.mode}"
        reply = await mgr.send_cmd(
            line,
            expect_prefixes_upper=["OK SERVO_PWR"],
            max_wait_s=3.0,
            pre_drain_s=0.0,
            close_on_error=False,
        )
        SERVO_PWR_MODE_ACTIVE = data.mode
        return ServoPowerOut(mode=data.mode, sent=line, reply=reply)

    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/servo", response_model=ServoCommandOut)
async def servo_cmd(data: ServoCommandIn):
    """
    Универсальный эндпоинт:
    POST /servo
    {
      "cmd": "SetServoA",
      "deg": 90
    }
    """
    line = f"{data.cmd} {data.deg}"
    try:
        mgr = _need_serial_mgr()
        exp = _infer_expect_prefixes_upper(line)
        reply = await mgr.send_cmd(line, expect_prefixes_upper=exp, max_wait_s=3.5)
        return ServoCommandOut(sent=line, reply=reply)

    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/servo/a", response_model=ServoCommandOut)
async def servo_a(deg: int = Query(..., ge=0, le=180)):
    return await servo_cmd(ServoCommandIn(cmd="SetServoA", deg=deg))


@app.post("/servo/b", response_model=ServoCommandOut)
async def servo_b(deg: int = Query(..., ge=0, le=180)):
    return await servo_cmd(ServoCommandIn(cmd="SetServoB", deg=deg))


@app.post("/servo/all", response_model=ServoCommandOut)
async def servo_all(deg: int = Query(..., ge=0, le=180)):
    return await servo_cmd(ServoCommandIn(cmd="SetServoAll", deg=deg))


@app.post("/servo/center", response_model=ServoCommandOut)
async def servo_center():
    """
    Быстрый центр (90° для обоих)
    """
    return await servo_cmd(ServoCommandIn(cmd="SetServoAll", deg=90))


@app.get("/actions/list")
async def list_actions():
    return {"actions": [{"name": name, "title": meta["title"]} for name, meta in ACTIONS.items()]}


@app.post("/actions/run", response_model=ActionOut)
async def actions_run(data: ActionIn):
    if data.action not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action: {data.action}")

    try:
        sent, replies = await run_action(data.action, data.power)

        if data.duration_ms > 0:
            await asyncio.sleep(data.duration_ms / 1000.0)
            stop_sent, stop_replies = await run_action("stop", 0)
            sent += stop_sent
            replies += stop_replies

        return ActionOut(action=data.action, sent=sent, replies=replies)

    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/actions/stop")
async def action_stop():
    sent, replies = await run_action("stop", 0)
    return {"sent": sent, "replies": replies}


@app.post("/actions/forward")
async def action_forward(power: int = Query(160, ge=0, le=255)):
    sent, replies = await run_action("forward", power)
    return {"sent": sent, "replies": replies}


@app.post("/actions/backward")
async def action_backward(power: int = Query(160, ge=0, le=255)):
    sent, replies = await run_action("backward", power)
    return {"sent": sent, "replies": replies}


@app.post("/actions/left")
async def action_left(power: int = Query(160, ge=0, le=255)):
    sent, replies = await run_action("turn_left", power)
    return {"sent": sent, "replies": replies}


@app.post("/actions/right")
async def action_right(power: int = Query(160, ge=0, le=255)):
    sent, replies = await run_action("turn_right", power)
    return {"sent": sent, "replies": replies}
@app.websocket("/ws/joystick")
async def ws_joystick(websocket: WebSocket):
    rid = websocket.headers.get("x-request-id") or str(uuid.uuid4())
    token = REQUEST_ID.set(rid)

    client_host = getattr(websocket.client, "host", "-")
    client_port = getattr(websocket.client, "port", "-")

    await websocket.accept()
    log.info("↔ WS CONNECT /ws/joystick | from=%s:%s | rid=%s", client_host, client_port, rid)

    last_client_msg = time.monotonic()

    latest: Optional[JoystickIn] = None
    latest_seq = 0
    sent_seq = 0
    latest_lock = asyncio.Lock()
    new_data_event = asyncio.Event()

    async def safe_stop(reason: str):
        if not WS_STOP_ON_CLOSE:
            return
        try:
            mgr = _need_serial_mgr()
            log.info("WS STOP (%s) | rid=%s", reason, rid)
            await mgr.send_cmds(["SetAEngine 0", "SetBEngine 0"])
        except Exception as e:
            log.warning("WS STOP FAILED (%s) | rid=%s | err=%s", reason, rid, repr(e))

    async def receiver_loop():
        nonlocal last_client_msg, latest, latest_seq
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception:
                raise WebSocketDisconnect(code=1001)

            last_client_msg = time.monotonic()

            if isinstance(msg, dict) and msg.get("type") in ("pong", "ping"):
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "t": time.time()})
                continue

            try:
                data = JoystickIn(**msg)
            except Exception as e:
                await websocket.send_json({"type": "error", "detail": f"bad payload: {e}"})
                continue

            async with latest_lock:
                latest = data
                latest_seq += 1
                new_data_event.set()

    async def sender_loop():
        nonlocal sent_seq
        min_interval = 1.0 / max(1.0, WS_MAX_RATE_HZ)
        last_send = 0.0

        while True:
            await new_data_event.wait()
            new_data_event.clear()

            while True:
                async with latest_lock:
                    if latest is None or sent_seq == latest_seq:
                        break
                    data = latest
                    target_seq = latest_seq

                now = time.monotonic()
                dt = now - last_send
                if dt < min_interval:
                    await asyncio.sleep(min_interval - dt)

                try:
                    out = await process_joystick(data)
                    sent_seq = target_seq
                    last_send = time.monotonic()

                    await websocket.send_json(
                        {
                            "type": "joy_ack",
                            "seq": sent_seq,
                            "motor_a": out.motor_a,
                            "motor_b": out.motor_b,
                            "sent": out.sent,
                            "replies": out.replies,
                            "t": time.time(),
                        }
                    )

                except HTTPException as e:
                    await websocket.send_json({"type": "error", "detail": e.detail, "status": e.status_code})
                except Exception as e:
                    await websocket.send_json({"type": "error", "detail": str(e)})

    async def ping_loop():
        nonlocal last_client_msg
        while True:
            await asyncio.sleep(WS_PING_INTERVAL)
            idle = time.monotonic() - last_client_msg
            if idle > WS_PING_TIMEOUT:
                log.warning("WS TIMEOUT idle=%.1fs | rid=%s", idle, rid)
                try:
                    await websocket.close(code=1001)
                finally:
                    return
            try:
                await websocket.send_json({"type": "ping", "t": time.time()})
            except Exception:
                return

    tasks = [
        asyncio.create_task(receiver_loop()),
        asyncio.create_task(sender_loop()),
        asyncio.create_task(ping_loop()),
    ]

    try:
        await websocket.send_json(
            {
                "type": "hello",
                "rid": rid,
                "ping_interval": WS_PING_INTERVAL,
                "ping_timeout": WS_PING_TIMEOUT,
                "max_rate_hz": WS_MAX_RATE_HZ,
            }
        )
    except Exception:
        pass

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc:
                raise exc

    except WebSocketDisconnect:
        log.info("↔ WS DISCONNECT | rid=%s", rid)
    except Exception as e:
        log.warning("↔ WS ERROR | rid=%s | err=%s", rid, repr(e))
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await safe_stop("disconnect/timeout/error")
        REQUEST_ID.reset(token)
        log.info("↔ WS CLOSED | rid=%s", rid)