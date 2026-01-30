from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from server.api.deps import ensure_not_estopped, require_firmware_commands
from server.schemas.servo import (
    ServoPowerIn,
    ServoPowerOut,
    ServoSetIn,
    ServoSetOut,
    ServoBatchIn,
    ServoBatchOut,
)
from server.serial.manager import SerialManager
from server.services.servo import (
    ServoRuntimeState,
    build_center_items,
    set_servo_batch,
    set_servo_deg,
)

router = APIRouter(tags=["servo"])


def _state(request: Request) -> ServoRuntimeState:
    st = getattr(request.app.state, "servo_state", None)
    if st is None:
        st = ServoRuntimeState()
        request.app.state.servo_state = st
    return st


def _serial(request: Request) -> SerialManager:
    mgr = getattr(request.app.state, "serial_mgr", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")
    return mgr


@router.get("/servo/capabilities")
async def servo_capabilities(request: Request) -> dict[str, object]:
    s = request.app.state.settings
    return {
        "servo_count": s.servo_count,
        "default_range": [s.servo_default_min_deg, s.servo_default_max_deg],
        "limits": {str(k): list(v) for k, v in s.servo_limits.items()},
        "safe_pose": {str(k): int(v) for k, v in s.servo_safe_pose.items()},
        "center_deg": s.servo_center_deg,
        "slew_rate_dps": s.servo_slew_rate_dps,
        "max_cmd_hz": s.servo_max_cmd_hz,
        "rate_limit_mode": s.servo_rate_limit_mode,
    }


@router.get("/servo/state")
async def servo_state(request: Request) -> dict[str, object]:
    s = request.app.state.settings
    st = _state(request)
    return {
        "servo_count": s.servo_count,
        "last_deg": {str(k): int(v) for k, v in st.last_deg.items()},
    }


@router.post(
    "/servo/{servo_id}",
    response_model=ServoSetOut,
    dependencies=[
        Depends(ensure_not_estopped),
        Depends(require_firmware_commands(["SetServo"])),
    ],
)
async def servo_set(
    servo_id: int,
    data: ServoSetIn,
    request: Request,
) -> ServoSetOut:
    s = request.app.state.settings
    st = _state(request)
    mgr = _serial(request)

    return await set_servo_deg(
        settings=s,
        state=st,
        serial_mgr=mgr,
        servo_id=servo_id,
        deg=data.deg,
    )


@router.post(
    "/servo/batch",
    response_model=ServoBatchOut,
    dependencies=[
        Depends(ensure_not_estopped),
        Depends(require_firmware_commands(["SetServo"])),
    ],
)
async def servo_batch(
    data: ServoBatchIn,
    request: Request,
) -> ServoBatchOut:
    s = request.app.state.settings
    st = _state(request)
    mgr = _serial(request)

    items = [(i.id, i.deg) for i in data.items]
    outs = await set_servo_batch(settings=s, state=st, serial_mgr=mgr, items=items)
    return ServoBatchOut(items=outs)


@router.post(
    "/servo/center",
    response_model=ServoBatchOut,
    dependencies=[
        Depends(ensure_not_estopped),
        Depends(require_firmware_commands(["SetServo"])),
    ],
)
async def servo_center(request: Request) -> ServoBatchOut:
    s = request.app.state.settings
    st = _state(request)
    mgr = _serial(request)

    items = build_center_items(s)
    outs = await set_servo_batch(settings=s, state=st, serial_mgr=mgr, items=items)
    return ServoBatchOut(items=outs)


# --- Шорткаты для обратной совместимости (раньше были A/B/All)
@router.post("/servo/a", response_model=ServoSetOut, dependencies=[Depends(ensure_not_estopped)])
async def servo_a(data: ServoSetIn, request: Request) -> ServoSetOut:
    return await servo_set(1, data, request)


@router.post("/servo/b", response_model=ServoSetOut, dependencies=[Depends(ensure_not_estopped)])
async def servo_b(data: ServoSetIn, request: Request) -> ServoSetOut:
    return await servo_set(2, data, request)


@router.post("/servo/all", response_model=ServoBatchOut, dependencies=[Depends(ensure_not_estopped)])
async def servo_all(data: ServoSetIn, request: Request) -> ServoBatchOut:
    s = request.app.state.settings
    items = [{"id": sid, "deg": data.deg} for sid in range(1, s.servo_count + 1)]
    return await servo_batch(ServoBatchIn(items=items), request)


@router.get("/servo/power")
async def get_servo_power_mode(request: Request) -> dict[str, object]:
    return {
        "mode": getattr(request.app.state, "servo_pwr_mode_active", None),
        "hint": "Set via POST /servo/power or env SERVO_PWR_MODE at boot",
    }


@router.post(
    "/servo/power",
    response_model=ServoPowerOut,
    dependencies=[
        Depends(ensure_not_estopped),
        Depends(require_firmware_commands(["SetServo"])),
    ],
)
async def set_servo_power_mode(
    data: ServoPowerIn,
    request: Request,
) -> ServoPowerOut:
    mgr = _serial(request)

    line = f"ServoPwr {data.mode}"
    reply = await mgr.send_cmd(
        line,
        expect_prefixes_upper=["OK SERVO_PWR"],
        max_wait_s=3.0,
        pre_drain_s=0.0,
        close_on_error=False,
    )
    request.app.state.servo_pwr_mode_active = data.mode
    return ServoPowerOut(mode=data.mode, sent=line, reply=reply)
