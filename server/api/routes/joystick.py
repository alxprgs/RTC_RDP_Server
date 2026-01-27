from fastapi import APIRouter, Depends, HTTPException

import serial

from server.api.deps import get_serial_mgr
from server.serial.manager import SerialManager
from server.serial.protocol import SerialProtocolError
from server.schemas.joystick import JoystickIn, JoystickOut
from server.services.joystick import process_joystick

router = APIRouter()


@router.post("/joystick", response_model=JoystickOut)
async def joystick(data: JoystickIn, serial_mgr: SerialManager = Depends(get_serial_mgr)):
    try:
        return await process_joystick(serial_mgr, data)
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
