"""
Функции для работы с энкодерами через Serial
"""
import asyncio
import re
from typing import Optional, Dict, Any, Callable


async def get_encoder(serial_mgr, encoder_id: int) -> Dict[str, Any]:
    """
    Получить значение конкретного энкодера
    
    Args:
        serial_mgr: менеджер последовательного порта
        encoder_id: ID энкодера (1-4)
    
    Returns:
        словарь с позицией и скоростью
    """
    response = await serial_mgr.send_cmd(f"GetEncoder {encoder_id}")
    
    try:
        parts = response.split()
        if len(parts) >= 5 and parts[0] == "OK" and parts[1] == "ENCODER":
            pos = int(parts[4])
            return {"pos": pos, "speed": None}
    except (ValueError, IndexError) as e:
        print(f"Error parsing encoder response: {e}")
    
    return {"pos": 0, "speed": 0.0}


async def get_all_encoders(serial_mgr) -> Dict[str, Any]:
    """
    Получить значения всех энкодеров
    
    Args:
        serial_mgr: менеджер последовательного порта
    
    Returns:
        словарь с данными всех энкодеров
    """
    response = await serial_mgr.send_cmd("GetAllEncoders")
    
    encoders = {}
    pattern = r'\[(\d+):pos=(-?\d+),spd=(-?\d+\.?\d*)\]'
    matches = re.findall(pattern, response)
    
    for match in matches:
        enc_id, pos, speed = match
        encoders[f'enc{enc_id}_pos'] = int(pos)
        encoders[f'enc{enc_id}_speed'] = float(speed)
    
    return encoders


async def reset_encoder(serial_mgr, encoder_id: int) -> bool:
    """
    Сбросить значение конкретного энкодера
    
    Args:
        serial_mgr: менеджер последовательного порта
        encoder_id: ID энкодера (1-4)
    
    Returns:
        True если успешно, False если нет
    """
    response = await serial_mgr.send_cmd(f"ResetEncoder {encoder_id}")
    return response.startswith("OK ENCODER_RESET")


async def reset_all_encoders(serial_mgr) -> bool:
    """
    Сбросить значения всех энкодеров
    
    Args:
        serial_mgr: менеджер последовательного порта
    
    Returns:
        True если все успешно, False если хотя бы один не удался
    """
    results = await asyncio.gather(
        reset_encoder(serial_mgr, 1),
        reset_encoder(serial_mgr, 2),
        reset_encoder(serial_mgr, 3),
        reset_encoder(serial_mgr, 4),
        return_exceptions=True
    )
    
    return all(isinstance(r, bool) and r for r in results)


async def stream_encoders(
    serial_mgr, 
    callback: Callable,
    duration: Optional[float] = None
):
    """
    Потоковое чтение данных с энкодеров
    
    Args:
        serial_mgr: менеджер последовательного порта
        callback: функция для обработки каждого пакета данных
        duration: длительность сбора данных в секундах (None = бесконечно)
    """
    # Отправляем команду начала потока
    await serial_mgr.send_cmd("StreamEncoders", wait_response=False)
    
    # Ждем подтверждения
    response = await serial_mgr.read_line(timeout=2.0)
    if response != "OK STREAM_STARTED":
        raise Exception(f"Failed to start encoder stream: {response}")
    
    start_time = asyncio.get_event_loop().time()
    
    try:
        while True:
            # Проверяем длительность
            if duration and (asyncio.get_event_loop().time() - start_time) > duration:
                break
            
            # Читаем строку с данными
            line = await serial_mgr.read_line(timeout=1.0)
            if not line:
                continue
            
            if line.startswith("ENC:"):
                # Парсим данные
                parts = line[4:].strip().split(',')
                if len(parts) == 9:
                    data = {
                        'timestamp': int(parts[0]),
                        'enc1_pos': int(parts[1]),
                        'enc1_speed': float(parts[2]),
                        'enc2_pos': int(parts[3]),
                        'enc2_speed': float(parts[4]),
                        'enc3_pos': int(parts[5]),
                        'enc3_speed': float(parts[6]),
                        'enc4_pos': int(parts[7]),
                        'enc4_speed': float(parts[8])
                    }
                    
                    await callback(data)
                    
            elif line == "OK STREAM_STOPPED":
                break
            
    finally:
        # Останавливаем поток
        await serial_mgr.send_cmd("StopStream", wait_response=False)