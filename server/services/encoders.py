from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Awaitable, Callable

from server.serial.manager import SerialManager

_ENCODER_BLOCK_RE = re.compile(
    r"\[(\d+):pos=(-?\d+),spd=(-?\d+(?:\.\d+)?)\]"
)


def _validate_encoder_id(encoder_id: int) -> None:
    if encoder_id not in (1, 2, 3, 4):
        raise ValueError("encoder_id must be in range 1..4")


def parse_all_encoders_reply(reply: str) -> dict[str, Any]:
    matches = _ENCODER_BLOCK_RE.findall(reply or "")
    if not matches:
        raise ValueError(f"Cannot parse encoder reply: {reply!r}")

    data: dict[str, Any] = {}
    for enc_id, pos, speed in matches:
        data[f"enc{enc_id}_pos"] = int(pos)
        data[f"enc{enc_id}_speed"] = float(speed)

    # Гарантируем наличие всех 4 энкодеров
    for i in range(1, 5):
        data.setdefault(f"enc{i}_pos", 0)
        data.setdefault(f"enc{i}_speed", 0.0)

    return data


async def get_all_encoders(serial_mgr: SerialManager) -> dict[str, Any]:
    reply = await serial_mgr.send_cmd(
        "GetAllEncoders",
        expect_prefixes_upper=["OK ENCODERS"],
    )
    return parse_all_encoders_reply(reply)


async def get_encoder(serial_mgr: SerialManager, encoder_id: int) -> dict[str, Any]:
    _validate_encoder_id(encoder_id)
    data = await get_all_encoders(serial_mgr)
    return {
        "pos": data[f"enc{encoder_id}_pos"],
        "speed": data[f"enc{encoder_id}_speed"],
    }


async def reset_encoder(serial_mgr: SerialManager, encoder_id: int) -> bool:
    _validate_encoder_id(encoder_id)
    reply = await serial_mgr.send_cmd(
        f"ResetEncoder {encoder_id}",
        expect_prefixes_upper=["OK ENCODER_RESET"],
    )
    return reply.upper().startswith("OK ENCODER_RESET")


async def reset_all_encoders(serial_mgr: SerialManager) -> bool:
    results: list[bool] = []
    for encoder_id in (1, 2, 3, 4):
        results.append(await reset_encoder(serial_mgr, encoder_id))
    return all(results)


async def stream_encoders(
    serial_mgr: SerialManager,
    callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    duration: float | None = None,
    interval_ms: int = 50,
) -> list[dict[str, Any]] | None:
    """
    Безопасный серверный "стрим" через polling GetAllEncoders.
    Не использует firmware-команду StreamEncoders, потому что текущий
    SerialManager работает в режиме request/response.
    """
    interval_ms = max(10, int(interval_ms))
    started_at = time.monotonic()
    collected: list[dict[str, Any]] = []

    while True:
        if duration is not None and duration > 0:
            if time.monotonic() - started_at >= duration:
                break

        packet = {
            "timestamp": int(time.time() * 1000),
            **(await get_all_encoders(serial_mgr)),
        }

        if callback is None:
            collected.append(packet)
        else:
            result = callback(packet)
            if asyncio.iscoroutine(result):
                await result

        await asyncio.sleep(interval_ms / 1000.0)

    return collected if callback is None else None