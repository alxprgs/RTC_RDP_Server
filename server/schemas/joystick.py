from pydantic import BaseModel, Field


class JoystickIn(BaseModel):
    x: int = Field(ge=-255, le=255, description="Turn: left(-) .. right(+)")
    y: int = Field(ge=-255, le=255, description="Throttle: back(-) .. forward(+)")
    deadzone: int = Field(default=20, ge=0, le=80, description="Deadzone around center")
    scale: float = Field(default=1.0, ge=0.0, le=1.0)


class JoystickOut(BaseModel):
    input: dict
    motor_a: int
    motor_b: int
    sent: list[str]
    replies: list[str]
