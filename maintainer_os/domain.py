import json
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, Field, HttpUrl


class WorkflowType(StrEnum):
    REVIEW = "review"
    TEST_GENERATION = "test_generation"
    ISSUE_TRIAGE = "issue_triage"


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"


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


class WorkflowApproval(BaseModel):
    approved_by: str = Field(min_length=1, max_length=255)


class PolicyDecision(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    policy_version: str = "v1"


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
        self.github_deliveries: set[str] = set()

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

    def evaluate_policy(self, payload: WorkflowRunCreate) -> PolicyDecision:
        reasons: list[str] = []
        if payload.ref in {"main", "master", "production"}:
            reasons.append("protected refs require an explicit policy exception")
        if payload.requested_by == "anonymous":
            reasons.append("anonymous actors cannot start workflows")
        return PolicyDecision(allowed=not reasons, reasons=reasons)

    def record_policy_decision(
        self,
        *,
        payload: WorkflowRunCreate,
        decision: PolicyDecision,
        correlation_id: UUID,
    ) -> AuditEvent:
        return self.record_event(
            action="workflow_run.policy_evaluated",
            resource_type="repository",
            resource_id=payload.repository_id,
            actor_id=payload.requested_by,
            correlation_id=correlation_id,
            metadata={
                "allowed": str(decision.allowed).lower(),
                "policy_version": decision.policy_version,
                "reasons": "; ".join(decision.reasons),
                "workflow_type": payload.workflow_type.value,
                "ref": payload.ref,
            },
        )

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

    def approve_run(self, run_id: UUID, *, actor_id: str, correlation_id: UUID) -> WorkflowRun | None:
        run = self.runs.get(run_id)
        if run is None or run.status is not WorkflowStatus.QUEUED:
            return None
        run.status = WorkflowStatus.APPROVED
        self.record_event(
            action="workflow_run.approved",
            resource_type="workflow_run",
            resource_id=run.id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            metadata={"status": run.status.value},
        )
        return run

    def accept_github_delivery(
        self,
        *,
        delivery_id: str,
        event_name: str,
        correlation_id: UUID,
    ) -> AuditEvent | None:
        if delivery_id in self.github_deliveries:
            return None
        self.github_deliveries.add(delivery_id)
        return self.record_event(
            action="github_webhook.accepted",
            resource_type="github_delivery",
            resource_id=uuid5(NAMESPACE_URL, f"github-delivery:{delivery_id}"),
            actor_id="github",
            correlation_id=correlation_id,
            metadata={"delivery_id": delivery_id, "event": event_name},
        )

    def reset(self) -> None:
        self.repositories.clear()
        self.runs.clear()
        self.audit_events.clear()
        self.github_deliveries.clear()


class SQLiteStore(InMemoryStore):
    """Durable store for single-process deployments and local development."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        self._load()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY, full_name TEXT NOT NULL, clone_url TEXT NOT NULL,
                default_branch TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, workflow_type TEXT NOT NULL,
                ref TEXT NOT NULL, requested_by TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, FOREIGN KEY(repository_id) REFERENCES repositories(id)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL, actor_id TEXT NOT NULL, correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL, metadata TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS github_deliveries (
                delivery_id TEXT PRIMARY KEY
            );
            """
        )
        self.connection.commit()

    def _load(self) -> None:
        for row in self.connection.execute("SELECT * FROM repositories"):
            repository = Repository(
                id=UUID(row["id"]), full_name=row["full_name"], clone_url=row["clone_url"],
                default_branch=row["default_branch"], created_at=datetime.fromisoformat(row["created_at"]),
            )
            self.repositories[repository.id] = repository
        for row in self.connection.execute("SELECT * FROM workflow_runs"):
            run = WorkflowRun(
                id=UUID(row["id"]), repository_id=UUID(row["repository_id"]),
                workflow_type=row["workflow_type"], ref=row["ref"], requested_by=row["requested_by"],
                status=row["status"], created_at=datetime.fromisoformat(row["created_at"]),
            )
            self.runs[run.id] = run
        for row in self.connection.execute("SELECT * FROM audit_events ORDER BY rowid"):
            self.audit_events.append(
                AuditEvent(
                    id=UUID(row["id"]), action=row["action"], resource_type=row["resource_type"],
                    resource_id=UUID(row["resource_id"]), actor_id=row["actor_id"],
                    correlation_id=UUID(row["correlation_id"]), created_at=datetime.fromisoformat(row["created_at"]),
                    metadata=json.loads(row["metadata"]),
                )
            )
        self.github_deliveries = {
            row["delivery_id"] for row in self.connection.execute("SELECT delivery_id FROM github_deliveries")
        }

    def record_event(self, **kwargs):
        event = super().record_event(**kwargs)
        self.connection.execute(
            "INSERT OR IGNORE INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(event.id), event.action, event.resource_type, str(event.resource_id), event.actor_id,
             str(event.correlation_id), event.created_at.isoformat(), json.dumps(event.metadata, sort_keys=True)),
        )
        self.connection.commit()
        return event

    def add_repository(self, payload: RepositoryCreate, *, correlation_id: UUID) -> Repository:
        repository = super().add_repository(payload, correlation_id=correlation_id)
        self.connection.execute(
            "INSERT OR REPLACE INTO repositories VALUES (?, ?, ?, ?, ?)",
            (str(repository.id), repository.full_name, str(repository.clone_url), repository.default_branch,
             repository.created_at.isoformat()),
        )
        self.connection.commit()
        return repository

    def add_run(self, payload: WorkflowRunCreate, *, correlation_id: UUID) -> WorkflowRun | None:
        run = super().add_run(payload, correlation_id=correlation_id)
        if run:
            self.connection.execute(
                "INSERT OR REPLACE INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(run.id), str(run.repository_id), run.workflow_type.value, run.ref, run.requested_by,
                 run.status.value, run.created_at.isoformat()),
            )
            self.connection.commit()
        return run

    def approve_run(self, run_id: UUID, *, actor_id: str, correlation_id: UUID) -> WorkflowRun | None:
        run = super().approve_run(run_id, actor_id=actor_id, correlation_id=correlation_id)
        if run:
            self.connection.execute("UPDATE workflow_runs SET status = ? WHERE id = ?", (run.status.value, str(run.id)))
            self.connection.commit()
        return run

    def accept_github_delivery(self, *, delivery_id: str, event_name: str, correlation_id: UUID) -> AuditEvent | None:
        event = super().accept_github_delivery(
            delivery_id=delivery_id, event_name=event_name, correlation_id=correlation_id
        )
        if event:
            self.connection.execute("INSERT OR IGNORE INTO github_deliveries VALUES (?)", (delivery_id,))
            self.connection.commit()
        return event

    def reset(self) -> None:
        super().reset()
        self.connection.executescript(
            "DELETE FROM repositories; DELETE FROM workflow_runs; DELETE FROM audit_events; DELETE FROM github_deliveries;"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
