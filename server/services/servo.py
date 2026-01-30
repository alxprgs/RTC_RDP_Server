from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple, Iterable, List

from fastapi import HTTPException

from server.core.config import Settings
from server.serial.manager import SerialManager
from server.serial.protocol import infer_expect_prefixes_upper
from server.schemas.servo import ServoSetOut


@dataclass
class ServoRuntimeState:
    last_deg: Dict[int, int] = field(default_factory=dict)
    last_update_ts: Dict[int, float] = field(default_factory=dict)  # когда обновляли last_deg
    last_cmd_ts: Dict[int, float] = field(default_factory=dict)     # когда реально отправляли команду


def _clamp(v: int, lo: int, hi: int) -> int:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _validate_servo_id(settings: Settings, servo_id: int) -> None:
    if servo_id < 1 or servo_id > settings.servo_count:
        raise HTTPException(
            status_code=400,
            detail=f"servo id out of range: {servo_id} (allowed 1..{settings.servo_count})",
        )


def _limits_for(settings: Settings, servo_id: int) -> Tuple[int, int]:
    lo, hi = settings.servo_default_min_deg, settings.servo_default_max_deg
    if servo_id in settings.servo_limits:
        lo, hi = settings.servo_limits[servo_id]
    lo = _clamp(int(lo), 0, 180)
    hi = _clamp(int(hi), 0, 180)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _apply_slew_rate(
    *,
    settings: Settings,
    state: ServoRuntimeState,
    servo_id: int,
    target_deg: int,
    now: float,
) -> int:
    rate = float(settings.servo_slew_rate_dps or 0.0)
    if rate <= 0:
        return target_deg

    last = state.last_deg.get(servo_id)
    if last is None:
        return target_deg

    last_ts = state.last_update_ts.get(servo_id, now)
    dt = now - last_ts
    if dt <= 0:
        dt = 0.02  # защита от деления на ноль/слишком маленького dt

    max_delta = rate * dt
    if max_delta < 1.0:
        max_delta = 1.0  # минимальный шаг, чтобы не “застрять”

    delta = target_deg - last
    if abs(delta) <= max_delta:
        return target_deg

    step = int(round(max_delta))
    return last + (step if delta > 0 else -step)


async def _rate_limit_or_fail(
    *,
    settings: Settings,
    state: ServoRuntimeState,
    servo_id: int,
    now: float,
) -> None:
    hz = float(settings.servo_max_cmd_hz or 0.0)
    if hz <= 0:
        return

    min_interval = 1.0 / max(1.0, hz)
    last = state.last_cmd_ts.get(servo_id, 0.0)
    dt = now - last
    if dt >= min_interval:
        return

    wait_s = min_interval - dt
    mode = (settings.servo_rate_limit_mode or "reject").lower().strip()

    if mode == "sleep":
        await asyncio.sleep(wait_s)
        return

    retry_ms = int(round(wait_s * 1000.0))
    raise HTTPException(
        status_code=429,
        detail={
            "error": "servo_rate_limited",
            "servo_id": servo_id,
            "retry_after_ms": retry_ms,
        },
        headers={"Retry-After": f"{max(1, int(wait_s))}"},
    )


async def set_servo_deg(
    *,
    settings: Settings,
    state: ServoRuntimeState,
    serial_mgr: SerialManager,
    servo_id: int,
    deg: int,
) -> ServoSetOut:
    _validate_servo_id(settings, servo_id)

    lo, hi = _limits_for(settings, servo_id)
    requested = int(deg)
    target = _clamp(requested, lo, hi)

    now = time.monotonic()
    target = _apply_slew_rate(settings=settings, state=state, servo_id=servo_id, target_deg=target, now=now)

    await _rate_limit_or_fail(settings=settings, state=state, servo_id=servo_id, now=time.monotonic())

    line = f"SetServo {servo_id} {target}"
    exp = infer_expect_prefixes_upper(line)  # -> OK SETSERVO
    reply = await serial_mgr.send_cmd(line, expect_prefixes_upper=exp, max_wait_s=3.5, pre_drain_s=0.0)

    now2 = time.monotonic()
    state.last_deg[servo_id] = int(target)
    state.last_update_ts[servo_id] = now2
    state.last_cmd_ts[servo_id] = now2

    return ServoSetOut(
        id=servo_id,
        requested_deg=requested,
        applied_deg=int(target),
        sent=line,
        reply=reply,
    )


async def set_servo_batch(
    *,
    settings: Settings,
    state: ServoRuntimeState,
    serial_mgr: SerialManager,
    items: Iterable[tuple[int, int]],
) -> List[ServoSetOut]:
    outs: List[ServoSetOut] = []
    for sid, deg in items:
        outs.append(
            await set_servo_deg(
                settings=settings,
                state=state,
                serial_mgr=serial_mgr,
                servo_id=int(sid),
                deg=int(deg),
            )
        )
    return outs


def build_center_items(settings: Settings) -> list[tuple[int, int]]:
    items: list[tuple[int, int]] = []
    for sid in range(1, settings.servo_count + 1):
        if sid in settings.servo_safe_pose:
            items.append((sid, int(settings.servo_safe_pose[sid])))
        else:
            items.append((sid, int(settings.servo_center_deg)))
    return items
