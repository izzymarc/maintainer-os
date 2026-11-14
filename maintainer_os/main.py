import hashlib
import hmac
from pathlib import Path

from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from . import __version__
from .config import settings
from .runner import LocalWorker, WorkerError
from .domain import (
    AuditEvent,
    InMemoryStore,
    SQLiteStore,
    Repository,
    PolicyDecision,
    RepositoryCreate,
    WorkerClaim,
    WorkerLease,
    WorkflowApproval,
    WorkflowRun,
    WorkflowRunCreate,
    WorkerResult,
)


class WorkerExecutionRequest(BaseModel):
    workspace_path: str
    worker_id: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


app = FastAPI(title=settings.app_name, version=__version__)
store = SQLiteStore(settings.sqlite_path) if settings.storage == "sqlite" else InMemoryStore()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        raw_correlation_id = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = UUID(raw_correlation_id) if raw_correlation_id else uuid4()
        except ValueError:
            correlation_id = uuid4()
        request.state.correlation_id = correlation_id
        request.state.actor_id = request.headers.get("X-Actor-ID", "anonymous")
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(correlation_id)
        return response


app.add_middleware(RequestContextMiddleware)


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
        "available": ["health", "repositories", "workflow-runs", "github-webhooks"],
        "planned": ["audit-events", "persistent-storage"],
    }


@app.post(
    f"{settings.api_prefix}/repositories",
    response_model=Repository,
    status_code=status.HTTP_201_CREATED,
    tags=["repositories"],
)
async def create_repository(request: Request, payload: RepositoryCreate) -> Repository:
    return store.add_repository(payload, correlation_id=request.state.correlation_id)


@app.get(
    f"{settings.api_prefix}/repositories",
    response_model=list[Repository],
    tags=["repositories"],
)
async def list_repositories() -> list[Repository]:
    return list(store.repositories.values())


@app.post(
    f"{settings.api_prefix}/policies/evaluate",
    response_model=PolicyDecision,
    tags=["policies"],
)
async def evaluate_policy(payload: WorkflowRunCreate) -> PolicyDecision:
    return store.evaluate_policy(payload)


@app.post(
    f"{settings.api_prefix}/workflow-runs",
    response_model=WorkflowRun,
    status_code=status.HTTP_201_CREATED,
    tags=["workflow-runs"],
)
async def create_workflow_run(request: Request, payload: WorkflowRunCreate) -> WorkflowRun:
    if payload.repository_id not in store.repositories:
        raise HTTPException(status_code=404, detail="Repository not found")
    decision = store.evaluate_policy(payload)
    store.record_policy_decision(
        payload=payload,
        decision=decision,
        correlation_id=request.state.correlation_id,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail={"policy": decision.model_dump()})
    run = store.add_run(payload, correlation_id=request.state.correlation_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return run


@app.post(
    f"{settings.api_prefix}/workflow-runs/{{run_id}}/approve",
    response_model=WorkflowRun,
    tags=["workflow-runs"],
)
async def approve_workflow_run(
    request: Request, run_id: UUID, payload: WorkflowApproval
) -> WorkflowRun:
    run = store.approve_run(
        run_id,
        actor_id=payload.approved_by,
        correlation_id=request.state.correlation_id,
    )
    if run is None:
        raise HTTPException(status_code=409, detail="Workflow run is missing or not approvable")
    return run


@app.post(
    f"{settings.api_prefix}/workflow-runs/{{run_id}}/claim",
    response_model=WorkflowRun,
    tags=["workflow-runs"],
)
async def claim_workflow_run(
    request: Request, run_id: UUID, payload: WorkerClaim
) -> WorkflowRun:
    run = store.claim_run(
        run_id,
        worker_id=payload.worker_id,
        lease_seconds=payload.lease_seconds,
        correlation_id=request.state.correlation_id,
    )
    if run is None:
        raise HTTPException(status_code=409, detail="Workflow run is missing, unapproved, or already claimed")
    return run


@app.post(
    f"{settings.api_prefix}/workflow-runs/{{run_id}}/renew",
    response_model=WorkerLease,
    tags=["workflow-runs"],
)
async def renew_workflow_claim(
    request: Request, run_id: UUID, payload: WorkerClaim
) -> WorkerLease:
    lease = store.renew_claim(
        run_id, worker_id=payload.worker_id, lease_seconds=payload.lease_seconds
    )
    if lease is None:
        raise HTTPException(status_code=409, detail="Worker lease is missing, expired, or owned by another worker")
    store.record_event(
        action="workflow_run.lease_renewed",
        resource_type="workflow_run",
        resource_id=run_id,
        actor_id=payload.worker_id,
        correlation_id=request.state.correlation_id,
        metadata={"lease_expires_at": lease.expires_at.isoformat()},
    )
    return lease


@app.post(
    f"{settings.api_prefix}/workflow-runs/{{run_id}}/execute",
    response_model=WorkerResult,
    tags=["workflow-runs"],
)
async def execute_workflow_run(
    request: Request, run_id: UUID, payload: WorkerExecutionRequest
) -> WorkerResult:
    run = store.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    root = Path(settings.worker_workspace_root).resolve()
    workspace = Path(payload.workspace_path).resolve()
    try:
        workspace.relative_to(root)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Workspace is outside the configured worker root") from error
    if run_id in store.worker_results:
        raise HTTPException(status_code=409, detail="Workflow run has already been executed")
    lease = store.active_claim(run_id)
    if lease is None or lease.worker_id != payload.worker_id:
        raise HTTPException(status_code=409, detail="Worker lease is missing or expired")
    try:
        result = LocalWorker().execute(run, workspace)
    except WorkerError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return store.record_worker_result(result, correlation_id=request.state.correlation_id)


@app.get(
    f"{settings.api_prefix}/workflow-runs/{{run_id}}/result",
    response_model=WorkerResult,
    tags=["workflow-runs"],
)
async def get_workflow_result(run_id: UUID) -> WorkerResult:
    result = store.worker_results.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow result not found")
    return result


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


@app.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED, tags=["webhooks"])
async def github_webhook(request: Request) -> dict[str, str]:
    delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
    signature = request.headers.get("X-Hub-Signature-256", "")
    event_name = request.headers.get("X-GitHub-Event", "unknown").strip() or "unknown"
    body = await request.body()
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not delivery_id or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")
    event = store.accept_github_delivery(
        delivery_id=delivery_id,
        event_name=event_name,
        correlation_id=request.state.correlation_id,
    )
    if event is None:
        raise HTTPException(status_code=409, detail="GitHub delivery already processed")
    return {"status": "accepted", "delivery_id": delivery_id}


@app.get(
    f"{settings.api_prefix}/audit-events",
    response_model=list[AuditEvent],
    tags=["audit"],
)
async def list_audit_events() -> list[AuditEvent]:
    return list(store.audit_events)
