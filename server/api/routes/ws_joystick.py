from __future__ import annotations

import asyncio
import time
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from server.core.context import REQUEST_ID
from server.schemas.joystick import JoystickIn
from server.services.joystick import process_joystick

router = APIRouter()
log = logging.getLogger("motor-bridge")


@router.websocket("/ws/joystick")
async def ws_joystick(websocket: WebSocket):
    rid = websocket.headers.get("x-request-id") or str(uuid.uuid4())
    token = REQUEST_ID.set(rid)

    client_host = getattr(websocket.client, "host", "-")
    client_port = getattr(websocket.client, "port", "-")

    await websocket.accept()
    log.info("↔ WS CONNECT /ws/joystick | from=%s:%s | rid=%s", client_host, client_port, rid)

    settings = websocket.app.state.settings
    serial_mgr = getattr(websocket.app.state, "serial_mgr", None)

    last_client_msg = time.monotonic()

    latest: Optional[JoystickIn] = None
    latest_seq = 0
    sent_seq = 0
    latest_lock = asyncio.Lock()
    new_data_event = asyncio.Event()

    async def safe_stop(reason: str):
        if not settings.ws_stop_on_close:
            return
        try:
            if serial_mgr is None:
                return
            log.info("WS STOP (%s) | rid=%s", reason, rid)
            await serial_mgr.send_cmds(["SetAEngine 0", "SetBEngine 0"])
        except Exception as e:
            log.warning("WS STOP FAILED (%s) | rid=%s | err=%s", reason, rid, repr(e))

    async def receiver_loop():
        nonlocal last_client_msg, latest, latest_seq
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception:
                raise WebSocketDisconnect(code=1001)

            last_client_msg = time.monotonic()

            if isinstance(msg, dict) and msg.get("type") in ("pong", "ping"):
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "t": time.time()})
                continue

            try:
                data = JoystickIn(**msg)
            except Exception as e:
                await websocket.send_json({"type": "error", "detail": f"bad payload: {e}"})
                continue

            async with latest_lock:
                latest = data
                latest_seq += 1
                new_data_event.set()

    async def sender_loop():
        nonlocal sent_seq
        min_interval = 1.0 / max(1.0, float(settings.ws_max_rate_hz))
        last_send = 0.0

        while True:
            await new_data_event.wait()
            new_data_event.clear()

            while True:
                async with latest_lock:
                    if latest is None or sent_seq == latest_seq:
                        break
                    data = latest
                    target_seq = latest_seq

                now = time.monotonic()
                dt = now - last_send
                if dt < min_interval:
                    await asyncio.sleep(min_interval - dt)

                try:
                    if serial_mgr is None:
                        raise HTTPException(status_code=503, detail="Serial not initialized yet")

                    out = await process_joystick(serial_mgr, data)
                    sent_seq = target_seq
                    last_send = time.monotonic()

                    await websocket.send_json(
                        {
                            "type": "joy_ack",
                            "seq": sent_seq,
                            "motor_a": out.motor_a,
                            "motor_b": out.motor_b,
                            "sent": out.sent,
                            "replies": out.replies,
                            "t": time.time(),
                        }
                    )

                except HTTPException as e:
                    await websocket.send_json({"type": "error", "detail": e.detail, "status": e.status_code})
                except Exception as e:
                    await websocket.send_json({"type": "error", "detail": str(e)})

    async def ping_loop():
        nonlocal last_client_msg
        while True:
            await asyncio.sleep(float(settings.ws_ping_interval))
            idle = time.monotonic() - last_client_msg
            if idle > float(settings.ws_ping_timeout):
                log.warning("WS TIMEOUT idle=%.1fs | rid=%s", idle, rid)
                try:
                    await websocket.close(code=1001)
                finally:
                    return
            try:
                await websocket.send_json({"type": "ping", "t": time.time()})
            except Exception:
                return

    tasks = [
        asyncio.create_task(receiver_loop()),
        asyncio.create_task(sender_loop()),
        asyncio.create_task(ping_loop()),
    ]

    try:
        await websocket.send_json(
            {
                "type": "hello",
                "rid": rid,
                "ping_interval": settings.ws_ping_interval,
                "ping_timeout": settings.ws_ping_timeout,
                "max_rate_hz": settings.ws_max_rate_hz,
            }
        )
    except Exception:
        pass

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc:
                raise exc

    except WebSocketDisconnect:
        log.info("↔ WS DISCONNECT | rid=%s", rid)
    except Exception as e:
        log.warning("↔ WS ERROR | rid=%s | err=%s", rid, repr(e))
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await safe_stop("disconnect/timeout/error")
        REQUEST_ID.reset(token)
        log.info("↔ WS CLOSED | rid=%s", rid)
