from fastapi import APIRouter

from .health import router as health_router
from .telemetry import router as telemetry_router
from .motor import router as motor_router
from .joystick import router as joystick_router
from .servo import router as servo_router
from .actions import router as actions_router
from .ws_telemetry import router as ws_telemetry_router
from .ws_joystick import router as ws_joystick_router

router = APIRouter()
router.include_router(health_router)
router.include_router(telemetry_router)
router.include_router(ws_telemetry_router)
router.include_router(motor_router)
router.include_router(joystick_router)
router.include_router(servo_router)
router.include_router(actions_router)
router.include_router(ws_joystick_router)
