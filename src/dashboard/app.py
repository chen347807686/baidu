from fastapi import FastAPI
from pydantic import BaseModel

from dashboard.config import load_settings


class Health(BaseModel):
    status: str = "ok"


def create_app() -> FastAPI:
    load_settings()
    app = FastAPI(title="OneTitle Dashboard V2", version="0.1.0")

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health()

    return app
