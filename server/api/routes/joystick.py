import serial
from fastapi import APIRouter, Depends, HTTPException, Request

from server.api.deps import ensure_not_estopped, ensure_supported_command, get_serial_mgr
from server.serial.manager import SerialManager
from server.serial.protocol import SerialProtocolError
from server.schemas.joystick import JoystickIn, JoystickOut
from server.services.joystick import process_joystick

router = APIRouter(tags=["joystick"])


async def joystick_body_supported(data: JoystickIn, request: Request) -> JoystickIn:
    ensure_supported_command(request, ("SetAEngine", "SetBEngine"))
    return data


@router.post(
    "/joystick",
    response_model=JoystickOut,
    dependencies=[Depends(ensure_not_estopped)],
)
async def joystick(
    data: JoystickIn = Depends(joystick_body_supported),
    serial_mgr: SerialManager = Depends(get_serial_mgr),
) -> JoystickOut:
    try:
        return await process_joystick(serial_mgr, data)
    except SerialProtocolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail="Serial error") from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
