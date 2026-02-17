from __future__ import annotations

from server.serial.manager import SerialManager
from server.schemas.joystick import JoystickIn, JoystickOut
from server.utils.math_mix import deadzone, mix_tank


async def process_joystick(serial_mgr: SerialManager, data: JoystickIn) -> JoystickOut:
    x = deadzone(data.x, data.deadzone)
    y = deadzone(data.y, data.deadzone)

    x = int(round(x * data.scale))
    y = int(round(y * data.scale))

    if data.z > data.y:
        a, b = mix_tank(x, y)
        c, d = 0, 0
        lines = [
            f"SetAEngine {a}",
            f"SetBEngine {b}",
            f"SetCEngine 0",
            f"SetDEngine 0"
        ]
    else:  # data.z <= data.y
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
