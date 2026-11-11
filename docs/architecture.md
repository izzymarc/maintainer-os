# MaintainerOS architecture

## System boundary

MaintainerOS has five planned subsystems:

1. **Control plane:** authenticated API for organizations, repositories, policies, and workflow runs.
2. **Scheduler:** durable queue that dispatches approved work and retries transient failures.
3. **Sandbox runner:** isolated containers for checkout, analysis, tests, and patch generation.
4. **Integration gateway:** GitHub App webhooks and APIs, with provider adapters behind explicit permissions.
5. **Audit store:** append-only run events, artifacts, approvals, and policy decisions.

The current `0.1.0` implementation contains the control-plane process, repository/workflow contracts, and an in-memory audit log. This is deliberate: external side effects will not be added until authentication, authorization, idempotency, and persistent audit schemas are tested.

## Safety model

- Repository access is installation-scoped and read-only by default.
- A workflow may propose a patch, but a separate approval is required before creating or updating a pull request.
- Secrets are injected only into the smallest execution scope and are never included in prompts or logs.
- Runners are ephemeral, resource-limited, network-restricted, and destroyed after a run.
- Webhook delivery is verified, deduplicated, and processed asynchronously.
- Every state transition has an actor, timestamp, request identifier, and policy result.

## Milestones

- [x] Service skeleton, configuration, health contract, and CI
- [x] In-memory repository, workflow-run, and audit-event contracts
- [ ] Database migrations for organizations, repositories, policies, and runs
- [ ] GitHub App installation and signed webhook ingestion
- [ ] Queue and isolated runner interfaces
- [ ] Review and test-generation workflows with approval gates
- [ ] Audit event export and operator dashboard
- [ ] Public evaluation suite for correctness, safety, and cost

## Non-goals for the first release

- Autonomous merging or production deployment
- Storage of arbitrary long-lived repository secrets
- Supporting every forge and model provider at once
- Claiming production readiness before security review and integration tests
