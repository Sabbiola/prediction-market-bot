# ADR 0001: Operator Control Plane / Web UI Strategy

## Status

Accepted (beta-live documentation scope, no runtime behavior change).

## Context

The current runtime already supports:

- `DRY_RUN_STATIC`, `PAPER_LIVE`, `SANDBOX_CHAIN`, `LIVE_DISABLED`
- blocking human review queue in beta-live modes
- separate settlement lane
- sandbox-chain as transaction rehearsal lane
- SQLite operational state + JSONL audit artifacts

Operators currently use CLI commands for health, status, review, settlement, and sandbox tx reconciliation.
This is functional but not ideal for staging operations at scale.

## Decision

Introduce an Operator Control Plane / Web UI as a dedicated bounded context.

The UI is an operator-facing control and observability surface on top of the existing runtime.
The UI does not own prediction, risk, execution, or settlement business logic.

## Responsibilities (UI)

- provide consolidated runtime visibility (health, queue depth, positions, tx states)
- expose operator actions already present in runtime control surfaces
- present audit-ready run, review, settlement, and tx traces
- support staging-safe workflows with explicit approvals and pause/resume controls

## Non-Responsibilities (UI)

- no fair probability or risk computation
- no direct venue-specific order posting logic
- no bypass of review/risk guardrails
- no online model training or auto-tuning

## Guardrails

- venue live order posting remains disabled in this phase
- sandbox-chain remains the only real transaction rehearsal lane
- operator actions must be attributable to operator identity and rationale
- all state changes remain persisted via existing operational/audit paths

## Consequences

Positive:

- better staging operability and lower operator friction
- clearer incident handling and faster triage
- stronger auditability for human-in-the-loop decisions

Tradeoffs:

- additional integration surface (auth, permissions, API boundaries)
- need to keep strict separation to avoid UI-layer business logic

## Rollout Direction

1. Read-only UI over existing runtime state and metrics.
2. Controlled write actions for review queue and tx reconciliation.
3. Hardening with auth/RBAC, audit enrichment, and incident tooling.
