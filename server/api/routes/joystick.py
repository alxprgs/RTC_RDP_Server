# main.py
import asyncio
import logging
from fastapi import FastAPI, WebSocket
from server.serial.manager import SerialManager
from server.encoders import EncoderMonitor, EncoderData
from server.schemas.joystick import JoystickIn  # добавлен импорт
from server.joystick import process_joystick    # предполагаемая функция

logger = logging.getLogger(__name__)

app = FastAPI()
serial_mgr = SerialManager(port='/dev/ttyACM0', baud=115200)
encoder_monitor = EncoderMonitor(serial_mgr)

# Список активных WebSocket-соединений
active_connections: list[WebSocket] = []

@app.on_event("startup")
async def startup():
    await serial_mgr.connect()
    # Регистрируем обработчик, который рассылает данные энкодеров всем клиентам
    async def broadcast_encoder(data: EncoderData):
        # Формируем полное сообщение со всеми энкодерами
        msg = {
            "type": "encoder",
            "timestamp": data.timestamp,
            "enc1": {"pos": data.enc1_pos, "speed": data.enc1_speed},
            "enc2": {"pos": data.enc2_pos, "speed": data.enc2_speed},
            "enc3": {"pos": data.enc3_pos, "speed": data.enc3_speed},
            "enc4": {"pos": data.enc4_pos, "speed": data.enc4_speed},
        }
        # Конкурентная отправка всем клиентам
        tasks = []
        disconnected = []
        for ws in active_connections:
            tasks.append(ws.send_json(msg))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Обрабатываем возможные ошибки и удаляем отключившихся
        for ws, result in zip(active_connections[:], results):
            if isinstance(result, Exception):
                logger.error(f"Send to {ws.client} failed: {result}")
                active_connections.remove(ws)

    encoder_monitor.add_callback(broadcast_encoder)
    await encoder_monitor.start_stream()
    logger.info("Serial connected, encoder stream started")

@app.on_event("shutdown")
async def shutdown():
    await encoder_monitor.stop_stream()
    await serial_mgr.disconnect()
    logger.info("Shutdown complete")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"New WebSocket connection from {websocket.client}")
    try:
        while True:
            # Ждём сообщение от клиента
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "joystick":
                # Преобразуем в JoystickIn и обрабатываем
                joystick_in = JoystickIn(**data)
                result = await process_joystick(serial_mgr, joystick_in)
                await websocket.send_json({"type": "joystick_out", "data": result.dict()})
            else:
                # Отправляем ошибку о неизвестном типе
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}"
                })
                logger.warning(f"Unknown message type from {websocket.client}: {msg_type}")
    except Exception as e:
        logger.error(f"WebSocket error for {websocket.client}: {e}")
    finally:
        active_connections.remove(websocket)
        logger.info(f"WebSocket connection closed from {websocket.client}")