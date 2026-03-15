from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from typing import Optional
import asyncio
from server.serial.manager import SerialManager
from server.api.schemas.encoders import (  # <-- ИСПРАВЛЕНО
    EncoderData, 
    EncodersResponse, 
    StreamConfig,
    ResetEncodersResponse
)

router = APIRouter(prefix="/encoders", tags=["encoders"])

# Хранилище активных WebSocket соединений
active_connections = {}

@router.get("/all", response_model=EncodersResponse)
async def get_all_encoders():
    """
    Получить данные со всех энкодеров одним запросом
    """
    serial_mgr = SerialManager.get_instance()
    if not serial_mgr or not serial_mgr.is_connected:
        raise HTTPException(status_code=503, detail="Serial connection not available")
    
    try:
        from server.api.routes.encoder_functions import get_all_encoders as get_encoders  # <-- ИСПРАВЛЕНО
        data = await get_encoders(serial_mgr)
        return EncodersResponse(
            success=True,
            data=data,
            timestamp=asyncio.get_event_loop().time()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{encoder_id}", response_model=EncoderData)
async def get_encoder(encoder_id: int):
    """
    Получить данные с конкретного энкодера (1-4)
    """
    if encoder_id < 1 or encoder_id > 4:
        raise HTTPException(status_code=400, detail="Encoder ID must be between 1 and 4")
    
    serial_mgr = SerialManager.get_instance()
    if not serial_mgr or not serial_mgr.is_connected:
        raise HTTPException(status_code=503, detail="Serial connection not available")
    
    try:
        from server.api.routes.encoder_functions import get_encoder as get_enc  # <-- ИСПРАВЛЕНО
        data = await get_enc(serial_mgr, encoder_id)
        return EncoderData(
            id=encoder_id,
            position=data.get('pos', 0),
            speed=data.get('speed', 0.0)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset/{encoder_id}")
async def reset_encoder(encoder_id: int):
    """
    Сбросить счетчик конкретного энкодера
    """
    if encoder_id < 1 or encoder_id > 4:
        raise HTTPException(status_code=400, detail="Encoder ID must be between 1 and 4")
    
    serial_mgr = SerialManager.get_instance()
    if not serial_mgr or not serial_mgr.is_connected:
        raise HTTPException(status_code=503, detail="Serial connection not available")
    
    try:
        from server.api.routes.encoder_functions import reset_encoder as reset_enc  # <-- ИСПРАВЛЕНО
        success = await reset_enc(serial_mgr, encoder_id)
        return ResetEncodersResponse(
            success=success,
            message=f"Encoder {encoder_id} reset {'successful' if success else 'failed'}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset-all")
async def reset_all_encoders():
    """
    Сбросить счетчики всех энкодеров
    """
    serial_mgr = SerialManager.get_instance()
    if not serial_mgr or not serial_mgr.is_connected:
        raise HTTPException(status_code=503, detail="Serial connection not available")
    
    try:
        from server.api.routes.encoder_functions import reset_all_encoders as reset_all  # <-- ИСПРАВЛЕНО
        success = await reset_all(serial_mgr)
        return ResetEncodersResponse(
            success=success,
            message=f"All encoders reset {'successful' if success else 'failed'}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/stream")
async def websocket_encoder_stream(websocket: WebSocket):
    """
    WebSocket соединение для потоковой передачи данных с энкодеров
    """
    await websocket.accept()
    client_id = id(websocket)
    active_connections[client_id] = websocket
    
    serial_mgr = SerialManager.get_instance()
    if not serial_mgr or not serial_mgr.is_connected:
        await websocket.send_json({
            "type": "error",
            "message": "Serial connection not available"
        })
        await websocket.close()
        del active_connections[client_id]
        return
    
    try:
        from server.api.routes.encoder_functions import stream_encoders  # <-- ИСПРАВЛЕНО
        
        # Получаем конфигурацию от клиента
        config_data = await websocket.receive_json()
        config = StreamConfig(**config_data)
        
        # Функция callback для отправки данных через WebSocket
        async def send_encoder_data(data):
            await websocket.send_json({
                "type": "encoder_data",
                "timestamp": data['timestamp'],
                "encoders": [
                    {"id": 1, "position": data['enc1_pos'], "speed": data['enc1_speed']},
                    {"id": 2, "position": data['enc2_pos'], "speed": data['enc2_speed']},
                    {"id": 3, "position": data['enc3_pos'], "speed": data['enc3_speed']},
                    {"id": 4, "position": data['enc4_pos'], "speed": data['enc4_speed']}
                ]
            })
        
        # Запускаем потоковое чтение
        await stream_encoders(
            serial_mgr, 
            callback=send_encoder_data,
            duration=config.duration if config.duration > 0 else None
        )
        
        await websocket.send_json({"type": "stream_complete"})
        
    except WebSocketDisconnect:
        print(f"Client {client_id} disconnected")
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": str(e)
        })
    finally:
        if client_id in active_connections:
            del active_connections[client_id]


@router.get("/stats")
async def get_encoder_stats():
    """
    Получить статистику по энкодерам
    """
    serial_mgr = SerialManager.get_instance()
    if not serial_mgr or not serial_mgr.is_connected:
        raise HTTPException(status_code=503, detail="Serial connection not available")
    
    try:
        from server.api.routes.encoder_functions import get_all_encoders  # <-- ИСПРАВЛЕНО
        data = await get_all_encoders(serial_mgr)
        
        # Вычисляем статистику
        stats = {
            "active_connections": len(active_connections),
            "encoders": {}
        }
        
        for i in range(1, 5):
            pos_key = f'enc{i}_pos'
            speed_key = f'enc{i}_speed'
            stats["encoders"][f"encoder_{i}"] = {
                "position": data.get(pos_key, 0),
                "speed": data.get(speed_key, 0.0),
                "active": True
            }
        
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))