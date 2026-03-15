from __future__ import annotations

import logging

import serial
from fastapi import APIRouter, Depends, HTTPException, Request

from server.api.deps import (
    ensure_not_estopped,
    ensure_supported_command,
    get_serial_mgr,
    is_dev_mode,
)
from server.schemas.joystick import JoystickIn, JoystickOut
from server.serial.manager import SerialManager
from server.serial.protocol import SerialProtocolError
from server.services.joystick import process_joystick

logger = logging.getLogger(__name__)

router = APIRouter(tags=["joystick"])


async def joystick_body_supported(data: JoystickIn, request: Request) -> JoystickIn:
    """
    Проверяем, что прошивка умеет все команды, которые реально нужны
    для HTTP-джойстика с энкодерной стабилизацией.
    """
    ensure_supported_command(
        request,
        (
            "SetAEngine",
            "SetBEngine",
            "SetCEngine",
            "SetDEngine",
            "GetAllEncoders",
        ),
    )
    return data


@router.post(
    "/joystick",
    response_model=JoystickOut,
    dependencies=[Depends(ensure_not_estopped)],
)
async def joystick(
    request: Request,
    data: JoystickIn = Depends(joystick_body_supported),
    serial_mgr: SerialManager | None = Depends(get_serial_mgr),
) -> JoystickOut:
    """
    HTTP fallback для управления роботом джойстиком.

    Использует ту же бизнес-логику, что и WS:
    - выбирает активную пару моторов,
    - читает энкодеры,
    - применяет коррекцию,
    - отправляет команды на Arduino.
    """
    if is_dev_mode(request):
        return JoystickOut(
            input=data,
            motor_a=0,
            motor_b=0,
            motor_c=0,
            motor_d=0,
            raw_x=data.x,
            raw_y=data.y,
            sent=["DEV MODE"],
            replies=["OK DEV"],
        )

    if serial_mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")

    try:
        return await process_joystick(serial_mgr, data)

    except SerialProtocolError as e:
        logger.warning("Protocol error while processing joystick input: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e

    except TimeoutError as e:
        logger.warning("Timeout while processing joystick input: %s", e)
        raise HTTPException(status_code=504, detail=str(e)) from e

    except serial.SerialException as e:
        logger.error("Serial error while processing joystick input: %s", e)
        raise HTTPException(status_code=503, detail="Serial error") from e

    except HTTPException:
        raise

    except Exception as e:
        logger.exception("Unexpected error while processing joystick input")
        raise HTTPException(status_code=500, detail="Internal server error") from e