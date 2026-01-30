from fastapi import FastAPI

from server.core.config import Settings
from server.core.context import REQUEST_ID
from server.lifespan import build_lifespan
from server.core.logging_runtime import setup_base_logging
from server.api.routes import include_routers
from server.core.logging_runtime import request_logging_middleware
from server.services.servo import ServoRuntimeState


def create_app() -> FastAPI:
    settings = Settings()

    setup_base_logging(settings)

    app = FastAPI(
        title="Arduino Motor Bridge",
        lifespan=build_lifespan(settings),
    )

    app.state.settings = settings
    app.state.estop = False
    app.state.servo_state = ServoRuntimeState()

    app.middleware("http")(request_logging_middleware)

    include_routers(app)

    return app
