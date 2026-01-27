from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional, Literal

from server.core.config import Settings
from server.serial.manager import SerialManager

try:
    from InquirerPy import inquirer
except Exception:
    inquirer = None


ServoPowerMode = Literal["ARDUINO", "EXTERNAL"]


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


async def ensure_servo_power_mode_on_boot(serial_mgr: SerialManager, settings: Settings) -> str:
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
            await serial_mgr.send_cmd(
                cmd,
                expect_prefixes_upper=["OK SERVO_PWR"],
                max_wait_s=3.0,
                pre_drain_s=0.0,
                close_on_error=False,
            )
            return mode
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.25)

    raise RuntimeError(f"Не удалось установить ServoPwr: {last_err}") from last_err
