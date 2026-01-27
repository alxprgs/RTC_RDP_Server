from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException

from server.serial.device_probe import probe_device

router = APIRouter(tags=["device"])


def _serial(request: Request):
    mgr = getattr(request.app.state, "serial_mgr", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")
    return mgr


@router.get("/device")
async def device_info(request: Request):
    return {
        "serial_port": getattr(request.app.state, "serial_port", None),
        "servo_pwr": getattr(request.app.state, "servo_pwr_mode_active", None),
        "device": getattr(request.app.state, "device_info", None),
    }


@router.post("/device/refresh")
async def device_refresh(request: Request):
    s = request.app.state.settings
    mgr = _serial(request)
    info = await probe_device(mgr, timeout_s=float(s.device_probe_timeout_s))
    request.app.state.device_info = info
    return info
