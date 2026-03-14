# Architettura

## 8. Nuova architettura target consigliata

```text
prediction-market-bot/
  AGENTS.md
  README.md
  pyproject.toml
  config/
    agents.yaml
    app.yaml
  docs/
    ARCHITECTURE.md
    BETA_SCOPE.md
    BETA_GATE.md
    OPERATIONS.md
    DEVELOPMENT.md
    LEGACY_MAPPING.md
    REFACTOR_PLAN.md
  src/
    prediction_market_bot/
      app/
        settings.py
      domain/
        enums.py
        models.py
      interfaces/
        ports.py
      agents/
        base.py
        scanner.py
        research.py
        prediction.py
        risk.py
        execution.py
        settlement.py
        postmortem.py
      orchestration/
        coordinator.py
      ui/
        app.py
        server.py
        read_models.py
        routes/
        templates/
      main.py
  tests/
    test_pipeline_smoke.py
```

## 9. Bounded contexts del nuovo progetto

### 9.1 Market Discovery
Responsabile di:

- listing mercati attivi
- snapshot quote
- spread/liquidity/volume
- tempo alla risoluzione
- candidate selection

### 9.2 Narrative Research
Responsabile di:

- raccolta fonti parallele
- scoring credibilità
- sentiment / stance
- sintesi narrativa
- contradiction / disagreement detection

### 9.3 Probabilistic Modeling
Responsabile di:

- features quantitative del mercato
- features narrative
- fair probability
- edge vs market
- confidence

### 9.4 Risk & Portfolio
Responsabile di:

- bankroll state
- edge-based sizing
- fractional Kelly
- exposure cap per market / category / event date
- daily loss guardrails

### 9.5 Execution & Settlement
Responsabile di:

- traduzione ordine
- invio ordine
- reconciliation fill
- settlement
- PnL finale

### 9.6 Postmortem & Learning Memory
Responsabile di:

- root cause analysis delle loss
- classificazione errori
- action items
- aggiornamento dataset/feature backlog/risk rules

### 9.7 Operator Control Plane / Web UI
Responsabile di:

- visualizzazione stato operativo runtime (health, run, queue, posizioni, tx)
- orchestrazione azioni operatore gia esposte dal runtime
- osservabilita e audit UX per review/settlement/sandbox lane
- supporto al workflow giornaliero in staging

Non responsabile di:

- logica di prediction/risk/execution/settlement
- bypass dei guardrail di review e rischio
- posting ordini live verso venue

Vincoli:

- venue live order posting resta disabilitata in questa fase
- `SANDBOX_CHAIN` resta la lane transazionale reale di rehearsal
- tutte le azioni devono restare tracciabili con `run_id` e metadata operatore

Implementazione foundation (stato attuale):

- backend FastAPI in `src/prediction_market_bot/ui/`
- endpoint base:
  - `GET /health`
  - `GET /ready`
- endpoint tabbed read-only:
  - `GET /api/tabs/overview`
  - `GET /api/tabs/scanner`
  - `GET /api/tabs/research`
  - `GET /api/tabs/prediction`
  - `GET /api/tabs/risk`
  - `GET /api/tabs/execution`
  - `GET /api/tabs/settlement`
  - `GET /api/tabs/sandbox-tx`
  - `GET /api/tabs/reports`
  - `GET /api/tabs/system`
- operator actions:
  - `POST /api/actions/run-once`
  - `POST /api/actions/pause`
  - `POST /api/actions/resume`
- shell HTML server-rendered tramite template, con route `GET /`
- layer read-model dedicato per aggregare dati runtime senza logica business nei controller

## 12. Scelte implementative che consiglio senza esitazione

## 12.1 Una sola source of truth per l’inferenza
Nel nuovo progetto:
- niente training nel loop runtime
- solo inference online
- training offline separato

## 12.2 Nessuna dipendenza runtime da script di training
Le features condivise devono stare in:
- `src/prediction_market_bot/...`
non in `train_*.py`.

## 12.3 Dominio market/outcome, non ticker/price
Il prediction market è un dominio diverso.
Conviene rifarlo correttamente da subito.

## 12.4 Agent contracts tipizzati
Ogni agente deve avere input/output chiari.
Questo è fondamentale per evitare di ricadere nel monolite.

## 12.5 Postmortem come citizen di primo livello
Nel legacy il logging c’è.
Nel target il postmortem deve diventare parte strutturale del ciclo.

## 14. Conclusione netta

Il tuo vecchio progetto **ha già molte intuizioni corrette**, ma è cresciuto in modo ibrido:
mezzo framework, mezzo laboratorio di esperimenti.

Per portarlo nella direzione del TXT serve fare un salto di livello:

- da **bot ticker-based**
- a **platform prediction-market multi-agent**

La buona notizia è che il refactor non parte da zero:
hai già i semi giusti per intelligence, auditing, risk e operations.

La scelta architetturale corretta è:
**nuova codebase target ordinata + migrazione selettiva dei concetti validi del legacy**.
