import hashlib
import hmac

import httpx
import pytest
import pytest_asyncio

from maintainer_os.config import settings

from maintainer_os.main import app, store


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest_asyncio.fixture(autouse=True)
async def reset_store() -> None:
    store.reset()
    yield
    store.reset()


@pytest.mark.asyncio
async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_capabilities_are_explicit(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    assert "repositories" in response.json()["available"]


@pytest.mark.asyncio
async def test_policy_evaluation_has_safe_defaults(client: httpx.AsyncClient) -> None:
    payload = {
        "repository_id": "00000000-0000-0000-0000-000000000001",
        "workflow_type": "review",
        "ref": "main",
        "requested_by": "maintainer@example.com",
    }
    response = await client.post("/api/v1/policies/evaluate", json=payload)
    assert response.status_code == 200
    assert response.json()["allowed"] is False
    assert "protected refs" in response.json()["reasons"][0]

    safe_payload = {**payload, "ref": "feature/safe-review"}
    safe_response = await client.post("/api/v1/policies/evaluate", json=safe_payload)
    assert safe_response.json() == {"allowed": True, "reasons": [], "policy_version": "v1"}


@pytest.mark.asyncio
async def test_repository_and_workflow_run_lifecycle(client: httpx.AsyncClient) -> None:
    repository_response = await client.post(
        "/api/v1/repositories",
        json={
            "full_name": "izzymarc/example",
            "clone_url": "https://github.com/izzymarc/example.git",
            "default_branch": "main",
        },
    )
    assert repository_response.status_code == 201
    repository = repository_response.json()

    run_response = await client.post(
        "/api/v1/workflow-runs",
        json={
            "repository_id": repository["id"],
            "workflow_type": "review",
            "ref": "feature/review",
            "requested_by": "maintainer@example.com",
        },
    )
    assert run_response.status_code == 201
    assert run_response.json()["status"] == "queued"

    fetched_response = await client.get(f"/api/v1/workflow-runs/{run_response.json()['id']}")
    assert fetched_response.status_code == 200
    assert fetched_response.json()["repository_id"] == repository["id"]

    audit_response = await client.get("/api/v1/audit-events")
    assert audit_response.status_code == 200
    events = audit_response.json()
    assert [event["action"] for event in events] == [
        "repository.created",
        "workflow_run.policy_evaluated",
        "workflow_run.created",
    ]
    assert events[2]["actor_id"] == "maintainer@example.com"
    assert events[0]["correlation_id"] != "00000000-0000-0000-0000-000000000000"
    assert events[1]["metadata"]["allowed"] == "true"
    assert events[2]["correlation_id"] != events[0]["correlation_id"]


@pytest.mark.asyncio
async def test_approval_is_explicit_and_audited(client: httpx.AsyncClient) -> None:
    repository_response = await client.post(
        "/api/v1/repositories",
        headers={"X-Correlation-ID": "11111111-1111-1111-1111-111111111111"},
        json={
            "full_name": "izzymarc/approval-example",
            "clone_url": "https://github.com/izzymarc/approval-example.git",
        },
    )
    repository = repository_response.json()
    run_response = await client.post(
        "/api/v1/workflow-runs",
        json={
            "repository_id": repository["id"],
            "workflow_type": "test_generation",
            "ref": "feature/tests",
            "requested_by": "agent@example.com",
        },
    )
    run_id = run_response.json()["id"]

    approval_response = await client.post(
        f"/api/v1/workflow-runs/{run_id}/approve",
        headers={
            "X-Correlation-ID": "22222222-2222-2222-2222-222222222222",
        },
        json={"approved_by": "reviewer@example.com"},
    )
    assert approval_response.status_code == 200
    assert approval_response.headers["X-Correlation-ID"] == "22222222-2222-2222-2222-222222222222"
    assert approval_response.json()["status"] == "approved"

    repeat_response = await client.post(
        f"/api/v1/workflow-runs/{run_id}/approve",
        json={"approved_by": "reviewer@example.com"},
    )
    assert repeat_response.status_code == 409

    audit_response = await client.get("/api/v1/audit-events")
    events = audit_response.json()
    assert events[-1]["action"] == "workflow_run.approved"
    assert events[-1]["actor_id"] == "reviewer@example.com"
    assert events[-1]["correlation_id"] == "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_protected_workflow_is_blocked_and_audited(client: httpx.AsyncClient) -> None:
    repository_response = await client.post(
        "/api/v1/repositories",
        json={
            "full_name": "izzymarc/policy-example",
            "clone_url": "https://github.com/izzymarc/policy-example.git",
        },
    )
    repository = repository_response.json()
    response = await client.post(
        "/api/v1/workflow-runs",
        json={
            "repository_id": repository["id"],
            "workflow_type": "review",
            "ref": "main",
            "requested_by": "maintainer@example.com",
        },
    )
    assert response.status_code == 403
    assert "protected refs" in response.json()["detail"]["policy"]["reasons"][0]
    events = (await client.get("/api/v1/audit-events")).json()
    assert events[-1]["action"] == "workflow_run.policy_evaluated"
    assert events[-1]["metadata"]["allowed"] == "false"


@pytest.mark.asyncio
async def test_github_webhook_requires_valid_signature_and_deduplicates(
    client: httpx.AsyncClient,
) -> None:
    body = b'{"action":"opened"}'
    signature = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    headers = {
        "X-GitHub-Delivery": "delivery-123",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": signature,
    }

    accepted = await client.post("/webhooks/github", content=body, headers=headers)
    assert accepted.status_code == 202
    assert accepted.json()["delivery_id"] == "delivery-123"

    duplicate = await client.post("/webhooks/github", content=body, headers=headers)
    assert duplicate.status_code == 409

    events = (await client.get("/api/v1/audit-events")).json()
    assert events[-1]["action"] == "github_webhook.accepted"
    assert events[-1]["metadata"] == {"delivery_id": "delivery-123", "event": "issues"}


@pytest.mark.asyncio
async def test_github_webhook_rejects_invalid_signature(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/webhooks/github",
        content=b"{}",
        headers={
            "X-GitHub-Delivery": "delivery-invalid",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_execution_requires_approval_and_records_result(
    client: httpx.AsyncClient, tmp_path, monkeypatch
) -> None:
    from pathlib import Path

    monkeypatch.setattr(settings, "worker_workspace_root", str(tmp_path))
    workspace = tmp_path / "approved-workspace"
    workspace.mkdir()
    repository = (
        await client.post(
            "/api/v1/repositories",
            json={
                "full_name": "izzymarc/worker-api",
                "clone_url": "https://github.com/izzymarc/worker-api.git",
            },
        )
    ).json()
    run = (
        await client.post(
            "/api/v1/workflow-runs",
            json={
                "repository_id": repository["id"],
                "workflow_type": "review",
                "ref": "feature/worker",
                "requested_by": "maintainer@example.com",
            },
        )
    ).json()
    rejected = await client.post(
        f"/api/v1/workflow-runs/{run['id']}/execute",
        json={"workspace_path": str(workspace)},
    )
    assert rejected.status_code == 409

    approved = await client.post(
        f"/api/v1/workflow-runs/{run['id']}/approve",
        json={"approved_by": "reviewer@example.com"},
    )
    assert approved.status_code == 200
    executed = await client.post(
        f"/api/v1/workflow-runs/{run['id']}/execute",
        json={"workspace_path": str(workspace)},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"
    events = (await client.get("/api/v1/audit-events")).json()
    assert events[-1]["action"] == "workflow_run.executed"
    assert events[-1]["metadata"]["status"] == "succeeded"
    assert "stdout" not in events[-1]["metadata"]

    outside = await client.post(
        f"/api/v1/workflow-runs/{run['id']}/execute",
        json={"workspace_path": str(Path(tmp_path).parent)},
    )
    assert outside.status_code == 400


@pytest.mark.asyncio
async def test_workflow_run_requires_existing_repository(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/workflow-runs",
        json={
            "repository_id": "00000000-0000-0000-0000-000000000000",
            "workflow_type": "review",
            "requested_by": "maintainer@example.com",
        },
    )
    assert response.status_code == 404


def test_worker_requires_approval_and_runs_fixed_profile(tmp_path) -> None:
    from uuid import uuid4

    from maintainer_os.domain import WorkflowRun, WorkflowStatus, WorkflowType
    from maintainer_os.runner import LocalWorker, WorkerError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = WorkflowRun(
        id=uuid4(),
        repository_id=uuid4(),
        workflow_type=WorkflowType.REVIEW,
        ref="feature/safe",
        requested_by="maintainer@example.com",
        status=WorkflowStatus.QUEUED,
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    worker = LocalWorker(timeout_seconds=10)
    with pytest.raises(WorkerError, match="approved"):
        worker.execute(run, workspace)

    run.status = WorkflowStatus.APPROVED
    result = worker.execute(run, workspace)
    assert result.status == "succeeded"
    assert result.exit_code == 0


def test_worker_times_out_and_caps_output(tmp_path) -> None:
    import sys
    from uuid import uuid4

    from maintainer_os.domain import WorkflowRun, WorkflowStatus, WorkflowType
    from maintainer_os.runner import LocalWorker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = WorkflowRun(
        id=uuid4(),
        repository_id=uuid4(),
        workflow_type=WorkflowType.TEST_GENERATION,
        ref="feature/tests",
        requested_by="maintainer@example.com",
        status=WorkflowStatus.APPROVED,
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    worker = LocalWorker(timeout_seconds=1, max_output_bytes=8)
    worker.COMMANDS["test_generation"] = (sys.executable, "-c", "import time; time.sleep(2)")
    result = worker.execute(run, workspace)
    assert result.status == "timed_out"
    assert result.timed_out is True
    assert len(result.stdout.encode()) <= 8
    assert len(result.stderr.encode()) <= 8


def test_sqlite_store_survives_restart(tmp_path) -> None:
    from uuid import uuid4

    from maintainer_os.domain import RepositoryCreate, SQLiteStore

    database_path = tmp_path / "maintainer-os.db"
    first = SQLiteStore(str(database_path))
    repository = first.add_repository(
        RepositoryCreate(
            full_name="izzymarc/durable-example",
            clone_url="https://github.com/izzymarc/durable-example.git",
        ),
        correlation_id=uuid4(),
    )
    first.close()

    second = SQLiteStore(str(database_path))
    assert repository.id in second.repositories
    assert second.repositories[repository.id].full_name == "izzymarc/durable-example"
    second.close()
