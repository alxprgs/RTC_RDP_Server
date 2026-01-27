from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from server.serial.manager import SerialManager
from server.utils.math_mix import clamp


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


async def run_action(serial_mgr: SerialManager, action: str, power: int) -> tuple[list[str], list[str]]:
    power = clamp(power, 0, 255)

    meta = ACTIONS.get(action)
    if not meta:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    build: Callable[[int], list[str]] = meta["build"]
    lines = build(power)
    replies = await serial_mgr.send_cmds(lines, max_wait_s_each=2.5)
    return lines, replies
