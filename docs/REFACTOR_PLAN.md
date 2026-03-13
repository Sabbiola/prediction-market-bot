# Refactor Plan

## 11. Piano di refactoring raccomandato

## Fase 0 — Congelamento e classificazione

Obiettivo:
definire cosa è legacy runtime, cosa è research, cosa va archiviato.

Task:
- dichiarare `neurosynth` come **legacy reference**
- spostare script root in gruppi chiari:
  - `research/backtests`
  - `research/training`
  - `research/optimization`
  - `ops/legacy`
- fissare un file `LEGACY_TO_TARGET_MAP.md`

Output:
- nessuna modifica funzionale
- ordine concettuale immediato

## Fase 1 — Foundation del nuovo progetto

Obiettivo:
creare la nuova base pulita.

Task:
- nuovo package `prediction_market_bot`
- `pyproject.toml`
- `AGENTS.md`
- `config/agents.yaml`
- domain models prediction-market
- interfacce/ports

Output:
- nuovo scheletro installabile e leggibile

## Fase 2 — Scanner e Research

Obiettivo:
implementare la prima metà del flusso del TXT.

Task:
- `ScanAgent`
- adapters per mercati
- `ResearchAgent`
- source adapters Twitter/Reddit/RSS/official
- scoring credibilità e sintesi

Output:
- candidati mercato con dossier narrativo

## Fase 3 — Prediction Engine

Obiettivo:
calibrare fair probability vs market odds.

Task:
- feature store per market snapshots
- modello XGBoost offline
- calibrator online
- fusione ML + narrative
- confidence & edge thresholds

Output:
- `PredictionResult` ripetibile e auditabile

## Fase 4 — Risk / Execution / Settlement

Obiettivo:
passare da “signal” a “trade lifecycle”.

Task:
- bankroll manager
- fractional Kelly
- exposure rules
- executor adapter
- fill reconciliation
- settlement monitor

Output:
- ordini disciplinati e tracciabili

## Fase 5 — Postmortem loop

Obiettivo:
chiudere il ciclo di apprendimento.

Task:
- schema postmortem
- classificazione cause loss
- memorizzazione action items
- hook per backlog dataset/risk/features

Output:
- apprendimento sistematico, non solo logging

## Fase 6 — API / UI / Ops

Obiettivo:
dare una control plane seria.

Task:
- API separate da UI
- process supervision seria
- metrics
- health checks
- scheduler / worker separation

Output:
- progetto operabile in modo pulito
