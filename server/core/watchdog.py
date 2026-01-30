from __future__ import annotations

import asyncio
import time
from typing import Optional

from fastapi import FastAPI


async def _try_stop_motors(app: FastAPI, reason: str) -> bool:
    mgr = getattr(app.state, "serial_mgr", None)
    if mgr is None:
        return False
    try:
        await mgr.send_cmds(["SetAEngine 0", "SetBEngine 0"], max_wait_s_each=2.0, mark_activity=False)
        return True
    except Exception:
        return False


async def _try_servo_safe_pose(app: FastAPI, reason: str) -> bool:
    mgr = getattr(app.state, "serial_mgr", None)
    s = getattr(app.state, "settings", None)
    if mgr is None or s is None:
        return False

    # если E-STOP активен — сервы лучше не двигать
    if bool(getattr(app.state, "estop", False)):
        return False

    # строим безопасную позицию из settings.servo_safe_pose / center
    lines = []
    for sid in range(1, int(s.servo_count) + 1):
        deg = int(s.servo_safe_pose.get(sid, s.servo_center_deg))
        lines.append(f"SetServo {sid} {deg}")

    try:
        # mark_activity=False, чтобы сторожевой таймер не “кормил сам себя”
        await mgr.send_cmds(lines, max_wait_s_each=3.5, mark_activity=False)
        return True
    except Exception:
        return False


async def watchdog_loop(app: FastAPI) -> None:
    s = app.state.settings

    motor_applied = False
    servo_applied = False

    while True:
        await asyncio.sleep(float(s.watchdog_tick_s))

        if not bool(s.watchdog_enabled):
            # если отключили на лету — просто ничего не делаем
            continue

        mgr = getattr(app.state, "serial_mgr", None)
        if mgr is None:
            continue

        now = time.monotonic()

        # --- Сторожевой таймер моторов
        motor_idle = float(getattr(s, "watchdog_motor_idle_s", 0.0))
        if motor_idle > 0 and mgr.last_motor_ts > 0 and (now - mgr.last_motor_ts) >= motor_idle:
            if not motor_applied:
                ok = await _try_stop_motors(app, "motor_idle")
                motor_applied = ok or True  # даже если не получилось — не спамим каждую итерацию
        else:
            motor_applied = False

        if bool(getattr(s, "watchdog_servo_safe_enabled", False)):
            servo_idle = float(getattr(s, "watchdog_servo_idle_s", 0.0))
            if servo_idle > 0 and mgr.last_servo_ts > 0 and (now - mgr.last_servo_ts) >= servo_idle:
                if not servo_applied:
                    ok = await _try_servo_safe_pose(app, "servo_idle")
                    servo_applied = ok or True
            else:
                servo_applied = False


def start_watchdog(app: FastAPI) -> None:
    if getattr(app.state, "watchdog_task", None) is not None:
        return
    app.state.watchdog_task = asyncio.create_task(watchdog_loop(app))


async def stop_watchdog(app: FastAPI) -> None:
    task: Optional[asyncio.Task] = getattr(app.state, "watchdog_task", None)
    app.state.watchdog_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
