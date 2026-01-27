from typing import Literal
from pydantic import BaseModel, Field

CmdName = Literal["SetAEngine", "SetBEngine", "SetAllEngine"]


class MotorCommandIn(BaseModel):
    cmd: CmdName
    speed: int = Field(ge=-255, le=255)


class MotorCommandOut(BaseModel):
    sent: str
    reply: str
