from fastapi import FastAPI

from server.api.routes.health import router as health_router
from server.api.routes.telemetry import router as telemetry_router
from server.api.routes.ws_telemetry import router as ws_telemetry_router
from server.api.routes.motor import router as motor_router
from server.api.routes.joystick import router as joystick_router
from server.api.routes.actions import router as actions_router
from server.api.routes.servo import router as servo_router
from server.api.routes.ws_joystick import router as ws_joystick_router
from server.api.routes.safety import router as safety_router
from server.api.routes.version import router as version_router
from server.api.routes.device import router as device_router


def include_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(telemetry_router)
    app.include_router(ws_telemetry_router)
    app.include_router(safety_router)
    app.include_router(motor_router)
    app.include_router(joystick_router)
    app.include_router(actions_router)
    app.include_router(servo_router)
    app.include_router(ws_joystick_router)
    app.include_router(version_router)
    app.include_router(device_router)
