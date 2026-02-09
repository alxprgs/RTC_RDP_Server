import serial
from fastapi import APIRouter, Depends, HTTPException, Request

from server.api.deps import ensure_not_estopped, ensure_supported_command, get_serial_mgr, is_dev_mode
from server.serial.manager import SerialManager
from server.serial.protocol import SerialProtocolError
from server.schemas.joystick import JoystickIn, JoystickOut
from server.services.joystick import process_joystick

router = APIRouter(tags=["joystick"])


async def joystick_body_supported(data: JoystickIn, request: Request) -> JoystickIn:
    ensure_supported_command(request, ("SetAEngine", "SetBEngine", "SetCEngine", "SetDEngine"))
    return data


@router.post(
    "/joystick",
    response_model=JoystickOut,
    dependencies=[Depends(ensure_not_estopped)],
)
async def joystick(
        request: Request,
        data: JoystickIn = Depends(joystick_body_supported),
        serial_mgr: SerialManager = Depends(get_serial_mgr),
) -> JoystickOut:
    if is_dev_mode(request):
        return JoystickOut(
            motor_a=0,
            motor_b=0,
            motor_c=0,
            motor_d=0,
            raw_x=data.x,
            raw_y=data.y,
            input=data,
            sent=["DEV MODE"],
            replies=["OK DEV"],
        )

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