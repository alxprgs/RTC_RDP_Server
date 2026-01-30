from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.core.config import Settings
from server.serial.manager import SerialManager
from server.services.telemetry import get_arduino_telemetry_safe
from server.utils.system_snapshot import get_system_snapshot

router = APIRouter()


@router.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    await ws.accept()
    try:
        # ws не даёт Request, берём settings из ws.app.state
        settings: Settings = ws.app.state.settings
        serial_mgr: SerialManager | None = getattr(ws.app.state, "serial_mgr", None)

        while True:
            host = get_system_snapshot(
                settings=settings,
                include_disk=False,
                include_network=True,
                include_sensors=True,
            )
            ard = await get_arduino_telemetry_safe(serial_mgr)

            payload = {
                "host": host,
                "arduino": ard,
                "servo_pwr": getattr(ws.app.state, "servo_pwr_mode_active", None),
                "serial_port": getattr(ws.app.state, "serial_port", None),
            }

            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            await ws.send_text("\n")
            await asyncio.sleep(float(settings.stream_interval))

    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
