import httpx
import pytest
import pytest_asyncio

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
        "workflow_run.created",
    ]
    assert events[1]["actor_id"] == "maintainer@example.com"
    assert events[0]["correlation_id"] != "00000000-0000-0000-0000-000000000000"
    assert events[1]["correlation_id"] != events[0]["correlation_id"]


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
