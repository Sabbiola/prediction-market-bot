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
      cli/
        app.py
        parser.py
        commands/
          run_commands.py
          report_commands.py
          replay_commands.py
          review_commands.py
          tx_commands.py
          ops_commands.py
          health_commands.py
      services/
        history/
          run_summary_service.py
          replay_service.py
          evaluation_service.py
          report_service.py
          history_queries.py
      strategy_research/
        data_ingest/
          provider.py
          service.py
          normalizer.py
          storage.py
        linkage/
          models.py
          service.py
          storage.py
        features/
          schema.py
          service.py
          storage.py
          models.py
        alt_features/
          schema.py
          service.py
          storage.py
          models.py
        simulator/
          models.py
          service.py
      main.py
  tests/
    test_pipeline_smoke.py
```

### CLI layer

- `main.py` e un entrypoint sottile di backward compatibility.
- parser e dispatch vivono in `src/prediction_market_bot/cli/`:
  - `parser.py`: solo definizione argparse (nomi comandi/flag).
  - `app.py`: delega dal comando al modulo handler dedicato.
  - `commands/*.py`: handler per concern (`run`, `review`, `tx`, `ops`, `health`, `report`, `replay`).
- vincolo: nessuna logica di dominio dentro argparse o routing CLI.

### Performance observability path

- profiling runtime/CLI e opzionale (flag `--profile` su comandi selezionati).
- i costi stage-level principali vengono esposti come:
  - `pipeline_summaries.stage_timings_ms`
  - evento `slow_stage_detected` (guardrail soglia)
  - eventi adapter live con durata (`live_market_fetch_end`, `research_ingestion_*`)
  - timing replay/evaluation in osservabilita history (`analysis_timings_ms` + log timing dedicati)
- la UI espone hint operativi per polling (`X-Request-Duration-Ms`, `X-Poll-Suggested-Interval-Ms`) e usa cache short-TTL lato read-model per endpoint ad alta frequenza.

### Operational DB migrations

- schema versioning gestito da `infrastructure/operational_migrations.py`
- baseline + migrazioni incrementali applicate in ordine deterministico
- tabella `schema_migrations` come source of truth versione schema
- comandi runtime:
  - `db-init`
  - `db-upgrade`
  - `db-current-version`

## 9. Bounded contexts del nuovo progetto

### History / Reporting subsystem

- `services/history/run_summary_service.py`: dataclass e contratti tipizzati (`ReplaySummary`, `WindowEvaluation`, metriche).
- `services/history/replay_service.py`: ricostruzione deterministica run da artefatti JSONL.
- `services/history/evaluation_service.py`: aggregazione cross-run per finestre temporali.
- `services/history/report_service.py`: rendering markdown e write su filesystem.
- `services/history/history_queries.py`: query read-only (`list_run_ids`) separate dal rendering.
- `services/run_history.py`: shim di compatibilita per import legacy.

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
- emissione bundle feature research deterministico multi-dimensione

Boundary implementativo ingestion live research:

- `infrastructure/research/adapters/*`: trasporto + parsing source-specific (Wikipedia/OpenAlex, estendibile).
- `infrastructure/research/normalizer.py`: normalizzazione deterministica verso `ResearchFinding`.
- `infrastructure/research/cache.py`: policy condivisa retry/cache/http fetch.
- `infrastructure/research/pipeline.py`: orchestrazione ingestione, deduplica, persistenza artefatti/eventi.
- `infrastructure/live_research.py`: shim di compatibilita per import legacy.
- `services/research_features.py`: feature engineering shared runtime/offline (relevance, decay, priors, conflict, diversity, alignment, novelty).

### 9.3 Probabilistic Modeling
Responsabile di:

- features quantitative del mercato
- features narrative
- fair probability
- edge vs market
- confidence

Boundary serving runtime (Prediction Engine v2):

- `agents/prediction.py` resta entrypoint unico del `PredictionAgent`.
- path default: engine euristico (backward-compatible).
- path `shadow_scoring`: euristico primario + modello v2 side-by-side (solo osservabilita, execution invariata).
- path opzionale `model_v2`: inference da artifact offline versionato.
- contract esplicito validato a runtime:
  - `feature_schema_version`
  - `model_version`
  - `calibration_version`
  - feature parity (`required_features` vs feature runtime-safe)
- mismatch contratto/parity:
  - fail chiaro (`PredictionModelArtifactError`) se fallback disabilitato
  - fallback euristico se `fallback_to_heuristic=true`
  - in `shadow_scoring` mismatch sempre visibile via warning/artifact (`prediction_parity_warnings`, `prediction_shadow_comparisons`)
- nessun training online nel runtime; il runtime carica solo artifact promossi.

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

### 9.8 Strategy Research Offline Data Lake
Responsabile di:

- ingestione storica dei mercati risolti per ricerca offline
- persistenza separata raw/normalized con checkpoint resumable
- verifica coerenza dataset per training/evaluation
- generazione label leakage-safe da mercati risolti
- benchmark baseline temporali e confronti run-to-run
- generazione feature dataset versionati:
  - `features/*` per segnali market+research
  - `alt_features/*` per segnali news/social/LLM leakage-safe

Non responsabile di:

- serving runtime in `run-once`/scheduler
- decisioni di prediction/risk/execution nel loop operativo
- tuning online o modifica dinamica threshold in produzione

Boundary:

- package dedicato `src/prediction_market_bot/strategy_research/data_ingest/`
- package dedicato `src/prediction_market_bot/strategy_research/research_corpus/`
- package dedicato `src/prediction_market_bot/strategy_research/linkage/`
- package dedicato `src/prediction_market_bot/strategy_research/benchmarking/`
- package dedicato `src/prediction_market_bot/strategy_research/features/`
- package dedicato `src/prediction_market_bot/strategy_research/simulator/`
- package dedicato `src/prediction_market_bot/strategy_research/training/`
- comandi CLI dedicati (`backfill-historical-markets`, `inspect-dataset`, `verify-dataset`)
- comandi CLI dedicati corpus evidenze (`backfill-research-evidence`, `inspect-research-corpus`, `verify-research-alignment`)
- comandi CLI dedicati linkage evidenze (`build-linkage`, `inspect-linkage`, `verify-linkage-quality`)
- comandi CLI dedicati labels/benchmark (`build-labels`, `run-benchmarks`, `compare-benchmarks`)
- comandi CLI dedicati feature store (`build-feature-dataset`, `inspect-feature-schema`, `verify-feature-parity`)
- comandi CLI dedicati simulazione strategica (`run-walk-forward`, `generate-strategy-report`)
- comandi CLI dedicati training/calibration (`train-baseline-models`, `calibrate-model`, `compare-models`, `generate-model-card`)
- configurazione dedicata in `strategy_research.historical_data_ingest`
- configurazione dedicata in `strategy_research.research_corpus`
- configurazione dedicata in `strategy_research.linkage`

### 9.9 Alternative Data Ingestion (News/Social)
Responsabile di:

- ingestione e normalizzazione classi sorgente `news_rss_web`, `reddit`, `x`
- deduplica/provenance/timestamp alignment leakage-safe
- quality gating del segnale prima dell'uso modellistico

Boundary:

- ingestion rumorosa fuori da `PredictionAgent` e fuori da policy `RiskAgent`
- contract adapter capability-gated in `infrastructure/alt_data/`:
  - `base.py`
  - `capabilities.py`
  - `models.py`
  - `registry.py`
- adapter source-specific in infrastructure/research (o package dedicato alt-data), policy e schema in `strategy_research/*`
- capability enable/disable guidata da ambiente + credenziali (no fail-open silenzioso)

Comportamento di bootstrap/startup:

- registrazione sorgenti guidata da config (`alt_data.sources`)
- validazione credenziali e capability delle sorgenti abilitate
- failure esplicito in startup validation se una sorgente abilitata non e pronta

Riferimento policy:

- `docs/ALT_DATA_RESEARCH.md`
- `docs/NEWS_SOCIAL_SOURCE_POLICY.md`

### 9.9.1 Event/Entity/Market Linker (offline)

Responsabile di:

- collegare evidenze alt-data a `event_id`/`market_id` con regole deterministic-first
- applicare similarity + alias resolution + time-window constraints
- preservare traceability con score breakdown e reason code

Boundary:

- vive in `strategy_research/linkage/` (offline, fuori dal runtime decision loop)
- persiste stati espliciti: `linked`, `ambiguous`, `unresolved`, `stale_evidence`
- non forza linkage quando la confidenza e insufficiente o ambigua
- output usato da dataset/feature engineering, non da execution live diretto

### 9.10 LLM Enrichment Layer
Responsabile di:

- trasformare testo/evidenze in segnali strutturati (stance, relevance, contradiction, novelty)
- mantenere contratti output versionati e provenance-aware
- supportare ricerca offline e shadow analysis senza cambiare behavior execution

Non responsabile di:

- decisioni di approvazione trade
- override di review/risk guardrails
- training o auto-tuning online nel runtime

Boundary:

- default path deterministico non-LLM
- feature LLM usabili in runtime solo dopo promotion gate e parity checks
- fallback deterministico obbligatorio per evitare dipendenza hard da provider LLM
- implementazione offline corrente:
  - `strategy_research/llm_enrichment/provider.py`
  - `strategy_research/llm_enrichment/service.py`
  - `strategy_research/llm_enrichment/storage.py`
  - `strategy_research/llm_enrichment/models.py`
- comandi CLI:
  - `enrich-alt-data`
  - `inspect-llm-enrichment`

Riferimento policy:

- `docs/LLM_ENRICHMENT.md`

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
