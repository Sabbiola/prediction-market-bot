# Beta Scope

## Obiettivo beta

Questa beta definisce un rilascio **paper-trading only** per prediction markets.
Scopo della beta:

- validare la pipeline multi-agent end-to-end
- validare auditabilita e osservabilita operativa
- validare disciplina di rischio in ambiente non live
- raccogliere segnali per hardening prima di adapter reali

La beta non ha obiettivo di profitto live.

## In-scope

- pipeline completa con agenti:
  - `ScanAgent`
  - `ResearchAgent`
  - `PredictionAgent`
  - `RiskAgent`
  - `ExecutionAgent` (dry-run)
  - `SettlementAgent` (simulato)
  - `PostmortemAgent`
- comando CLI unico per run dry-run:
  - `python -m prediction_market_bot.main run --config config/app.yaml --agents-config config/agents.yaml`
- dependency injection nel coordinatore
- logging strutturato per ogni stage pipeline
- persistenza minima locale (JSONL) per:
  - market snapshots
  - research packets
  - prediction results
  - risk decisions
  - execution results
  - settlement results
  - postmortems
  - audit events
- output riassuntivo run (`PipelineSummary`)
- test:
  - unit
  - smoke
  - integration
- tipizzazione e validation strict (domain contracts)

## Out-of-scope

- trading live, gestione fondi reali, wallet signing, ordine su venue reale
- training online nel runtime
- auto-tuning in produzione
- dashboard operativa completa e control-plane UI
- scheduler 24/7 production-grade con HA
- multi-venue routing e smart order execution
- persistence production DB con migrazioni complete
- alerting enterprise (on-call paging, escalation automatica)

## Assunzioni operative

- runtime in `dry-run` obbligatorio
- `feature_flags.allow_live_execution` resta `false`
- nessun import runtime da codice legacy
- una sola source of truth config:
  - `config/app.yaml`
  - `config/agents.yaml`
- ogni run produce artefatti persistiti e correlabili con `run_id`
- operatori eseguono run manuali o schedule controllato, non autopilot live
- timezone e timestamp tracciati in UTC nei payload operativi

## Rischi principali

- gap tra comportamento simulato e venue reale (fill, slippage, latency)
- bias da sorgenti mock o incomplete
- overconfidence da metriche positive in ambiente sintetico
- configurazioni non allineate che degradano la decision quality
- degradazione architetturale se si bypassano ports/adapters
- rottura audit trail se la persistenza fallisce o e incompleta

## Release gates (Go/No-Go beta)

Una release beta e promuovibile solo se tutti i gate sono verdi:

1. **Qualita codice**
- test suite verde (`pytest`)
- type-check verde (`mypy`)
- nessuna regressione nota su orchestrazione end-to-end

2. **Integrita pipeline**
- run dry-run completa senza crash
- `PipelineSummary` coerente con gli artefatti prodotti
- stage logs presenti per tutti gli step chiave

3. **Audit e persistenza**
- scrittura JSONL riuscita per tutte le classi artefatto
- audit events persistiti con `run_id`
- assenza di payload critici mancanti nei record

4. **Safety guardrails**
- live execution disabilitata e verificata
- blocchi rischio attivi (edge/confidence/min bet/caps)
- postmortem prodotto per outcome non favorevoli

## Criteri di uscita dalla beta

La beta si considera conclusa quando:

- stabilita operativa dimostrata su una finestra continua di run dry-run
- rischio e explainability risultano coerenti su casi eterogenei
- backlog delle issue bloccanti beta e chiuso
- esiste piano approvato per introdurre adapter reali in fase successiva
