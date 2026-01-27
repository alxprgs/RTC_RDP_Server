from __future__ import annotations

from fastapi import Request, HTTPException

from server.serial.manager import SerialManager
from server.core.config import Settings

from typing import Iterable, Callable, Optional, Set, List

def ensure_not_estopped(request: Request) -> None:
    estop = bool(getattr(request.app.state, "estop", False))
    if estop:
        raise HTTPException(
            status_code=423,
            detail="E-STOP is active. Call POST /estop/reset to unlock.",
        )


def _supported_commands_lower(request: Request) -> Optional[Set[str]]:
    """
    Возвращает set команд (lowercase), если прошивка их прислала.
    Если прошивка не умеет CAPS/commands -> None (тогда НЕ блокируем совместимость).
    """
    info = getattr(request.app.state, "device_info", None) or {}

    cmds = info.get("supported_commands")
    if not cmds:
        caps = info.get("caps") or {}
        cmds = caps.get("commands") or caps.get("supported_commands")

    if not cmds:
        return None

    return {str(c).strip().lower() for c in cmds if str(c).strip()}


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_serial_mgr(request: Request) -> SerialManager:
    mgr = getattr(request.app.state, "serial_mgr", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")
    return mgr

def ensure_supported_command(request: Request, command: str) -> None:
    """
    Если прошивка отдала список supported_commands, то строго проверяем.
    Если списка нет (старая прошивка) — НЕ блокируем.
    """
    info = getattr(request.app.state, "device_info", None) or {}
    cmds = info.get("supported_commands")
    if not cmds:
        return

    cmd_norm = command.strip().lower()
    cmds_norm = {str(c).strip().lower() for c in cmds}
    if cmd_norm not in cmds_norm:
        raise HTTPException(
            status_code=501,
            detail=f"Firmware does not support command: {command}",
        )

def require_firmware_commands(required: List[str]) -> Callable:
    """
    Usage:
      dependencies=[Depends(require_firmware_commands(["SetServo"]))]

    Обрати внимание: required -> list[str]
    """
    def _dep(request: Request) -> None:
        ensure_supported_command(request, required)

    return _dep