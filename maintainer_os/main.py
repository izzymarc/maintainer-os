from fastapi import FastAPI
from pydantic import BaseModel

from . import __version__
from .config import settings


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


app = FastAPI(title=settings.app_name, version=__version__)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )


@app.get(f"{settings.api_prefix}/capabilities", tags=["system"])
async def capabilities() -> dict[str, list[str]]:
    return {
        "available": ["health"],
        "planned": ["repositories", "webhooks", "review-runs", "audit-events"],
    }
