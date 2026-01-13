import asyncio
import time
import os
import logging
import uuid
import contextvars
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional, Literal

import serial
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("motor-bridge")
serial_log = logging.getLogger("motor-bridge.serial")

# ВКЛ/ВЫКЛ лог тела запросов (осторожно, может содержать чувствительные данные)
LOG_REQUEST_BODY = os.getenv("LOG_REQUEST_BODY", "0") == "1"
MAX_BODY_PREVIEW = int(os.getenv("MAX_BODY_PREVIEW", "800"))

# ВКЛ/ВЫКЛ лог serial (что отправили/что получили)
SERIAL_LOG = os.getenv("SERIAL_LOG", "0") == "1"
SERIAL_MAX_PREVIEW = int(os.getenv("SERIAL_MAX_PREVIEW", "200"))

WS_PING_INTERVAL = float(os.getenv("WS_PING_INTERVAL", "5"))      # как часто server->ping
WS_PING_TIMEOUT  = float(os.getenv("WS_PING_TIMEOUT", "15"))      # если нет сообщений от клиента — считаем мёртвым
WS_MAX_RATE_HZ   = float(os.getenv("WS_MAX_RATE_HZ", "30"))       # максимум отправок в serial в секунду
WS_STOP_ON_CLOSE = os.getenv("WS_STOP_ON_CLOSE", "1") == "1"      # авто стоп при закрытии

REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def _slog(level: str, msg: str, *args):
    """
    Serial logging helper:
    - если SERIAL_LOG=1 -> INFO/WARNING/ERROR
    - иначе -> DEBUG (не шумит)
    """
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


class SerialManager:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None
        self._lock = asyncio.Lock()

    def connect(self) -> None:
        rid = REQUEST_ID.get()
        if self._ser and self._ser.is_open:
            return

        _slog("info", "CONNECT port=%s baud=%s timeout=%.2fs | rid=%s", self.port, self.baudrate, self.timeout, rid)
        try:
            self._ser = serial.Serial(
                self.port,
                self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout,
            )
        except Exception as e:
            _slog("error", "CONNECT FAILED port=%s | rid=%s | err=%s", self.port, rid, repr(e))
            raise

        time.sleep(2.0)

        try:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
        except Exception:
            pass

        try:
            _ = self._ser.readline()
        except Exception:
            pass

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

    def _send_line_sync(self, line: str) -> str:
        rid = REQUEST_ID.get()
        self.connect()
        if not self._ser:
            raise RuntimeError("Serial not connected")

        clean = line.strip()
        payload = (clean + "\n").encode("utf-8")

        preview = clean
        if len(preview) > SERIAL_MAX_PREVIEW:
            preview = preview[:SERIAL_MAX_PREVIEW] + "…(truncated)"

        t0 = time.perf_counter()
        _slog("info", "→ TX %r (%d bytes) | rid=%s", preview, len(payload), rid)

        try:
            self._ser.write(payload)
            self._ser.flush()
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            _slog("error", "✖ TX FAILED %r | rid=%s | took=%.1fms | err=%s", preview, rid, dt, repr(e))
            raise

        try:
            resp_bytes = self._ser.readline()
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            _slog("error", "✖ RX FAILED after %r | rid=%s | took=%.1fms | err=%s", preview, rid, dt, repr(e))
            raise

        dt = (time.perf_counter() - t0) * 1000.0

        resp = resp_bytes.decode("utf-8", errors="replace").strip()
        resp_preview = resp
        if len(resp_preview) > SERIAL_MAX_PREVIEW:
            resp_preview = resp_preview[:SERIAL_MAX_PREVIEW] + "…(truncated)"

        if not resp:
            _slog("warning", "← RX <EMPTY> after %r | rid=%s | took=%.1fms", preview, rid, dt)
            raise TimeoutError("No response from Arduino")

        _slog("info", "← RX %r | rid=%s | took=%.1fms", resp_preview, rid, dt)
        return resp

    async def send_line(self, line: str) -> str:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._send_line_sync, line)
            except Exception:
                self.close()
                raise

    async def send_lines(self, lines: list[str]) -> list[str]:
        """Отправить несколько строк подряд под одним lock (важно для A+B)."""
        async with self._lock:
            rid = REQUEST_ID.get()
            _slog("info", "BATCH START n=%d | rid=%s", len(lines), rid)
            try:
                replies: list[str] = []
                for line in lines:
                    replies.append(await asyncio.to_thread(self._send_line_sync, line))
                _slog("info", "BATCH OK n=%d | rid=%s", len(lines), rid)
                return replies
            except Exception as e:
                _slog("warning", "BATCH FAILED n=%d | rid=%s | err=%s", len(lines), rid, repr(e))
                self.close()
                raise


SERIAL_PORT = "COM11"
serial_mgr = SerialManager(SERIAL_PORT, baudrate=115200, timeout=1.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Arduino Motor Bridge (serial_port=%s)", SERIAL_PORT)
    try:
        yield
    finally:
        serial_mgr.close()
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
            request._receive = receive

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

CmdName = Literal["SetAEngine", "SetBEngine", "SetAllEngine"]

class MotorCommandIn(BaseModel):
    cmd: CmdName
    speed: int = Field(ge=-255, le=255)

class MotorCommandOut(BaseModel):
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
    "stop":       {"title": "Стоп",            "build": lambda p: _a_b(0, 0)},
    "forward":    {"title": "Вперёд",          "build": lambda p: _a_b(p, p)},
    "backward":   {"title": "Назад",           "build": lambda p: _a_b(-p, -p)},
    "turn_left":  {"title": "Поворот влево",   "build": lambda p: _a_b(int(p * 0.4), p)},
    "turn_right": {"title": "Поворот вправо",  "build": lambda p: _a_b(p, int(p * 0.4))},
    "spin_left":  {"title": "Разворот влево",  "build": lambda p: _a_b(-p, p)},
    "spin_right": {"title": "Разворот вправо", "build": lambda p: _a_b(p, -p)},
    "slow_mode":  {"title": "Медленный режим", "build": lambda p: _a_b(int(p * 0.3), int(p * 0.3))},
}

async def run_action(action: str, power: int) -> tuple[list[str], list[str]]:
    power = clamp(power, 0, 255)

    meta = ACTIONS.get(action)
    if not meta:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    build: Callable[[int], list[str]] = meta["build"]
    lines = build(power)

    replies = await serial_mgr.send_lines(lines)
    return lines, replies

async def process_joystick(data: JoystickIn) -> JoystickOut:
    x = deadzone(data.x, data.deadzone)
    y = deadzone(data.y, data.deadzone)

    x = int(round(x * data.scale))
    y = int(round(y * data.scale))

    a, b = mix_tank(x, y)
    lines = [f"SetAEngine {a}", f"SetBEngine {b}"]

    replies = await serial_mgr.send_lines(lines)
    for r in replies:
        if r.startswith("ERR"):
            raise HTTPException(status_code=400, detail={"sent": lines, "replies": replies})

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
        reply = await serial_mgr.send_line("PING")
        return {"ok": True, "arduino": reply}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/motor", response_model=MotorCommandOut)
async def motor(cmd: MotorCommandIn):
    line = f"{cmd.cmd} {cmd.speed}"
    try:
        reply = await serial_mgr.send_line(line)
        if reply.startswith("ERR"):
            raise HTTPException(status_code=400, detail=reply)
        return MotorCommandOut(sent=line, reply=reply)
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
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

        for r in replies:
            if r.startswith("ERR"):
                raise HTTPException(status_code=400, detail={"sent": sent, "replies": replies})

        return ActionOut(action=data.action, sent=sent, replies=replies)

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
            log.info("WS STOP (%s) | rid=%s", reason, rid)
            await serial_mgr.send_lines(["SetAEngine 0", "SetBEngine 0"])
        except Exception as e:
            log.warning("WS STOP FAILED (%s) | rid=%s | err=%s", reason, rid, repr(e))

    async def receiver_loop():
        nonlocal last_client_msg, latest, latest_seq
        while True:
            msg = await websocket.receive_json()
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

                    await websocket.send_json({
                        "type": "joy_ack",
                        "seq": sent_seq,
                        "motor_a": out.motor_a,
                        "motor_b": out.motor_b,
                        "sent": out.sent,
                        "replies": out.replies,
                        "t": time.time(),
                    })

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
        await websocket.send_json({
            "type": "hello",
            "rid": rid,
            "ping_interval": WS_PING_INTERVAL,
            "ping_timeout": WS_PING_TIMEOUT,
            "max_rate_hz": WS_MAX_RATE_HZ,
        })
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