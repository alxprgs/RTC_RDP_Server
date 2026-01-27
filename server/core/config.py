from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Serial
    arduino_baud: int = 115200

    # Logging
    log_profile: str | None = None
    log_level: str = "INFO"
    log_request_body: bool = False
    max_body_preview: int = 800

    serial_log: bool = False
    serial_max_preview: int = 200

    # WS
    ws_ping_interval: float = 5.0
    ws_ping_timeout: float = 15.0
    ws_max_rate_hz: float = 30.0
    ws_stop_on_close: bool = True

    # Telemetry
    cpu_percent_interval: float = 0.10
    stream_interval: float = 1.0
