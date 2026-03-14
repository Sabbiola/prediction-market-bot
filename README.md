# Prediction Market Bot

Piattaforma modulare multi-agent per operativita su prediction market in modalita sicura:

- paper execution
- sandbox-chain transaction rehearsal
- human-in-the-loop review gate
- audit JSONL + stato operativo SQLite

Il progetto e orientato a staging beta-live, con **zero venue trading reale**.

## Scope attuale

La pipeline operativa implementata e:

1. scansione mercati
2. ricerca multi-sorgente
3. prediction probabilistica
4. risk sizing e guardrail
5. review queue bloccante (in `PAPER_LIVE` e `SANDBOX_CHAIN`)
6. execution paper o sandbox transaction lane
7. settlement asincrono
8. replay/report/evaluation

## Principi architetturali

- domain isolato da infrastruttura
- runtime senza training online
- integrazioni esterne via interfaces/adapters
- contratti tipizzati per ogni artefatto di pipeline
- persistenza operativa separata da audit append-only

## Struttura repository

```text
prediction-market-bot/
  AGENTS.md
  README.md
  pyproject.toml
  Makefile
  config/
    app.yaml
    agents.yaml
  docs/
    ARCHITECTURE.md
    BETA_SCOPE.md
    BETA_GATE.md
    OPERATIONS.md
    DEVELOPMENT.md
    LEGACY_MAPPING.md
    REFACTOR_PLAN.md
  src/prediction_market_bot/
    app/
    domain/
    interfaces/
    agents/
    orchestration/
    services/
    infrastructure/
    main.py
  tests/
```

Nota: `src/prediction_market_bot/orchestrator` e `src/prediction_market_bot/ports` sono namespace legacy di compatibilita; il codice corrente usa `orchestration` e `interfaces`.

## Comandi operatore (CLI)

```bash
# startup/health
python -m prediction_market_bot.main validate-startup --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main healthcheck --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main status --config config/app.yaml --agents-config config/agents.yaml --json

# run control
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main run-scheduler --config config/app.yaml --agents-config config/agents.yaml --interval-sec 60
python -m prediction_market_bot.main pause --config config/app.yaml --agents-config config/agents.yaml --reason "incident_<id>"
python -m prediction_market_bot.main resume --config config/app.yaml --agents-config config/agents.yaml

# review gate
python -m prediction_market_bot.main review-list --config config/app.yaml --agents-config config/agents.yaml --status pending_review --json
python -m prediction_market_bot.main review-show --config config/app.yaml --agents-config config/agents.yaml --queue-id <queue_id> --json
python -m prediction_market_bot.main review-approve --config config/app.yaml --agents-config config/agents.yaml --queue-id <queue_id> --operator-id <operator> --rationale "<rationale>"
python -m prediction_market_bot.main review-reject --config config/app.yaml --agents-config config/agents.yaml --queue-id <queue_id> --operator-id <operator> --rationale "<rationale>"

# settlement/report/replay
python -m prediction_market_bot.main run-settlement-lane --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main last-report --config config/app.yaml --agents-config config/agents.yaml --format markdown

# sandbox tx lane
python -m prediction_market_bot.main tx-status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-reconcile --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-resubmit-safe --config config/app.yaml --agents-config config/agents.yaml --intent-id <intent_id> --json
```

## Comandi sviluppatore

```bash
make install
make lint
make typecheck
make test              # default: esclude suite acceptance
make test-all          # include tutto
make beta-acceptance   # suite beta-live acceptance
make ci
```

## CI

Workflow GitHub Actions: `.github/workflows/ci.yml`

I job principali sono:

- `Quality Checks`
- `Smoke Dry Run`
- `Smoke Beta-Live (Mocked Providers + Sandbox Tx)`
- `Beta-Live Acceptance Gate`

La release readiness beta-live e documentata in [docs/BETA_GATE.md](docs/BETA_GATE.md).

## Sicurezza operativa

- live venue order posting disabilitato
- nessuna credenziale venue reale richiesta per test e CI
- ogni transazione sandbox deve essere tracciabile a `run_id` e review decision
