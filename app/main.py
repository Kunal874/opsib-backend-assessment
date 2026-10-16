"""FastAPI application factory. Routers stay thin; business logic lives in the domain packages.

Run: uvicorn --factory app.main:create_app
"""
from fastapi import FastAPI

from app.config import Settings, load_settings
from app.errors import install_error_handlers
from app.log import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="OPSiB receipt-shortfall service", version="0.1.0")
    app.state.settings = settings
    install_error_handlers(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok"}

    return app
