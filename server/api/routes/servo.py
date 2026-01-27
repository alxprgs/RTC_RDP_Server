from fastapi import APIRouter, Depends, HTTPException, Query, Request
import serial

from server.api.deps import get_serial_mgr
from server.serial.manager import SerialManager
from server.serial.protocol import SerialProtocolError, infer_expect_prefixes_upper
from server.schemas.servo import (
    ServoCommandIn, ServoCommandOut,
    ServoPowerIn, ServoPowerOut,
)

router = APIRouter()


@router.get("/servo/power")
async def get_servo_power_mode(request: Request):
    return {
        "mode": getattr(request.app.state, "servo_pwr_mode_active", None),
        "hint": "Set via POST /servo/power or env SERVO_PWR_MODE at boot",
    }


@router.post("/servo/power", response_model=ServoPowerOut)
async def set_servo_power_mode(
    request: Request,
    data: ServoPowerIn,
    serial_mgr: SerialManager = Depends(get_serial_mgr),
):
    try:
        line = f"ServoPwr {data.mode}"
        reply = await serial_mgr.send_cmd(
            line,
            expect_prefixes_upper=["OK SERVO_PWR"],
            max_wait_s=3.0,
            pre_drain_s=0.0,
            close_on_error=False,
        )
        request.app.state.servo_pwr_mode_active = data.mode
        return ServoPowerOut(mode=data.mode, sent=line, reply=reply)

    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servo", response_model=ServoCommandOut)
async def servo_cmd(data: ServoCommandIn, serial_mgr: SerialManager = Depends(get_serial_mgr)):
    line = f"{data.cmd} {data.deg}"
    try:
        exp = infer_expect_prefixes_upper(line)
        reply = await serial_mgr.send_cmd(line, expect_prefixes_upper=exp, max_wait_s=3.5)
        return ServoCommandOut(sent=line, reply=reply)

    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Serial error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servo/a", response_model=ServoCommandOut)
async def servo_a(deg: int = Query(..., ge=0, le=180), serial_mgr: SerialManager = Depends(get_serial_mgr)):
    return await servo_cmd(ServoCommandIn(cmd="SetServoA", deg=deg), serial_mgr)


@router.post("/servo/b", response_model=ServoCommandOut)
async def servo_b(deg: int = Query(..., ge=0, le=180), serial_mgr: SerialManager = Depends(get_serial_mgr)):
    return await servo_cmd(ServoCommandIn(cmd="SetServoB", deg=deg), serial_mgr)


@router.post("/servo/all", response_model=ServoCommandOut)
async def servo_all(deg: int = Query(..., ge=0, le=180), serial_mgr: SerialManager = Depends(get_serial_mgr)):
    return await servo_cmd(ServoCommandIn(cmd="SetServoAll", deg=deg), serial_mgr)


@router.post("/servo/center", response_model=ServoCommandOut)
async def servo_center(serial_mgr: SerialManager = Depends(get_serial_mgr)):
    return await servo_cmd(ServoCommandIn(cmd="SetServoAll", deg=90), serial_mgr)
