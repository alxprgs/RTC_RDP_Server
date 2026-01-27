from __future__ import annotations

from typing import Literal, List
from pydantic import BaseModel, Field


ServoPowerMode = Literal["ARDUINO", "EXTERNAL"]


class ServoPowerIn(BaseModel):
    mode: ServoPowerMode


class ServoPowerOut(BaseModel):
    mode: ServoPowerMode
    sent: str
    reply: str


class ServoSetIn(BaseModel):
    deg: int = Field(ge=0, le=180, description="Позиция сервопривода (0..180)")


class ServoSetOut(BaseModel):
    id: int
    requested_deg: int
    applied_deg: int
    sent: str
    reply: str


class ServoBatchItem(BaseModel):
    id: int = Field(ge=1, le=64)
    deg: int = Field(ge=0, le=180)


class ServoBatchIn(BaseModel):
    items: List[ServoBatchItem] = Field(min_length=1, max_length=64)


class ServoBatchOut(BaseModel):
    items: List[ServoSetOut]