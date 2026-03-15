from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from server.api.deps import ensure_supported_command, get_serial_mgr, is_dev_mode
from server.schemas.encoders import (
    EncoderData,
    EncodersResponse,
    ResetEncodersResponse,
    StreamConfig,
)
from server.serial.manager import SerialManager
from server.services.encoders import (
    get_all_encoders as svc_get_all_encoders,
    get_encoder as svc_get_encoder,
    reset_all_encoders as svc_reset_all_encoders,
    reset_encoder as svc_reset_encoder,
    stream_encoders as svc_stream_encoders,
)

router = APIRouter(prefix="/encoders", tags=["encoders"])


def _dev_encoder_payload() -> dict[str, float | int]:
    return {
        "enc1_pos": 0,
        "enc1_speed": 0.0,
        "enc2_pos": 0,
        "enc2_speed": 0.0,
        "enc3_pos": 0,
        "enc3_speed": 0.0,
        "enc4_pos": 0,
        "enc4_speed": 0.0,
    }


def _ws_supported(websocket: WebSocket, required: tuple[str, ...]) -> bool:
    info = getattr(websocket.app.state, "device_info", None) or {}
    cmds = info.get("supported_commands")
    if not cmds:
        caps = info.get("caps") or {}
        cmds = caps.get("commands") or caps.get("supported_commands")

    if not cmds:
        return True

    supported = {str(cmd).strip().lower() for cmd in cmds if str(cmd).strip()}
    return all(cmd.strip().lower() in supported for cmd in required)


@router.get("/all", response_model=EncodersResponse)
async def get_all_encoders(
    request: Request,
    serial_mgr: SerialManager | None = Depends(get_serial_mgr),
) -> EncodersResponse:
    if is_dev_mode(request):
        data = _dev_encoder_payload()
    else:
        ensure_supported_command(request, ("GetAllEncoders",))
        if serial_mgr is None:
            raise HTTPException(status_code=503, detail="Serial not initialized yet")
        data = await svc_get_all_encoders(serial_mgr)

    return EncodersResponse(
        success=True,
        data=data,
        timestamp=time.time(),
    )


@router.get("/{encoder_id}", response_model=EncoderData)
async def get_encoder(
    encoder_id: int,
    request: Request,
    serial_mgr: SerialManager | None = Depends(get_serial_mgr),
) -> EncoderData:
    if encoder_id < 1 or encoder_id > 4:
        raise HTTPException(status_code=400, detail="Encoder ID must be between 1 and 4")

    if is_dev_mode(request):
        return EncoderData(id=encoder_id, position=0, speed=0.0)

    ensure_supported_command(request, ("GetAllEncoders",))

    if serial_mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")

    data = await svc_get_encoder(serial_mgr, encoder_id)
    return EncoderData(
        id=encoder_id,
        position=data["pos"],
        speed=data["speed"],
    )


@router.post("/reset/{encoder_id}", response_model=ResetEncodersResponse)
async def reset_encoder(
    encoder_id: int,
    request: Request,
    serial_mgr: SerialManager | None = Depends(get_serial_mgr),
) -> ResetEncodersResponse:
    if encoder_id < 1 or encoder_id > 4:
        raise HTTPException(status_code=400, detail="Encoder ID must be between 1 and 4")

    if is_dev_mode(request):
        return ResetEncodersResponse(
            success=True,
            message=f"Encoder {encoder_id} reset successful (dev mode)",
        )

    ensure_supported_command(request, ("ResetEncoder",))

    if serial_mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")

    success = await svc_reset_encoder(serial_mgr, encoder_id)
    return ResetEncodersResponse(
        success=success,
        message=f"Encoder {encoder_id} reset {'successful' if success else 'failed'}",
    )


@router.post("/reset-all", response_model=ResetEncodersResponse)
async def reset_all_encoders(
    request: Request,
    serial_mgr: SerialManager | None = Depends(get_serial_mgr),
) -> ResetEncodersResponse:
    if is_dev_mode(request):
        return ResetEncodersResponse(
            success=True,
            message="All encoders reset successful (dev mode)",
        )

    ensure_supported_command(request, ("ResetEncoder",))

    if serial_mgr is None:
        raise HTTPException(status_code=503, detail="Serial not initialized yet")

    success = await svc_reset_all_encoders(serial_mgr)
    return ResetEncodersResponse(
        success=success,
        message=f"All encoders reset {'successful' if success else 'failed'}",
    )


@router.websocket("/stream")
async def websocket_encoder_stream(websocket: WebSocket) -> None:
    await websocket.accept()

    serial_mgr = getattr(websocket.app.state, "serial_mgr", None)
    device_info = getattr(websocket.app.state, "device_info", None) or {}
    dev_mode = device_info.get("mode") == "dev"

    if not dev_mode:
        if serial_mgr is None:
            await websocket.send_json({
                "type": "error",
                "message": "Serial connection not available",
            })
            await websocket.close()
            return

        if not _ws_supported(websocket, ("GetAllEncoders",)):
            await websocket.send_json({
                "type": "error",
                "message": "Firmware does not support GetAllEncoders",
            })
            await websocket.close()
            return

    try:
        config_payload = await websocket.receive_json()
        config = StreamConfig(**config_payload)

        interval_ms = config.interval_ms or 50
        duration = config.duration if (config.duration or 0) > 0 else None

        async def send_encoder_packet(packet: dict) -> None:
            await websocket.send_json({
                "type": "encoder_data",
                "timestamp": packet["timestamp"],
                "encoders": [
                    {
                        "id": 1,
                        "position": packet["enc1_pos"],
                        "speed": packet["enc1_speed"] if config.include_speed else 0.0,
                    },
                    {
                        "id": 2,
                        "position": packet["enc2_pos"],
                        "speed": packet["enc2_speed"] if config.include_speed else 0.0,
                    },
                    {
                        "id": 3,
                        "position": packet["enc3_pos"],
                        "speed": packet["enc3_speed"] if config.include_speed else 0.0,
                    },
                    {
                        "id": 4,
                        "position": packet["enc4_pos"],
                        "speed": packet["enc4_speed"] if config.include_speed else 0.0,
                    },
                ],
            })

        if dev_mode:
            started_at = time.monotonic()
            while True:
                if duration is not None and time.monotonic() - started_at >= duration:
                    break

                await send_encoder_packet({
                    "timestamp": int(time.time() * 1000),
                    **_dev_encoder_payload(),
                })
                await asyncio.sleep(max(10, interval_ms) / 1000.0)
        else:
            await svc_stream_encoders(
                serial_mgr=serial_mgr,
                callback=send_encoder_packet,
                duration=duration,
                interval_ms=interval_ms,
            )

        await websocket.send_json({"type": "stream_complete"})

    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/stats")
async def get_encoder_stats(
    request: Request,
    serial_mgr: SerialManager | None = Depends(get_serial_mgr),
) -> dict:
    if is_dev_mode(request):
        data = _dev_encoder_payload()
    else:
        ensure_supported_command(request, ("GetAllEncoders",))
        if serial_mgr is None:
            raise HTTPException(status_code=503, detail="Serial not initialized yet")
        data = await svc_get_all_encoders(serial_mgr)

    stats = {"encoders": {}}
    for i in range(1, 5):
        stats["encoders"][f"encoder_{i}"] = {
            "position": data.get(f"enc{i}_pos", 0),
            "speed": data.get(f"enc{i}_speed", 0.0),
            "active": True,
        }

    return stats