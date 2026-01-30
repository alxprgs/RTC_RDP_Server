from fastapi import APIRouter, Depends, Request

from server.api.deps import get_serial_mgr, get_settings
from server.serial.manager import SerialManager
from server.core.config import Settings
from server.services.telemetry import get_arduino_telemetry_safe
from server.utils.system_snapshot import get_system_snapshot

router = APIRouter()


@router.get("/telemetry")
async def telemetry(
    request: Request,
    disk: bool = True,
    net: bool = True,
    sensors: bool = True,
    arduino: bool = True,
    settings: Settings = Depends(get_settings),
    serial_mgr: SerialManager = Depends(get_serial_mgr),
) -> dict[str, object]:
    host = get_system_snapshot(
        settings=settings,
        include_disk=disk,
        include_network=net,
        include_sensors=sensors,
    )

    ard = None
    if arduino:
        ard = await get_arduino_telemetry_safe(serial_mgr)

    return {
        "host": host,
        "arduino": ard,
        "servo_pwr": getattr(request.app.state, "servo_pwr_mode_active", None),
        "serial_port": getattr(request.app.state, "serial_port", None),
    }


@router.get("/telemetry/arduino")
async def telemetry_arduino(
    serial_mgr: SerialManager = Depends(get_serial_mgr),
) -> dict[str, object]:
    return await get_arduino_telemetry_safe(serial_mgr)
