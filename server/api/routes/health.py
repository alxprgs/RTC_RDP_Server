from fastapi import APIRouter, Depends, Request, HTTPException

from server.api.deps import get_serial_mgr
from server.serial.manager import SerialManager

router = APIRouter()


@router.get("/health")
async def health(request: Request, serial_mgr: SerialManager = Depends(get_serial_mgr)):
    try:
        reply = await serial_mgr.send_cmd("PING", expect_prefixes_upper=["OK PONG"], max_wait_s=2.0, pre_drain_s=0.0)
        return {
            "ok": True,
            "arduino": reply,
            "servo_pwr": getattr(request.app.state, "servo_pwr_mode_active", None),
        }
    except HTTPException as e:
        return {"ok": False, "error": e.detail}
    except Exception as e:
        return {"ok": False, "error": str(e)}
