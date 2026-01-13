import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional, Literal

import serial
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

class SerialManager:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None
        self._lock = asyncio.Lock()

    def connect(self) -> None:
        if self._ser and self._ser.is_open:
            return

        self._ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )

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

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def _send_line_sync(self, line: str) -> str:
        self.connect()
        if not self._ser:
            raise RuntimeError("Serial not connected")

        payload = (line.strip() + "\n").encode("utf-8")
        self._ser.write(payload)
        self._ser.flush()

        resp = self._ser.readline().decode("utf-8", errors="replace").strip()
        if not resp:
            raise TimeoutError("No response from Arduino")
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
            try:
                replies: list[str] = []
                for line in lines:
                    replies.append(await asyncio.to_thread(self._send_line_sync, line))
                return replies
            except Exception:
                self.close()
                raise


SERIAL_PORT = "COM5"
serial_mgr = SerialManager(SERIAL_PORT, baudrate=115200, timeout=1.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        serial_mgr.close()


app = FastAPI(title="Arduino Motor Bridge", lifespan=lifespan)

def clamp(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v

def deadzone(v: int, dz: int) -> int:
    return 0 if abs(v) < dz else v

def mix_tank(x: int, y: int) -> tuple[int, int]:
    """
    Классический микс:
      A = Y + X
      B = Y - X
    """
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
    x = deadzone(data.x, data.deadzone)
    y = deadzone(data.y, data.deadzone)

    x = int(round(x * data.scale))
    y = int(round(y * data.scale))

    a, b = mix_tank(x, y)

    lines = [f"SetAEngine {a}", f"SetBEngine {b}"]
    try:
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