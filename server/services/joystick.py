from __future__ import annotations

import asyncio
from dataclasses import dataclass

from server.schemas.joystick import JoystickIn, JoystickOut
from server.serial.manager import SerialManager
from server.services.encoders import get_all_encoders
from server.utils.math_mix import clamp, deadzone, mix_tank

# Подбираемые коэффициенты
SYNC_KP = 0.35              # сколько PWM менять на 1 тик рассинхрона
SYNC_MAX_CORRECTION = 50    # максимальная коррекция PWM
SYNC_TICKS_DEADBAND = 2     # мелкий разброс игнорируем
SYNC_MIN_COMMAND = 40       # на очень малых скоростях не корректируем


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


@dataclass
class _PairSyncState:
    pair: str | None = None
    signature: tuple[int, int] | None = None
    ref_left: int = 0
    ref_right: int = 0


class _EncoderSyncController:
    def __init__(self) -> None:
        self.state = _PairSyncState()

    def reset(self) -> None:
        self.state = _PairSyncState()

    def apply(
        self,
        pair: str,
        left_cmd: int,
        right_cmd: int,
        left_pos: int,
        right_pos: int,
    ) -> tuple[int, int]:
        moving = left_cmd != 0 or right_cmd != 0
        signature = (_sign(left_cmd), _sign(right_cmd))

        if not moving:
            self.reset()
            return left_cmd, right_cmd

        # На слишком маленькой скорости не лезем в коррекцию
        if max(abs(left_cmd), abs(right_cmd)) < SYNC_MIN_COMMAND:
            self.state = _PairSyncState(
                pair=pair,
                signature=signature,
                ref_left=left_pos,
                ref_right=right_pos,
            )
            return left_cmd, right_cmd

        # Если сменили пару или направление движения — начинаем отсчёт заново
        if self.state.pair != pair or self.state.signature != signature:
            self.state = _PairSyncState(
                pair=pair,
                signature=signature,
                ref_left=left_pos,
                ref_right=right_pos,
            )
            return left_cmd, right_cmd

        left_dist = abs(left_pos - self.state.ref_left)
        right_dist = abs(right_pos - self.state.ref_right)
        error = left_dist - right_dist

        if abs(error) <= SYNC_TICKS_DEADBAND:
            return left_cmd, right_cmd

        correction = clamp(
            int(round(error * SYNC_KP)),
            -SYNC_MAX_CORRECTION,
            SYNC_MAX_CORRECTION,
        )

        # Если левый ушёл вперёд — уменьшаем левый и увеличиваем правый.
        # Работаем по модулю, чтобы это одинаково вело себя и на реверсе.
        left_mag = clamp(abs(left_cmd) - correction, 0, 255)
        right_mag = clamp(abs(right_cmd) + correction, 0, 255)

        return _sign(left_cmd) * left_mag, _sign(right_cmd) * right_mag


_sync_lock = asyncio.Lock()
_sync_controller = _EncoderSyncController()


def _build_motor_commands(data: JoystickIn) -> tuple[str, int, int, int, int]:
    x = deadzone(data.x, data.deadzone)
    y = deadzone(data.y, data.deadzone)

    x = int(round(x * data.scale))
    y = int(round(y * data.scale))

    if x == 0 and y == 0:
        return "STOP", 0, 0, 0, 0

    # Диагонали не поддерживаются:
    # оставляем только доминирующую ось
    if abs(x) > abs(y):
        y = 0
        a, b = mix_tank(x, y)
        return "AB", a, b, 0, 0

    x = 0
    c, d = mix_tank(-x, y)
    return "CD", 0, 0, c, d


async def process_joystick(serial_mgr: SerialManager, data: JoystickIn) -> JoystickOut:
    async with _sync_lock:
        pair, a, b, c, d = _build_motor_commands(data)

        if pair == "STOP":
            _sync_controller.reset()
        else:
            encoders = await get_all_encoders(serial_mgr)

            if pair == "AB":
                a, b = _sync_controller.apply(
                    pair="AB",
                    left_cmd=a,
                    right_cmd=b,
                    left_pos=encoders["enc1_pos"],
                    right_pos=encoders["enc2_pos"],
                )
            else:
                c, d = _sync_controller.apply(
                    pair="CD",
                    left_cmd=c,
                    right_cmd=d,
                    left_pos=encoders["enc3_pos"],
                    right_pos=encoders["enc4_pos"],
                )

        lines = [
            f"SetAEngine {a}",
            f"SetBEngine {b}",
            f"SetCEngine {c}",
            f"SetDEngine {d}",
        ]
        replies = await serial_mgr.send_cmds(lines, max_wait_s_each=2.5)

        return JoystickOut(
            input=data,
            motor_a=a,
            motor_b=b,
            motor_c=c,
            motor_d=d,
            raw_x=data.x,
            raw_y=data.y,
            sent=lines,
            replies=replies,
        )