from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _to_int_keys(d: Any) -> dict[int, Any]:
    if not d:
        return {}
    if not isinstance(d, dict):
        raise ValueError("Expected dict/JSON object")
    out: dict[int, Any] = {}
    for k, v in d.items():
        try:
            ik = int(k)
        except Exception as e:
            raise ValueError(f"Bad key in dict: {k!r}") from e
        out[ik] = v
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Серийный порт
    arduino_baud: int = 115200
    ws_ping_interval: float = 5.0
    ws_ping_timeout: float = 15.0
    ws_max_rate_hz: float = 30.0
    ws_stop_on_close: bool = True
    stream_interval: float = 1.0

    # --- Серво-расширение
    servo_count: int = 5

    servo_default_min_deg: int = 0
    servo_default_max_deg: int = 180
    servo_center_deg: int = 90

    # JSON в .env:
    # SERVO_LIMITS={"1":[10,170],"2":[0,140]}
    servo_limits: Dict[int, Tuple[int, int]] = Field(default_factory=dict)

    # JSON в .env:
    # SERVO_SAFE_POSE={"1":90,"2":90,"3":20,"4":160,"5":90}
    servo_safe_pose: Dict[int, int] = Field(default_factory=dict)

    # Ограничение скорости (градусов/сек). 0 = выключено.
    servo_slew_rate_dps: float = 0.0

    # Лимит частоты команд на 1 серво. 0 = выключено.
    servo_max_cmd_hz: float = 25.0

    # Поведение при превышении частоты:
    # "reject" -> HTTP 429, "sleep" -> подождать нужное время
    servo_rate_limit_mode: str = "reject"

    # --- Безопасность / E-STOP (серверный)
    estop_enabled: bool = True
    watchdog_enabled: bool = True

    # как часто проверять (сек)
    watchdog_tick_s: float = 0.20

    # если нет мотор-команд > N сек -> стоп моторов (0 = выключено)
    watchdog_motor_idle_s: float = 1.50

    # если нет серво-команд > N сек -> безопасная поза (0 = выключено)
    watchdog_servo_idle_s: float = 6.0

    # что делать с сервами на простое
    watchdog_servo_safe_enabled: bool = False  # по умолчанию выключено, чтобы не было сюрпризов

    # --- Проверка обновлений (GitHub)
    update_check_enabled: bool = True
    update_check_interval_s: int = 6 * 60 * 60  # раз в 6 часов
    update_check_timeout_s: float = 3.0
    github_repo: str = "alxprgs/RTC_RDP_Server"  # владелец/репозиторий
    github_branch: str = "main"
    github_token: str | None = None  # если надо обойти лимит запросов

    # --- Проверка устройства (Arduino caps/version)
    device_probe_on_startup: bool = True
    device_probe_timeout_s: float = 2.5

    log_level: Optional[str] = None
    log_profile: Optional[str] = None

    connection_type: Optional[str] = None

    @field_validator("servo_count")
    @classmethod
    def _v_servo_count(cls: type["Settings"], v: int) -> int:
        if v < 1 or v > 16:
            raise ValueError("servo_count must be 1..16")
        return v

    @field_validator("servo_default_min_deg", "servo_default_max_deg")
    @classmethod
    def _v_servo_range(cls: type["Settings"], v: int) -> int:
        if v < 0 or v > 180:
            raise ValueError("servo default deg must be 0..180")
        return v

    @field_validator("servo_limits", mode="before")
    @classmethod
    def _v_servo_limits(cls: type["Settings"], v: Any) -> Dict[int, Tuple[int, int]]:
        d = _to_int_keys(v)
        out: Dict[int, Tuple[int, int]] = {}
        for sid, pair in d.items():
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"servo_limits[{sid}] must be [min,max]")
            lo = int(pair[0])
            hi = int(pair[1])
            out[int(sid)] = (lo, hi)
        return out

    @field_validator("servo_safe_pose", mode="before")
    @classmethod
    def _v_servo_safe_pose(cls: type["Settings"], v: Any) -> Dict[int, int]:
        d = _to_int_keys(v)
        out: Dict[int, int] = {}
        for sid, deg in d.items():
            out[int(sid)] = int(deg)
        return out

    @field_validator("servo_slew_rate_dps")
    @classmethod
    def _v_slew(cls: type["Settings"], v: float) -> float:
        if v < 0:
            raise ValueError("servo_slew_rate_dps must be >= 0")
        return float(v)

    @field_validator("servo_max_cmd_hz")
    @classmethod
    def _v_hz(cls: type["Settings"], v: float) -> float:
        if v < 0:
            raise ValueError("servo_max_cmd_hz must be >= 0")
        return float(v)

    @field_validator("servo_rate_limit_mode")
    @classmethod
    def _v_rl_mode(cls: type["Settings"], v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("reject", "sleep"):
            raise ValueError("servo_rate_limit_mode must be reject|sleep")
        return v

    @field_validator("watchdog_tick_s", "watchdog_motor_idle_s", "watchdog_servo_idle_s")
    @classmethod
    def _v_watchdog_times(cls: type["Settings"], v: float) -> float:
        if v < 0:
            raise ValueError("watchdog times must be >= 0")
        return float(v)

    @field_validator("connection_type", mode="before")
    @classmethod
    def validate_connection_type(
        cls: type["Settings"],
        v: Any,
    ) -> Optional[str]:
        if v not in ["serial", "uart", None]:
            raise ValueError("connection_type must be 'serial', 'uart', or None.")
        return v
