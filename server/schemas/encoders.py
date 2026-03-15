from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class EncoderData(BaseModel):
    """Данные одного энкодера"""
    id: int
    position: int
    speed: float
    
    class Config:
        schema_extra = {
            "example": {
                "id": 1,
                "position": 12345,
                "speed": 12.5
            }
        }

class EncodersResponse(BaseModel):
    """Ответ с данными всех энкодеров"""
    success: bool
    data: Dict[str, Any]
    timestamp: float
    
    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "data": {
                    "enc1_pos": 12345,
                    "enc1_speed": 12.5,
                    "enc2_pos": 67890,
                    "enc2_speed": -5.3
                },
                "timestamp": 1234567890.123
            }
        }

class StreamConfig(BaseModel):
    """Конфигурация для потоковой передачи"""
    interval_ms: Optional[int] = 50  # интервал между измерениями в мс
    duration: Optional[float] = 0    # длительность в секундах (0 = бесконечно)
    include_speed: Optional[bool] = True
    
    class Config:
        schema_extra = {
            "example": {
                "interval_ms": 50,
                "duration": 10.0,
                "include_speed": True
            }
        }

class ResetEncodersResponse(BaseModel):
    """Ответ на запрос сброса энкодеров"""
    success: bool
    message: str
    
    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "message": "All encoders reset successful"
            }
        }

class EncoderStreamData(BaseModel):
    """Данные для WebSocket потока"""
    type: str
    timestamp: int
    encoders: List[EncoderData]
    
    class Config:
        schema_extra = {
            "example": {
                "type": "encoder_data",
                "timestamp": 1234567890,
                "encoders": [
                    {"id": 1, "position": 12345, "speed": 12.5},
                    {"id": 2, "position": 67890, "speed": -5.3}
                ]
            }
        }