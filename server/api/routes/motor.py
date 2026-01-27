from fastapi import APIRouter, Depends, HTTPException

import serial

from server.api.deps import get_serial_mgr
from server.serial.manager import SerialManager
from server.serial.protocol import SerialProtocolError, infer_expect_prefixes_upper
from server.schemas.motor import MotorCommandIn, MotorCommandOut

router = APIRouter()


@router.post("/motor", response_model=MotorCommandOut)
async def motor(cmd: MotorCommandIn, serial_mgr: SerialManager = Depends(get_serial_mgr)):
    line = f"{cmd.cmd} {cmd.speed}"
    try:
        exp = infer_expect_prefixes_upper(line)
        reply = await serial_mgr.send_cmd(line, expect_prefixes_upper=exp, max_wait_s=2.5)
        return MotorCommandOut(sent=line, reply=reply)
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
