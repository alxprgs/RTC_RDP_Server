from fastapi import FastAPI

from server.core.config import Settings
from server.core.context import REQUEST_ID
from server.lifespan import build_lifespan
from server.core.logging_runtime import setup_base_logging
from server.api.routes import router as api_router
from server.core.logging_runtime import request_logging_middleware


def create_app() -> FastAPI:
    settings = Settings()

    # базовая конфигурация логирования (формат, logger-и)
    setup_base_logging(settings)

    app = FastAPI(
        title="Arduino Motor Bridge",
        lifespan=build_lifespan(settings),
    )

    # сохраняем settings в state
    app.state.settings = settings

    # middleware логирования запросов (берёт runtime flags из app.state)
    app.middleware("http")(request_logging_middleware)

    # роуты
    app.include_router(api_router)

    return app
