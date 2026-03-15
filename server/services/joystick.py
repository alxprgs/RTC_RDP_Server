from __future__ import annotations
import asyncio
from server.serial.manager import SerialManager
from server.schemas.joystick import JoystickIn, JoystickOut
from server.utils.math_mix import deadzone, mix_tank


async def process_joystick(serial_mgr: SerialManager, data: JoystickIn) -> JoystickOut:
    x = deadzone(data.x, data.deadzone)
    y = deadzone(data.y, data.deadzone)

    x = int(round(x * data.scale))
    y = int(round(y * data.scale))

    if abs(data.x) > abs(data.y):
        a, b = mix_tank(x, y)
        c, d = 0, 0
        lines = [
            f"SetAEngine {a}",
            f"SetBEngine {b}",
            f"SetCEngine 0",
            f"SetDEngine 0"
        ]
    else:  # data.x <= data.y
        c, d = mix_tank(-x, y)
        a, b = 0, 0
        lines = [
            f"SetAEngine 0",
            f"SetBEngine 0",
            f"SetCEngine {c}",
            f"SetDEngine {d}"
        ]

    replies = await serial_mgr.send_cmds(lines, max_wait_s_each=2.5)

    return JoystickOut(
        input={
            "x": data.x,
            "y": data.y,
            "deadzone": data.deadzone,
            "scale": data.scale,
            "motor_pair": data.motor_pair
        },
        motor_a=a,
        motor_b=b,
        motor_c=c,
        motor_d=d,
        sent=lines,
        replies=replies,
    )


# Новая функция для потокового чтения энкодеров
async def stream_encoders(serial_mgr: SerialManager, callback=None, duration=None):
    """
    Потоковое чтение данных с энкодеров
    
    Args:
        serial_mgr: SerialManager instance
        callback: функция для обработки каждого пакета данных
        duration: длительность сбора данных в секундах (None = бесконечно)
    
    Returns:
        list: список собранных данных (если callback не указан)
    """
    # Отправляем команду начала потока
    await serial_mgr.send_cmd("StreamEncoders", wait_response=False)
    
    # Ждем подтверждения
    response = await serial_mgr.read_line(timeout=2.0)
    if response != "OK STREAM_STARTED":
        raise Exception(f"Failed to start encoder stream: {response}")
    
    collected_data = []
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
                    
                    if callback:
                        await callback(data)
                    else:
                        collected_data.append(data)
                        
            elif line == "OK STREAM_STOPPED":
                break
                
    finally:
        # Останавливаем поток
        await serial_mgr.send_cmd("StopStream", wait_response=False)
    
    return collected_data if not callback else None


# Функция для однократного чтения всех энкодеров
async def get_all_encoders(serial_mgr: SerialManager) -> dict:
    """
    Однократное чтение всех энкодеров через команду GetAllEncoders
    
    Returns:
        dict: словарь с данными энкодеров
    """
    response = await serial_mgr.send_cmd("GetAllEncoders")
    
    # Парсим ответ формата: "OK ENCODERS [1:pos=123,spd=12.5][2:pos=456,spd=-5.3]..."
    import re
    
    encoders = {}
    pattern = r'\[(\d+):pos=(-?\d+),spd=(-?\d+\.?\d*)\]'
    matches = re.findall(pattern, response)
    
    for match in matches:
        enc_id, pos, speed = match
        encoders[f'enc{enc_id}_pos'] = int(pos)
        encoders[f'enc{enc_id}_speed'] = float(speed)
    
    return encoders


# Функция для чтения конкретного энкодера
async def get_encoder(serial_mgr: SerialManager, encoder_id: int) -> dict:
    """
    Чтение конкретного энкодера
    
    Args:
        encoder_id: номер энкодера (1-4)
    
    Returns:
        dict: {'pos': позиция, 'speed': скорость}
    """
    response = await serial_mgr.send_cmd(f"GetEncoder {encoder_id}")
    # Формат ответа: "OK ENCODER id=1 pos=123"
    
    parts = response.split()
    if len(parts) >= 5:
        return {
            'pos': int(parts[4]),
            'speed': None  # скорость не возвращается этой командой
        }
    return {}


# Функция для сброса энкодера
async def reset_encoder(serial_mgr: SerialManager, encoder_id: int) -> bool:
    """
    Сброс конкретного энкодера в 0
    
    Args:
        encoder_id: номер энкодера (1-4)
    
    Returns:
        bool: успешность операции
    """
    response = await serial_mgr.send_cmd(f"ResetEncoder {encoder_id}")
    return response.startswith("OK ENCODER_RESET")


# Функция для сброса всех энкодеров
async def reset_all_encoders(serial_mgr: SerialManager) -> bool:
    """
    Сброс всех энкодеров в 0
    """
    results = await asyncio.gather(
        reset_encoder(serial_mgr, 1),
        reset_encoder(serial_mgr, 2),
        reset_encoder(serial_mgr, 3),
        reset_encoder(serial_mgr, 4)
    )
    return all(results)


# Пример использования в основном коде:
async def main_example():
    # Создаем SerialManager (предполагается, что он уже настроен)
    serial_mgr = SerialManager(port='/dev/ttyACM0', baud=115200)
    await serial_mgr.connect()
    
    # Пример 1: Однократное чтение всех энкодеров
    encoders = await get_all_encoders(serial_mgr)
    print(f"Encoders: {encoders}")
    
    # Пример 2: Потоковое чтение с callback
    async def process_encoder_data(data):
        print(f"ENC1: pos={data['enc1_pos']} speed={data['enc1_speed']:.2f}")
    
    # Собираем данные в течение 5 секунд
    await stream_encoders(serial_mgr, callback=process_encoder_data, duration=5)
    
    # Пример 3: Сброс всех энкодеров
    success = await reset_all_encoders(serial_mgr)
    print(f"Reset all encoders: {'OK' if success else 'FAILED'}")
    
    # Пример 4: Использование с джойстиком
    # Здесь можно комбинировать управление моторами и чтение энкодеров
    joystick_data = JoystickIn(x=100, y=50, deadzone=10, scale=1.0, motor_pair="AB")
    
    # Запускаем поток энкодеров в фоне
    encoder_task = asyncio.create_task(
        stream_encoders(serial_mgr, callback=process_encoder_data)
    )
    
    # Отправляем команды джойстика
    result = await process_joystick(serial_mgr, joystick_data)
    print(f"Joystick result: {result}")
    
    # Ждем немного и останавливаем поток энкодеров
    await asyncio.sleep(2)
    encoder_task.cancel()
    
    await serial_mgr.disconnect()