# Prediction Market Bot

Sistema modulare multi-agent per analisi, valutazione ed esecuzione **dry-run** su prediction markets.

Il progetto nasce come rifondazione pulita di un vecchio bot di trading automatico, con una nuova architettura centrata su:

- scansione opportunità di mercato
- ricerca multi-sorgente
- stima di probabilità fair
- calcolo edge
- position sizing basato sul rischio
- esecuzione simulata
- settlement simulato
- postmortem automatico

## Obiettivo

Costruire una codebase **production-grade**, fortemente tipizzata, testabile e pronta ad accogliere in seguito adapter reali per:

- venue di prediction markets
- news providers
- social/research sources
- persistence backend
- execution backend
- settlement backend

La prima milestone è un **MVP completamente funzionante in dry-run**.

---

## Principi architetturali

Il progetto segue una **clean architecture** con separazione netta tra dominio, agenti, orchestrazione e infrastruttura.

### Regole fondamentali

- il **domain layer** non dipende da infrastructure
- nessun import dal codice legacy nel runtime
- nessun training loop dentro il runtime
- tutte le integrazioni esterne passano da **ports/adapters**
- ogni agente deve essere testabile in isolamento
- ogni decisione deve produrre un payload di spiegazione
- la modalità **dry-run** deve essere sempre funzionante
- niente god classes
- niente segreti hardcoded
- niente logica critica nascosta in script monolitici

---

## Architettura del sistema

La pipeline target è:

1. **ScanAgent**
   - seleziona e ranka mercati interessanti
   - valuta liquidità, spread, activity, tempo alla risoluzione

2. **ResearchAgent**
   - aggrega findings da fonti mock o future fonti reali
   - stima sentiment, evidence strength, disagreement score

3. **PredictionAgent**
   - fonde probabilità implicita di mercato e segnale di ricerca
   - produce fair probability, edge e confidence

4. **RiskAgent**
   - applica threshold, confidence gating, exposure cap e sizing
   - produce una decisione di rischio spiegabile

5. **ExecutionAgent**
   - genera `OrderIntent`
   - simula l'esecuzione in modalità dry-run

6. **SettlementAgent**
   - simula la risoluzione del mercato
   - calcola pnl e outcome finale

7. **PostmortemAgent**
   - classifica il risultato
   - individua cause, lezioni e action items

---

## Struttura repository

```text
prediction-market-bot/
├─ AGENTS.md
├─ README.md
├─ pyproject.toml
├─ .env.example
├─ Makefile
├─ config/
│  ├─ app.yaml
│  └─ agents.yaml
├─ docs/
│  ├─ ARCHITECTURE.md
│  ├─ REFACTOR_PLAN.md
│  ├─ LEGACY_MAPPING.md
│  └─ DEVELOPMENT.md
├─ src/
│  └─ prediction_market_bot/
│     ├─ __init__.py
│     ├─ main.py
│     ├─ domain/
│     ├─ agents/
│     ├─ services/
│     ├─ orchestrator/
│     ├─ infrastructure/
│     └─ ports/
└─ tests/
```

## Documentazione operativa

- [BETA_SCOPE.md](docs/BETA_SCOPE.md)
- [OPERATIONS.md](docs/OPERATIONS.md)

## CI (beta)

GitHub Actions workflow: `.github/workflows/ci.yml`

Il workflow esegue in ordine:

- `make lint`
- `make typecheck`
- `make test`
- `make ci-smoke RUN_ID=ci-smoke-<github_run_id>`

La smoke run produce report markdown/json e li carica come artifact CI insieme a:

- `data/artifacts/pipeline_summaries.jsonl`
- `data/audit/events.jsonl`

## Comandi operatore (CLI)

```bash
# stato sistema operatore
python -m prediction_market_bot.main status --config config/app.yaml --agents-config config/agents.yaml

# esecuzione singola dry-run
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>

# scheduler CLI
python -m prediction_market_bot.main run-scheduler --config config/app.yaml --agents-config config/agents.yaml --interval-sec 60

# safety controls
python -m prediction_market_bot.main pause --config config/app.yaml --agents-config config/agents.yaml --reason "manual_pause"
python -m prediction_market_bot.main resume --config config/app.yaml --agents-config config/agents.yaml

# portfolio e replay
python -m prediction_market_bot.main portfolio --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main replay --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>

# report run
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main last-report --config config/app.yaml --agents-config config/agents.yaml --format markdown

# evaluation
python -m prediction_market_bot.main evaluate-window --config config/app.yaml --agents-config config/agents.yaml --limit-runs 50
python -m prediction_market_bot.main generate-eval-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
```

## Comandi sviluppatore (Makefile)

```bash
make install
make lint
make typecheck
make test
make run
make smoke-dry-run RUN_ID=local-smoke-001
make ci-quality
make ci
```

## Repository Hygiene

Regole operative per beta-live development:

- tenere in git solo codice runtime (`src/`), config (`config/`), test (`tests/`) e documentazione (`docs/`)
- non tracciare cache locali (`.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, `*.pyc`)
- non tracciare output runtime locali (`data/artifacts/*`, `data/audit/*`)
- mantenere solo placeholder espliciti (`data/artifacts/.gitkeep`, `data/audit/.gitkeep`)
- trattare `solana-memecoin-bot-main/` (o eventuale `legacy_reference/`) come materiale legacy non-runtime

Il packaging e i test escludono esplicitamente cartelle legacy/tmp/cache/artifacts per evitare effetti collaterali in CI.
