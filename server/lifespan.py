from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI

from server.core.config import Settings
from server.core.logging_runtime import ensure_logging_config_on_boot
from server.serial.ports import find_arduino_port
from server.serial.manager import SerialManager
from server.services.servo_power import ensure_servo_power_mode_on_boot


def build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 1) применяем профиль логов / интерактивный выбор
        runtime = await ensure_logging_config_on_boot(settings)
        app.state.logging_runtime = runtime

        # 2) выбираем порт Arduino
        try:
            serial_port = find_arduino_port()
        except Exception as e:
            raise RuntimeError(
                f"Не удалось авто-найти порт Arduino: {e}. "
                f"Задай ARDUINO_PORT (например COM11 или /dev/ttyACM0)."
            )

        app.state.serial_port = serial_port

        # 3) поднимаем SerialManager
        serial_mgr = SerialManager(
            port=serial_port,
            baudrate=settings.arduino_baud,
            timeout=1.0,
            logging_runtime=runtime,
        )
        app.state.serial_mgr = serial_mgr

        # 4) старт
        try:
            servo_mode = await ensure_servo_power_mode_on_boot(
                serial_mgr=serial_mgr,
                settings=settings,
            )
            app.state.servo_pwr_mode_active = servo_mode
            yield
        finally:
            try:
                serial_mgr.close()
            except Exception:
                pass

    return lifespan
