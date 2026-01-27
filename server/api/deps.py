from fastapi import Request, HTTPException

from server.serial.manager import SerialManager
from server.core.config import Settings

def ensure_not_estopped(request: Request) -> None:
    estop = bool(getattr(request.app.state, "estop", False))
    if estop:
        raise HTTPException(
            status_code=423,
            detail="E-STOP is active. Call POST /estop/reset to unlock.",
        )

def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_serial_mgr(request: Request) -> SerialManager:
    mgr = getattr(request.app.state, "serial_mgr", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")
    return mgr
