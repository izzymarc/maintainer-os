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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryStore:
    def __init__(self) -> None:
        self.repositories: dict[UUID, Repository] = {}
        self.runs: dict[UUID, WorkflowRun] = {}

    def add_repository(self, payload: RepositoryCreate) -> Repository:
        repository = Repository(id=uuid4(), created_at=now_utc(), **payload.model_dump())
        self.repositories[repository.id] = repository
        return repository

    def add_run(self, payload: WorkflowRunCreate) -> WorkflowRun | None:
        if payload.repository_id not in self.repositories:
            return None
        run = WorkflowRun(
            id=uuid4(),
            status=WorkflowStatus.QUEUED,
            created_at=now_utc(),
            **payload.model_dump(),
        )
        self.runs[run.id] = run
        return run

    def reset(self) -> None:
        self.repositories.clear()
        self.runs.clear()
