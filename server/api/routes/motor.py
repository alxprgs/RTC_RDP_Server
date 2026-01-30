from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from server.api.deps import ensure_not_estopped, ensure_supported_command
from server.schemas.motor import MotorCommandIn, MotorCommandOut
from server.serial.protocol import infer_expect_prefixes_upper, SerialProtocolError
from server.serial.manager import SerialManager

router = APIRouter(tags=["motor"])


def _serial(request: Request) -> SerialManager:
    mgr = getattr(request.app.state, "serial_mgr", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")
    return mgr


async def motor_body_supported(cmd: MotorCommandIn, request: Request) -> MotorCommandIn:
    ensure_supported_command(request, (cmd.cmd,))
    return cmd


@router.post("/motor", response_model=MotorCommandOut, dependencies=[Depends(ensure_not_estopped)])
async def motor(
    request: Request,
    cmd: MotorCommandIn = Depends(motor_body_supported),
) -> MotorCommandOut:
    line = f"{cmd.cmd} {cmd.speed}"
    try:
        mgr = _serial(request)
        exp = infer_expect_prefixes_upper(line)
        reply = await mgr.send_cmd(line, expect_prefixes_upper=exp, max_wait_s=2.5)
        return MotorCommandOut(sent=line, reply=reply)
    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
