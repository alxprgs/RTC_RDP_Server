from __future__ import annotations

from server.serial.manager import SerialManager
from server.schemas.joystick import JoystickIn, JoystickOut
from server.utils.math_mix import deadzone, mix_tank


async def process_joystick(serial_mgr: SerialManager, data: JoystickIn) -> JoystickOut:
    x = deadzone(data.x, data.deadzone)
    y = deadzone(data.y, data.deadzone)

    x = int(round(x * data.scale))
    y = int(round(y * data.scale))

    a, b = mix_tank(x, y)
    lines = [f"SetAEngine {a}", f"SetBEngine {b}"]

    replies = await serial_mgr.send_cmds(lines, max_wait_s_each=2.5)

    return JoystickOut(
        input={"x": data.x, "y": data.y, "deadzone": data.deadzone, "scale": data.scale},
        motor_a=a,
        motor_b=b,
        sent=lines,
        replies=replies,
    )
