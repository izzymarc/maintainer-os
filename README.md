# MaintainerOS

**The open-source control plane for software maintenance.**

MaintainerOS is a self-hosted platform for running safe, auditable maintenance workflows across software repositories. It is designed to help maintainers review changes, generate regression tests, triage issues, and keep documentation current—with explicit human approval before repository mutations.

> Status: early foundation (`0.1.0`). The current release exposes repository, workflow-run, and audit-event contracts; integrations and persistence are intentionally staged behind a documented roadmap.

## Design goals

- **Human controlled:** no merge, release, or destructive operation occurs without an explicit policy decision.
- **Auditable:** every agent action will have a durable run record, inputs, outputs, and approval state.
- **Least privilege:** GitHub access uses narrowly scoped installations and short-lived credentials.
- **Provider neutral:** model providers are adapters, not the product boundary.
- **Self-hostable:** local development uses Docker Compose, with deployment options documented separately.

## Quick start

Requirements: Python 3.11+ and optionally Docker Compose.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
uvicorn maintainer_os.main:app --reload
```

Check the service:

```bash
curl http://127.0.0.1:8000/health
```

Run tests:

```bash
pytest
```

## Local services

```bash
docker compose up -d postgres redis
cp .env.example .env
```

By default, the service uses an in-memory store for fast tests. For a durable single-process deployment, set `MAINTAINEROS_STORAGE=sqlite` and configure `MAINTAINEROS_SQLITE_PATH`; the SQLite store persists repositories, workflow runs, audit events, and GitHub delivery deduplication. The fixed-profile local worker is available for development, with single-worker claims, expiring leases, and idempotent result retrieval; production execution still requires container/VM isolation.

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for boundaries, threat assumptions, and the staged implementation plan.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening an issue or pull request. Security reports should follow [`SECURITY.md`](SECURITY.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE).
