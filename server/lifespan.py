from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI

from server.core.config import Settings
from server.core.logging_runtime import ensure_logging_config_on_boot
from server.serial.ports import find_arduino_port, find_uart_port
from server.serial.manager import SerialManager
from server.services.servo_power import ensure_servo_power_mode_on_boot
from server.core.watchdog import start_watchdog, stop_watchdog

import asyncio
import time
from server.core.update_checker import check_github_latest, status_to_dict, should_refresh
from server.serial.device_probe import probe_device

try:
    from InquirerPy import inquirer
except ImportError:
    inquirer = None


async def choose_connection_type() -> str:
    if inquirer:
        connection_type = await inquirer.select(
            message="Выберите тип соединения",
            choices=["serial", "uart"],
            default="serial"
        ).execute_async()
        return connection_type
    else:
        return "serial"


async def get_connection_type(settings: Settings) -> str:
    """Определяет, какой тип соединения использовать: из конфигурации или через консоль."""
    if settings.connection_type:
        return settings.connection_type
    else:
        return await choose_connection_type()

async def _update_check_loop(app):
    s = app.state.settings
    while True:
        await asyncio.sleep(30)
        try:
            if not s.update_check_enabled:
                continue
            last = getattr(app.state, "update_last_checked_ts", None)
            if not should_refresh(last, s.update_check_interval_s):
                continue

            st = check_github_latest(
                repo=s.github_repo,
                branch=s.github_branch,
                timeout_s=float(s.update_check_timeout_s),
                token=s.github_token,
            )
            app.state.update_status = status_to_dict(st)
            app.state.update_last_checked_ts = time.time()
        except Exception:
            # тихо: это не критично
            pass

def build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        start_watchdog(app)
        # 1) применяем профиль логов / интерактивный выбор
        runtime = await ensure_logging_config_on_boot(settings)
        app.state.logging_runtime = runtime

        # 2) выбираем порт (Serial или UART)
        connection_type = await get_connection_type(settings)
        serial_port = None
        if connection_type == 'serial':
            try:
                serial_port = find_arduino_port()
            except Exception as e:
                raise RuntimeError(f"Не удалось найти порт Arduino по serial: {e}.")
        elif connection_type == 'uart':
            try:
                serial_port = find_uart_port()
            except Exception as e:
                raise RuntimeError(f"Не удалось найти порт по UART: {e}.")

        app.state.serial_port = serial_port

        # 3) поднимаем SerialManager
        serial_mgr = SerialManager(
            port=serial_port,
            baudrate=settings.arduino_baud,
            timeout=1.0,
            logging_runtime=runtime,
        )
        app.state.serial_mgr = serial_mgr
        app.state.device_info = None
        app.state.update_status = None
        app.state.update_last_checked_ts = None

        if app.state.settings.device_probe_on_startup:
            try:
                app.state.device_info = await probe_device(app.state.serial_mgr, timeout_s=float(app.state.settings.device_probe_timeout_s))
            except Exception:
                app.state.device_info = None

        app.state.update_task = asyncio.create_task(_update_check_loop(app))
        try:
            servo_mode = await ensure_servo_power_mode_on_boot(
                serial_mgr=serial_mgr,
                settings=settings,
            )
            app.state.servo_pwr_mode_active = servo_mode
            yield
        finally:
            t = getattr(app.state, "update_task", None)
            try:
                #1
                serial_mgr.close()
                #2
                await stop_watchdog(app)
                #3
                if t:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            except Exception:
                pass

    return lifespan