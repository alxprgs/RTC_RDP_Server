from pydantic import BaseModel, Field


class ActionIn(BaseModel):
    action: str
    power: int = Field(default=160, ge=0, le=255, description="Сила действия (0..255)")
    duration_ms: int = Field(default=0, ge=0, le=10_000, description="Сколько держать, 0 = без таймера")


class ActionOut(BaseModel):
    action: str
    sent: list[str]
    replies: list[str]
