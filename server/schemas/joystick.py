from pydantic import BaseModel, Field
from typing import Literal


class JoystickIn(BaseModel):
    x: int = Field(ge=-255, le=255, description="Turn: left(-) .. right(+)")
    y: int = Field(ge=-255, le=255, description="Throttle: back(-) .. forward(+)")
    deadzone: int = Field(default=20, ge=0, le=80, description="Deadzone around center")
    scale: float = Field(default=1.0, ge=0.0, le=1.0)
    motor_pair: Literal["AB", "CD"] = Field(
        default="AB", description="Motor pair to use (AB or CD)"
    )


class JoystickOut(BaseModel):
    input: JoystickIn
    motor_a: int
    motor_b: int
    motor_c: int = 0
    motor_d: int = 0
    raw_x: int = Field(default=0, description="Raw X value before processing")
    raw_y: int = Field(default=0, description="Raw Y value before processing")
    sent: list[str]
    replies: list[str]