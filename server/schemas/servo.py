from typing import Literal
from pydantic import BaseModel, Field

ServoCmdName = Literal["SetServoA", "SetServoB", "SetServoAll"]
ServoPowerMode = Literal["ARDUINO", "EXTERNAL"]


class ServoCommandIn(BaseModel):
    cmd: ServoCmdName
    deg: int = Field(ge=0, le=180, description="Позиция сервопривода (0..180)")


class ServoCommandOut(BaseModel):
    sent: str
    reply: str


class ServoPowerIn(BaseModel):
    mode: ServoPowerMode


class ServoPowerOut(BaseModel):
    mode: ServoPowerMode
    sent: str
    reply: str
