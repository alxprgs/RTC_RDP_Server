from __future__ import annotations

import asyncio
import time
import uuid
from typing import Iterable, Optional

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect

from server.core.config import Settings
from server.core.context import REQUEST_ID
from server.schemas.joystick import JoystickIn
from server.serial.manager import SerialManager
from server.services.joystick import process_joystick


router = APIRouter(tags=["ws"])


def _supported_cmds_from_app(app: FastAPI) -> set[str] | None:
    info = getattr(app.state, "device_info", None) or {}
    cmds = info.get("supported_commands")
    if not cmds:
        caps = info.get("caps") or {}
        cmds = caps.get("commands") or caps.get("supported_commands")
    if not cmds:
        return None
    return {str(c).strip().lower() for c in cmds if str(c).strip()}


def _ws_require(app: FastAPI, required: Iterable[str]) -> bool:
    cmds = _supported_cmds_from_app(app)
    if cmds is None:
        return True  # старые прошивки не блокируем
    return all(r.strip().lower() in cmds for r in required)


def _get_app_and_settings(ws: WebSocket) -> tuple[FastAPI, Settings]:
    app = getattr(ws, "app", None) or ws.scope.get("app")
    if app is None:
        raise RuntimeError("WebSocket app is not available in scope")
    settings = getattr(app.state, "settings", None)
    if settings is None:
        raise RuntimeError("app.state.settings is not initialized")
    return app, settings


def _get_serial_mgr(app: FastAPI) -> SerialManager | None:
    mgr = getattr(app.state, "serial_mgr", None)
    if mgr is None:
        return None
    return mgr


@router.websocket("/ws/joystick")
async def ws_joystick(websocket: WebSocket) -> None:
    app, settings = _get_app_and_settings(websocket)
    log = getattr(app.state, "log", None)  # опционально если ты логгер кладёшь в state
    if log is None:
        import logging

        log = logging.getLogger("motor-bridge.ws")

    rid = websocket.headers.get("x-request-id") or str(uuid.uuid4())
    token = REQUEST_ID.set(rid)

    client_host = getattr(websocket.client, "host", "-")
    client_port = getattr(websocket.client, "port", "-")

    ws_ping_interval = float(getattr(settings, "ws_ping_interval", 5.0))
    ws_ping_timeout = float(getattr(settings, "ws_ping_timeout", 15.0))
    ws_max_rate_hz = float(getattr(settings, "ws_max_rate_hz", 30.0))
    ws_stop_on_close = bool(getattr(settings, "ws_stop_on_close", True))

    if not _ws_require(app, ("SetAEngine", "SetBEngine")):
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "error",
                "detail": "firmware_unsupported",
                "missing": ["SetAEngine", "SetBEngine"],
                "status": 501,
            }
        )
        await websocket.close(code=1008)
        return

    def is_estopped() -> bool:
        return bool(getattr(app.state, "estop", False))

    async def safe_stop(reason: str) -> None:
        """
        ВАЖНО: это единственные мотор-команды, которые мы допускаем даже при E-STOP,
        потому что они "стоп". (Если хочешь совсем ноль команд при E-STOP — скажи.)
        """
        if not ws_stop_on_close:
            return
        mgr = _get_serial_mgr(app)
        if mgr is None:
            return
        try:
            log.info("WS SAFE STOP (%s) | rid=%s", reason, rid)
            await mgr.send_cmds(["SetAEngine 0", "SetBEngine 0"], max_wait_s_each=2.0)
        except Exception as e:
            log.warning("WS SAFE STOP FAILED (%s) | rid=%s | err=%s", reason, rid, repr(e))

    await websocket.accept()
    log.info("↔ WS CONNECT /ws/joystick | from=%s:%s | rid=%s", client_host, client_port, rid)

    last_client_msg = time.monotonic()

    latest: Optional[JoystickIn] = None
    latest_seq = 0
    sent_seq = 0
    latest_lock = asyncio.Lock()
    new_data_event = asyncio.Event()

    # Чтобы не спамить стоп на каждом кадре при активном E-STOP:
    estop_stop_sent = False

    async def receiver_loop() -> None:
        nonlocal last_client_msg, latest, latest_seq
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception:
                raise WebSocketDisconnect(code=1001)

            last_client_msg = time.monotonic()

            # пинг/понг служебные
            if isinstance(msg, dict) and msg.get("type") in ("pong", "ping"):
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "t": time.time()})
                continue

            # джойстик
            try:
                data = JoystickIn(**msg)
            except Exception as e:
                await websocket.send_json({"type": "error", "detail": f"bad payload: {e}"})
                continue

            async with latest_lock:
                latest = data
                latest_seq += 1
                new_data_event.set()

    async def sender_loop() -> None:
        nonlocal sent_seq, estop_stop_sent
        min_interval = 1.0 / max(1.0, ws_max_rate_hz)
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

                # --- КЛЮЧЕВОЕ: E-STOP блокирует управление моторами ---
                if is_estopped():
                    if not estop_stop_sent:
                        estop_stop_sent = True
                        await safe_stop("estop_active")

                    sent_seq = target_seq
                    last_send = time.monotonic()

                    # отвечаем клиенту на каждый кадр
                    try:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "detail": "estop",
                                "status": 423,
                                "seq": sent_seq,
                                "t": time.time(),
                            }
                        )
                    except Exception:
                        return
                    continue

                # если E-STOP сняли — снова разрешаем (и сбрасываем флаг стопа)
                if estop_stop_sent and not is_estopped():
                    estop_stop_sent = False

                # обычный режим: обрабатываем и шлём мотор-команды через process_joystick()
                try:
                    mgr = _get_serial_mgr(app)
                    if mgr is None:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "detail": "serial_unavailable",
                                "status": 503,
                            }
                        )
                        return
                    out = await process_joystick(mgr, data)
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
                except Exception:
                    # Важно: если в process_joystick есть HTTPException — можешь здесь точнее распаковать
                    try:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "detail": "internal_error",
                                "status": 500,
                            }
                        )
                    except Exception:
                        return

    async def ping_loop() -> None:
        nonlocal last_client_msg
        while True:
            await asyncio.sleep(ws_ping_interval)
            idle = time.monotonic() - last_client_msg
            if idle > ws_ping_timeout:
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

    # приветствие
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "rid": rid,
                "ping_interval": ws_ping_interval,
                "ping_timeout": ws_ping_timeout,
                "max_rate_hz": ws_max_rate_hz,
                "estop": is_estopped(),
            }
        )
        # если уже активен E-STOP — сразу уведомим
        if is_estopped():
            await websocket.send_json(
                {
                    "type": "error",
                    "detail": "estop",
                    "status": 423,
                    "t": time.time(),
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

        # при закрытии — стоп (если включено)
        await safe_stop("disconnect/timeout/error")

        REQUEST_ID.reset(token)
        log.info("↔ WS CLOSED | rid=%s", rid)
