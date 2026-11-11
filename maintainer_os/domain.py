from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl


class WorkflowType(StrEnum):
    REVIEW = "review"
    TEST_GENERATION = "test_generation"
    ISSUE_TRIAGE = "issue_triage"


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    AWAITING_APPROVAL = "awaiting_approval"


class RepositoryCreate(BaseModel):
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    clone_url: HttpUrl
    default_branch: str = Field(default="main", min_length=1, max_length=255)


class Repository(RepositoryCreate):
    id: UUID
    created_at: datetime


class WorkflowRunCreate(BaseModel):
    repository_id: UUID
    workflow_type: WorkflowType
    ref: str = Field(default="main", min_length=1, max_length=255)
    requested_by: str = Field(min_length=1, max_length=255)


class WorkflowRun(WorkflowRunCreate):
    id: UUID
    status: WorkflowStatus
    created_at: datetime


class AuditEvent(BaseModel):
    id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    actor_id: str
    correlation_id: UUID
    created_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryStore:
    def __init__(self) -> None:
        self.repositories: dict[UUID, Repository] = {}
        self.runs: dict[UUID, WorkflowRun] = {}
        self.audit_events: list[AuditEvent] = []

    def record_event(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: UUID,
        actor_id: str,
        correlation_id: UUID,
        metadata: dict[str, str] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=uuid4(),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            created_at=now_utc(),
            metadata=metadata or {},
        )
        self.audit_events.append(event)
        return event

    def add_repository(self, payload: RepositoryCreate, *, correlation_id: UUID) -> Repository:
        repository = Repository(id=uuid4(), created_at=now_utc(), **payload.model_dump())
        self.repositories[repository.id] = repository
        self.record_event(
            action="repository.created",
            resource_type="repository",
            resource_id=repository.id,
            actor_id="system",
            correlation_id=correlation_id,
            metadata={"full_name": repository.full_name},
        )
        return repository

    def add_run(self, payload: WorkflowRunCreate, *, correlation_id: UUID) -> WorkflowRun | None:
        if payload.repository_id not in self.repositories:
            return None
        run = WorkflowRun(
            id=uuid4(),
            status=WorkflowStatus.QUEUED,
            created_at=now_utc(),
            **payload.model_dump(),
        )
        self.runs[run.id] = run
        self.record_event(
            action="workflow_run.created",
            resource_type="workflow_run",
            resource_id=run.id,
            actor_id=run.requested_by,
            correlation_id=correlation_id,
            metadata={"workflow_type": run.workflow_type.value, "status": run.status.value},
        )
        return run

    def reset(self) -> None:
        self.repositories.clear()
        self.runs.clear()
        self.audit_events.clear()
