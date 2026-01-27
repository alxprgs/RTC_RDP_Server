from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException

router = APIRouter(tags=["safety"])


def _serial(request: Request):
    mgr = getattr(request.app.state, "serial_mgr", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")
    return mgr


@router.get("/safety/state")
async def safety_state(request: Request):
    return {
        "estop_enabled": bool(request.app.state.settings.estop_enabled),
        "estop": bool(getattr(request.app.state, "estop", False)),
    }


@router.post("/estop")
async def estop_on(request: Request):
    if not request.app.state.settings.estop_enabled:
        raise HTTPException(status_code=404, detail="E-STOP is disabled in settings")

    request.app.state.estop = True
    mgr = _serial(request)

    # Максимально безопасно: сразу стоп моторов текущими командами
    try:
        await mgr.send_cmds(["SetAEngine 0", "SetBEngine 0"], max_wait_s_each=2.5)
    except Exception:
        pass

    # И отдельная команда для новой прошивки (может пока не существовать) — не критично
    try:
        await mgr.send_cmd("EStop", expect_prefixes_upper=["OK ESTOP"], max_wait_s=2.5, pre_drain_s=0.0, close_on_error=False)
    except Exception:
        pass

    return {"ok": True, "estop": True}


@router.post("/estop/reset")
async def estop_reset(request: Request):
    if not request.app.state.settings.estop_enabled:
        raise HTTPException(status_code=404, detail="E-STOP is disabled in settings")

    request.app.state.estop = False
    mgr = _serial(request)

    try:
        await mgr.send_cmd("EStop RESET", expect_prefixes_upper=["OK ESTOP"], max_wait_s=2.5, pre_drain_s=0.0, close_on_error=False)
    except Exception:
        pass

    return {"ok": True, "estop": False}
