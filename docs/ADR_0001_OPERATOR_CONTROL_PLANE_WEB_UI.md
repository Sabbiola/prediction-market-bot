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

## Implementation Boundary (Read/Query Layer)

UI read-models are organized as a dedicated package (`src/prediction_market_bot/ui/read_models/`) with explicit layering:

- `queries.py`: read-only data access to JSONL artifacts/events and operational repositories
- panel modules (`overview.py`, `system.py`, `scanner.py`, `research.py`, `prediction.py`, `risk.py`, `review_queue.py`, `execution.py`, `positions.py`, `settlement.py`, `sandbox_tx.py`, `reports.py`): per-tab aggregation logic
- `ui/models.py`: typed UI-facing response/view-model contracts
- `service.py`: thin facade used by API/page routes, including short-TTL poll cache

Tab ownership is explicit at module level to prevent a new UI god object and keep route handlers thin.

## UI Visual Foundation (Design System)

The control-plane UI uses a lightweight server-rendered design system in `ui/templates/base.html`:

- design tokens for colors, spacing, typography, radius and shadows (`:root`)
- shared component classes for `card`, `pill`, `alert`, `btn`, `tabs`, table and form controls
- utility classes for vertical rhythm (`mt-*`, `mb-*`, `meta-line`, `empty-note`) to avoid inline style duplication
- semantic status hierarchy:
  - health/severity: `ok`, `warning`, `critical` (`pill--approved` / `pill--warn` / `pill--failed`)
  - workflow states: `pending`, `approved`, `rejected`, `failed`

Design constraints:

- no SPA rewrite; keep server-rendered templates simple and fast
- no business logic introduced by styling changes
- keep templates declarative and readable; route/read-model layers remain owners of shaping logic

## Engine Tab Responsibilities

Engine tabs (`Scanner`, `Research`, `Prediction`, `Risk`) follow a shared operator mental model:

1. summary KPIs
2. health/status banner
3. primary table/list
4. diagnostics
5. anomalies/explanations

Responsibility split:

- UI read-model layer computes operator-facing summaries and anomalies from persisted artifacts/config.
- Route handlers remain thin and do not implement per-tab logic.
- Templates render shaped view models and avoid business decision logic.

## Post-Approval Tab Responsibilities

Post-approval operator workflow ownership is explicit:

1. `Review Queue`: human approval/rejection and rationale/audit decision.
2. `Execution`: execution decision path visibility (`skipped/submitted/completed/failed`) and linkage to review decision.
3. `Sandbox TX`: intent/attempt/receipt lifecycle, reconcile state, safe-resubmit affordance.
4. `Positions`: first-class open position visibility (entry, exposure, simulated PnL) with links to review/tx/settlement.
5. `Settlement`: pending vs resolved lifecycle, realized PnL, retry/failure visibility.

This keeps operator mental model linear without moving business logic into templates/routes.

## Rollout Direction

1. Read-only UI over existing runtime state and metrics.
2. Controlled write actions for review queue and tx reconciliation.
3. Hardening with auth/RBAC, audit enrichment, and incident tooling.
