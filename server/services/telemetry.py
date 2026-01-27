from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from server.serial.manager import SerialManager
from server.serial.protocol import parse_arduino_telem_reply


async def get_arduino_telemetry_safe(serial_mgr: SerialManager | None) -> dict[str, Any]:
    try:
        if serial_mgr is None:
            raise HTTPException(status_code=503, detail="Serial not initialized yet")

        last_err: str | None = None

        for _ in range(2):
            try:
                reply = await serial_mgr.send_cmd(
                    "TELEM",
                    expect_prefixes_upper=["OK TELEM"],
                    max_wait_s=2.5,
                    pre_drain_s=0.0,
                    close_on_error=True,
                )
                data = parse_arduino_telem_reply(reply)
                return {"ok": True, "data": data}
            except Exception as e:
                last_err = str(e)
                await asyncio.sleep(0.05)

        return {"ok": False, "error": last_err or "unknown error"}

    except HTTPException as e:
        return {"ok": False, "error": str(e.detail)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
