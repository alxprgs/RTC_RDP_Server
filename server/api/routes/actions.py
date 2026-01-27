from fastapi import APIRouter, Depends, HTTPException, Query
import serial

from server.api.deps import get_serial_mgr
from server.serial.manager import SerialManager
from server.serial.protocol import SerialProtocolError
from server.schemas.actions import ActionIn, ActionOut
from server.services.actions import ACTIONS, run_action

router = APIRouter()


@router.get("/actions/list")
async def list_actions():
    return {"actions": [{"name": name, "title": meta["title"]} for name, meta in ACTIONS.items()]}


@router.post("/actions/run", response_model=ActionOut)
async def actions_run(data: ActionIn, serial_mgr: SerialManager = Depends(get_serial_mgr)):
    if data.action not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action: {data.action}")

    try:
        sent, replies = await run_action(serial_mgr, data.action, data.power)

        if data.duration_ms > 0:
            import asyncio
            await asyncio.sleep(data.duration_ms / 1000.0)
            stop_sent, stop_replies = await run_action(serial_mgr, "stop", 0)
            sent += stop_sent
            replies += stop_replies

        return ActionOut(action=data.action, sent=sent, replies=replies)

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


@router.post("/actions/stop")
async def action_stop(serial_mgr: SerialManager = Depends(get_serial_mgr)):
    sent, replies = await run_action(serial_mgr, "stop", 0)
    return {"sent": sent, "replies": replies}


@router.post("/actions/forward")
async def action_forward(power: int = Query(160, ge=0, le=255), serial_mgr: SerialManager = Depends(get_serial_mgr)):
    sent, replies = await run_action(serial_mgr, "forward", power)
    return {"sent": sent, "replies": replies}


@router.post("/actions/backward")
async def action_backward(power: int = Query(160, ge=0, le=255), serial_mgr: SerialManager = Depends(get_serial_mgr)):
    sent, replies = await run_action(serial_mgr, "backward", power)
    return {"sent": sent, "replies": replies}


@router.post("/actions/left")
async def action_left(power: int = Query(160, ge=0, le=255), serial_mgr: SerialManager = Depends(get_serial_mgr)):
    sent, replies = await run_action(serial_mgr, "turn_left", power)
    return {"sent": sent, "replies": replies}


@router.post("/actions/right")
async def action_right(power: int = Query(160, ge=0, le=255), serial_mgr: SerialManager = Depends(get_serial_mgr)):
    sent, replies = await run_action(serial_mgr, "turn_right", power)
    return {"sent": sent, "replies": replies}
