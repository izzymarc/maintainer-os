from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from . import __version__
from .config import settings
from .domain import (
    AuditEvent,
    InMemoryStore,
    Repository,
    RepositoryCreate,
    WorkflowRun,
    WorkflowRunCreate,
)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


app = FastAPI(title=settings.app_name, version=__version__)
store = InMemoryStore()


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
        "available": ["health", "repositories", "workflow-runs"],
        "planned": ["webhooks", "audit-events", "persistent-storage"],
    }


@app.post(
    f"{settings.api_prefix}/repositories",
    response_model=Repository,
    status_code=status.HTTP_201_CREATED,
    tags=["repositories"],
)
async def create_repository(payload: RepositoryCreate) -> Repository:
    return store.add_repository(payload, correlation_id=uuid4())


@app.get(
    f"{settings.api_prefix}/repositories",
    response_model=list[Repository],
    tags=["repositories"],
)
async def list_repositories() -> list[Repository]:
    return list(store.repositories.values())


@app.post(
    f"{settings.api_prefix}/workflow-runs",
    response_model=WorkflowRun,
    status_code=status.HTTP_201_CREATED,
    tags=["workflow-runs"],
)
async def create_workflow_run(payload: WorkflowRunCreate) -> WorkflowRun:
    run = store.add_run(payload, correlation_id=uuid4())
    if run is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return run


@app.get(
    f"{settings.api_prefix}/workflow-runs/{{run_id}}",
    response_model=WorkflowRun,
    tags=["workflow-runs"],
)
async def get_workflow_run(run_id: UUID) -> WorkflowRun:
    run = store.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return run


@app.get(
    f"{settings.api_prefix}/audit-events",
    response_model=list[AuditEvent],
    tags=["audit"],
)
async def list_audit_events() -> list[AuditEvent]:
    return list(store.audit_events)
