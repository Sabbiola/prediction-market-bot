# OPERATIONS (Beta-Live Staging)

## Obiettivo

Questa guida descrive l'operativita in ambiente **beta-live staging**:

- provider live in sola lettura
- execution venue reale sempre disabilitata
- sandbox-chain usata come lane transazionale non-trading
- audit JSONL + stato operativo SQLite/Postgres

Nota backend Operational DB:

- `sqlite` resta valido per locale/dev e scenari lightweight
- `postgres` e il backend raccomandato per staging condiviso beta-live

## Operator Control Plane / Web UI (intended scope)

Responsabilita previste:

- aggregare la vista operativa su health, run status, review queue, open positions, pending settlements, tx states
- esporre widget di monitoraggio live (`live_source_failures`, `review_queue_depth`, `pending_settlements`, `tx_*`, `open_positions`, `stale_data_*`)
- esporre banner incident/status con raccomandazioni operatore
- esporre feed incident/events da audit log lato server (nessun parsing log nel browser)
- rendere accessibili le azioni operatore gia presenti nel runtime (`pause/resume`, review actions, tx reconcile/resubmit-safe)
- fornire una vista auditabile delle decisioni (`run_id`, rationale modello/operatore, stati tx)
- esporre shell tabbed con pannelli iniziali `Overview` e `System/Health`

Non-responsabilita:

- nessuna logica di business nel layer UI
- nessun calcolo di prediction/risk/execution/settlement nel frontend
- nessun posting diretto di ordini live su venue

Vincoli operativi:

- la source of truth resta il runtime backend e la sua persistenza operativa
- `SANDBOX_CHAIN` resta la lane transazionale reale di rehearsal
- venue live order posting resta disabilitata
- worker (`prediction-market-worker`) e UI (`prediction-market-ui`) sono processi separati: la UI non e prerequisito runtime

## UI Auth e RBAC (staging)

Autenticazione:

- session-based auth configurata in `config/app.yaml` (`ui_auth`)
- cookie di sessione con secure defaults (`cookie_secure`, `cookie_samesite`)
- timeout inattivita controllato da `ui_auth.session_timeout_sec`
- logout esplicito via UI/API (`POST /logout` o `POST /api/auth/logout`)
- per staging condiviso usare password hash PBKDF2 (`ui_auth.require_password_hashes=true`)
- lockout tentativi falliti (`ui_auth.max_failed_attempts`, `ui_auth.lockout_seconds`)

Ruoli:

- `viewer`: sola lettura (tab/health/status)
- `operator`: puo fare review actions, `run-once`, `tx-reconcile`
- `admin`: puo fare tutto l'operator + `pause/resume` + `admin-settings`

Regole:

- tutte le azioni mutanti richiedono sessione autenticata e ruolo valido
- ogni azione UI mutante viene auditata con `acting_user` e `acting_role`
- non esporre mai segreti o credenziali in template/log

## Secret resolution (staging)

- tutti i secret runtime/UI passano dal provider centralizzato `security.secrets`
- backend supportati:
  - `env` (default)
  - `command` (integrazione con secret manager tramite command template)
- fallback controllato su env (`security.secrets.env_fallback`)
- i secret non devono essere stampati in log, payload UI o artifact audit

## Alert routing (staging)

Configurazione minima:

- `alerting.enabled: true`
- `alerting.webhook.enabled: true`
- `alerting.webhook.webhook_url` oppure `alerting.webhook.webhook_url_env`
- `alerting.dedupe_window_sec` per limitare duplicati/alert storm

Eventi actionable alertati:

- `startup_validation_failure`
- `healthcheck_failure`
- `db_migration_mismatch`
- `repeated_live_source_failures`
- `tx_reconciliation_failure`
- `pipeline_critical_failure`

Aspettativa operativa:

- un alert indica azione operatore richiesta, non semplice telemetria
- usare metriche + `status` + UI incidents feed per triage e conferma
- payload alert redatto (no secret, no private key, no token)

## Outbound network policy

- policy HTTP centralizzata in `http` config:
  - `timeout_sec`, `max_retries`, `retry_backoff_sec`, `retry_jitter_sec`
  - `enforce_allowed_hosts`, `allowed_hosts`
  - `max_response_bytes`
- con `enforce_allowed_hosts=true`, host fuori allowlist vengono bloccati fail-closed
- usare allowlist esplicita in staging (provider live + endpoint sandbox necessari)

## Performance profiling e troubleshooting

Obiettivo operativo:

- identificare stage lenti prima che diventino colli di bottiglia in beta-live
- distinguere latenza provider esterni da costi CPU/query locali

Strumentazione disponibile:

- pipeline stage timings in `pipeline_summaries.stage_timings_ms`
- evento `slow_stage_detected` quando uno stage supera `observability.performance.slow_stage_threshold_ms`
- live market fetch con timing:
  - evento `live_market_fetch_end` (`fetch_duration_ms`, `total_duration_ms`, `cache_hit`)
- live research ingestion con timing:
  - evento `research_ingestion_source_end` (`duration_ms` per source)
  - evento `research_ingestion_end` (`source_total_duration_ms`, `ingestion_duration_ms`)
- replay/evaluation:
  - timing query in log `history_evaluate_window_timing`
  - `replay_run` include `observability.analysis_timings_ms`

Profiling mode CLI (on-demand, low-overhead):

```bash
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --profile
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --profile
python -m prediction_market_bot.main evaluate-window --config config/app.yaml --agents-config config/agents.yaml --date-from 2026-03-01 --date-to 2026-03-14 --profile
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --profile
python -m prediction_market_bot.main generate-eval-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --profile
```

Nota:

- il sommario profiling va su `stderr` (stdout resta pulito per output JSON/automation)
- configurazione globale opzionale: `observability.performance.enable_cli_profile`

## UI polling: rischi e mitigazioni

Endpoint ad alto polling tipico:

- `GET /api/tabs/overview`
- `GET /api/tabs/system`
- `GET /api/incidents`

Mitigazioni lato server:

- cache read-model short TTL (`observability.performance.ui_poll_cache_ttl_sec`)
- hint di polling (`X-Poll-Suggested-Interval-Ms`)
- timing header (`X-Request-Duration-Ms`)
- `Cache-Control: private, max-age=<ttl>`

Raccomandazioni staging:

- non scendere sotto 2s di polling per tab globali (overview/system/incidents)
- usare refresh event-driven (azione operatore) per tab ad alta volatilita (review/tx)
- se compaiono `ui_slow_request` nei log, aumentare intervallo polling e verificare query/repository hot path

## RBAC CLI privilegiato (opzionale, consigliato in staging condiviso)

- abilitare `security.cli_auth.enabled=true`
- identita attore da env:
  - `security.cli_auth.actor_user_env` (default `PM_BOT_ACTOR_USER`)
  - `security.cli_auth.actor_role_env` (default `PM_BOT_ACTOR_ROLE`)
- enforcement ruoli:
  - `pause`, `resume`, `db-restore`, `tx-resubmit-safe` => `admin`
  - `review-approve`, `review-reject`, `tx-reconcile` => `operator`/`admin`

## Comandi operativi principali

```bash
python -m prediction_market_bot.main validate-startup --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main healthcheck --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-current-version --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-init --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-upgrade --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-backup --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-restore --config config/app.yaml --agents-config config/agents.yaml --backup-file <backup.sqlite3> --force --json
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main run-scheduler --config config/app.yaml --agents-config config/agents.yaml --interval-sec 60
python -m prediction_market_bot.main status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-reconcile --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-resubmit-safe --config config/app.yaml --agents-config config/agents.yaml --intent-id <intent_id> --json
python -m prediction_market_bot.main evaluate-model-promotion --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main model-promotion-status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main promote-model-v2 --config config/app.yaml --agents-config config/agents.yaml --rationale "<reason>" --json
python -m prediction_market_bot.main rollback-model-v2 --config config/app.yaml --agents-config config/agents.yaml --rationale "<incident reason>" --json
python -m prediction_market_bot.main clear-model-v2-rollback --config config/app.yaml --agents-config config/agents.yaml --rationale "<close incident>" --json
python -m prediction_market_bot.main drift-status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main record-threshold-tuning --config config/app.yaml --agents-config config/agents.yaml --rationale "<why>" --ticket "<chg-id>" --json
python -m prediction_market_bot.main generate-shadow-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --output data/artifacts/reports/<run_id>.shadow.md
python -m prediction_market_bot.ui.server --config config/app.yaml --agents-config config/agents.yaml --host 127.0.0.1 --port 8080
docker compose up -d --build prediction-market-worker prediction-market-ui
docker compose --profile staging-postgres up -d postgres-staging
```

## Model promotion, drift e rollback runbook

Gate runtime:

- `model_v2` puo sostituire euristico solo con promozione esplicita in operator state
- per mode `PAPER_LIVE`/`SANDBOX_CHAIN` il gate e bloccante (se configurazione default)
- mismatch versione artifact/promozione o rollback attivo bloccano il path v2
- path `alt+LLM promoted`:
  - attivabile solo quando `model_v2` e consentito dal gate
  - attivabile solo in `SANDBOX_CHAIN`
  - richiede `agents.prediction.model_inference.alt_shadow.promoted_enabled=true`
  - se coverage/enrichment/capability non sono disponibili, fallback esplicito a baseline `model_v2` con reason code auditabile

Procedura promozione consigliata:

1. `evaluate-model-promotion` e verificare `passed=true`.
2. Allegare output evaluation + shadow report + walk-forward al change record.
3. `promote-model-v2 --rationale "..."`
4. Verificare con `model-promotion-status` che:
   - gate decision sia `model_v2_allowed`
   - versione promoted allineata a artifact attuale.
5. Se si usa il path enriched:
   - verificare in UI tab `Prediction`:
     - `effective_engine=model_v2_alt_promoted`
     - `active_source_set`
     - `enrichment_coverage`
     - `disagreement_vs_baseline`

Procedura rollback (incident):

1. `rollback-model-v2 --rationale "..."`
2. Confermare fallback euristico con `model-promotion-status` / `status`.
3. Eseguire `drift-status` e raccogliere segnali.
4. Dopo analisi incident, `clear-model-v2-rollback` solo con approvazione.

Monitoraggio drift:

- eseguire `drift-status` periodicamente (o schedulato in automation)
- trattare `critical` come evento operatore actionable
- usare i signal detail per distinguere:
  - shift feature input
  - regime market
  - degrado coverage research
  - collasso confidence/approval

Threshold tuning (disciplinato):

- mai tuning implicito nel runtime
- registrare ogni proposta con `record-threshold-tuning`
- includere ticket, rationale, valori correnti e proposti
- applicare modifiche solo via config versionata dopo evidence review

## Runbook Sandbox-Live v2 (dress rehearsal)

Obiettivo:

- verificare che `model_v2` operi in `SANDBOX_CHAIN` senza indebolire review/risk guardrail
- produrre evidenza ripetibile per il gate finale beta-live

Procedura raccomandata:

1. Preparazione
   - configurare runtime `SANDBOX_CHAIN`
   - verificare artifact model/calibration v2 disponibili
2. Promozione controllata
   - `promote-model-v2 --rationale "..."`
   - `model-promotion-status --json` e confermare:
     - `gate_decision.reason=model_v2_allowed`
     - `gate_decision.effective_engine=model_v2`
3. Startup checks
   - `validate-startup`
   - `healthcheck --json`
4. Rehearsal run end-to-end
   - primo `run-once` (atteso: review queue `PENDING_REVIEW`)
   - `review-approve` con rationale
   - secondo `run-once` (atteso: submit sandbox tx)
   - `tx-status --json`
   - `tx-reconcile --json`
5. Lifecycle e reporting
   - `paper-portfolio-state --json`
   - `run-settlement-lane --run-id <run_id>`
   - `replay-run --run-id <run_id>`
   - `generate-report --run-id <run_id>`
6. Verifica UI control-plane
   - tab `Overview`: model attivo, gate reason, drift alert
   - tab `Prediction`: model version, calibration summary, shadow/disagreement/approval summary

Criteri PASS:

- nessuna esecuzione senza review `APPROVED`
- tx sandbox tracciata e riconciliata
- replay/report generabili da artifact persistiti
- model visibility esplicita e coerente in UI

Nota:

- venue live order posting resta disabilitata
- `PAPER_LIVE` resta path shadow/guarded; attivazione promoted v2 limitata a `SANDBOX_CHAIN` salvo override config esplicito

## Shadow scoring operativo (Prediction Engine v2)

Modalita consigliata pre-promotion:

1. configurare `agents.prediction.model_inference.engine=shadow_scoring`
2. mantenere artifact v2 valorizzato (`model_artifact_path`, opzionale `calibration_artifact_path`)
3. eseguire run normali (`run-once` / scheduler) con execution invariata
4. generare report di confronto:

```bash
python -m prediction_market_bot.main generate-shadow-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-shadow-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --json
```

Output atteso:

- confronto euristico vs v2 persistito in `prediction_shadow_comparisons.jsonl`
- confronto opzionale euristico vs `alt_llm_shadow` persistito nello stesso artifact quando `model_inference.alt_shadow.enabled=true`
- warning parity visibili in `prediction_parity_warnings.jsonl` e audit event `prediction_parity_warning`
- warning espliciti attesi per path alt+LLM:
  - `missing_source_coverage`
  - `enrichment_pipeline_failure`
  - mismatch schema/feature contract
- metriche confronto:
  - prediction delta
  - confidence delta
  - approval-rate delta
  - disagreement buckets
  - delta calibrazione/edge (v2 e alt+LLM, quando disponibili)

Note implementative (report/replay/eval):

- i comandi `replay-run`, `evaluate-window`, `generate-report`, `generate-eval-report` usano il sottosistema `services/history/`
- query run/history e rendering report sono separati per supportare integrazione UI/API senza accoppiamento a logica CLI
- `services/run_history.py` resta solo come shim di backward compatibility
- lo schema Operational DB e migration-driven (`schema_migrations`) e non dipende da create table impliciti nei repository

## Backup e Disaster Recovery (Operational DB SQLite)

Configurazione:

- `storage.operational_db.path`: path DB operativo
- `storage.operational_db.backup_dir`: directory snapshot immutable (`.sqlite3`)

Comandi:

```bash
python -m prediction_market_bot.main db-backup --config config/app.yaml --agents-config config/agents.yaml --label daily --json
python -m prediction_market_bot.main db-restore --config config/app.yaml --agents-config config/agents.yaml --backup-file <backup.sqlite3> --force --json
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml --json
```

Regole operative:

- ogni backup e uno snapshot timestamped immutabile
- il restore non sovrascrive DB esistente senza `--force`
- dopo restore eseguire sempre `db-verify` (integrity + schema migration status)

Cadence consigliata (staging beta-live):

- backup ogni 6 ore durante operativita attiva
- backup pre-deploy e post-deploy
- backup immediato prima di `db-upgrade`
- retention minima 7 giorni (meglio 14 in staging)

Procedura restore rapida:

1. `pause` del runtime worker.
2. Identificare backup target (`storage.operational_db.backup_dir`).
3. Eseguire `db-restore ... --force`.
4. Eseguire `db-verify --json`.
5. Eseguire `validate-startup` + `healthcheck`.
6. `resume` solo con esito verde.

Endpoint UI principali:

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/session`
- `GET /api/incidents`
- `GET /api/tabs/overview`
- `GET /api/tabs/scanner`
- `GET /api/tabs/research`
- `GET /api/tabs/prediction`
- `GET /api/tabs/risk`
- `GET /api/tabs/review-queue`
- `GET /api/tabs/execution`
- `GET /api/tabs/settlement`
- `GET /api/tabs/sandbox-tx`
- `GET /api/tabs/reports`
- `GET /api/tabs/system`
- `POST /api/actions/run-once`
- `POST /api/actions/pause`
- `POST /api/actions/resume`
- `POST /api/actions/review-approve`
- `POST /api/actions/review-reject`
- `POST /api/actions/tx-reconcile`
- `POST /api/actions/tx-resubmit-safe`
- `POST /api/actions/admin-settings`

## Runbook

## 1. Startup

1. Eseguire `db-current-version`.
2. Se `up_to_date=false`, eseguire `db-upgrade`.
3. Eseguire `validate-startup`.
4. Eseguire `healthcheck`.
5. Verificare:
   - guardrail dry-run valide
   - path storage scrivibili (`data/`)
   - connessione Operational DB disponibile
   - config sandbox-chain coerente (`submit_tx`, signer/unlocked sender, rpc_url, contract_address)

Se `db-upgrade` o `validate-startup` falliscono, non avviare scheduler.

## 1.b Config staging Postgres

Prerequisiti:

- dipendenza `psycopg` installata (`pip install ".[postgres]"`)
- DSN configurato in `storage.operational_db.dsn` o via `storage.operational_db.dsn_env`

Esempio DSN:

- `postgresql://pm_bot:pm_bot@127.0.0.1:5432/prediction_market_bot`

Check attesi da `validate-startup` in modalita postgres:

- `operational_db_config=ok`
- `operational_schema_version=ok`
- `operational_repository_connectivity=ok`

## 2. Avvio scheduler

1. Avviare:
   - `run-scheduler` in foreground, oppure
   - container Docker/compose (`prediction-market-worker`).
2. Verificare da `status`:
   - `paused=false`
   - `pending_review_count`
   - `pending_settlement_count`
   - `runtime_metrics`

## 2.b Avvio UI control plane

1. Avviare `prediction-market-ui` (compose o processo dedicato).
2. Verificare:
   - `GET /health` restituisce `status=ok`
   - `GET /ready` restituisce `ready=true`
3. Se `ui_auth.enabled=true`, verificare login su `/login` con ruolo corretto.

## 3. Operativita continua

1. Controllare periodicamente `status --json`.
2. Controllare in UI i banner incident e il feed `api/incidents`.
3. Monitorare metriche prom textfile in `observability.metrics.path`.
4. Monitorare review queue:
   - `review-list --status pending_review`
5. Per sandbox tx:
   - `tx-status`
   - `tx-reconcile`
   - `tx-resubmit-safe` solo se stato non pending/mined.

## 4. Graceful shutdown

In `run-scheduler` sono gestiti `SIGINT`/`SIGTERM`:

- il processo completa il ciclo corrente
- salva stato scheduler
- termina senza interrompere transizioni a meta

## Failure modes operativi

## Config/dependency startup

- `dry_run_guardrail_failed`
- `operational_repository_connectivity_failed`
- `sandbox_chain.rpc_url_missing`
- `sandbox_chain.contract_address_missing`
- `no_private_key_or_unlocked_sender_configured_for_submit_tx`

Azione: bloccare avvio, correggere config/segreti, rilanciare `validate-startup`.

## Provider live/research

- `live_market_fetch_failed`
- `research_ingestion_source_failed`
- aumento `pm_bot_live_source_failures_total`

Azione: verificare endpoint/API key, applicare fallback policy o pausa operativa.

## Prediction shadow parity

- `prediction_parity_warning`
- `prediction_parity_warnings` artifact non vuoto
- report shadow senza `model_v2_prediction` disponibile
- report shadow con `alt_llm_shadow_status` non `available` quando la lane alt+LLM e abilitata

Azione:

1. verificare `feature_schema_version` atteso vs artifact promoted.
2. verificare `required_features` nel modello rispetto al bundle runtime-safe.
3. se abilitato `alt_shadow`, verificare coverage sorgenti richieste (`news/reddit/x`) e `f_alt_enrichment_coverage`.
4. rigenerare artifact offline o riallineare contract prima di qualsiasi promotion decision.

## Review gate

- coda review in crescita (`pm_bot_review_queue_depth`)
- trade bloccati in `manual_review_pending`

Azione: smaltire coda con `review-approve/review-reject`.

## Settlement

- `pm_bot_pending_settlements` in crescita anomala

Azione: eseguire lane settlement (`settle-run` / `run-settlement-lane`) e verificare resolver.

## Sandbox tx

- tx `PENDING` oltre soglia
- tx `DROPPED`/`REPLACED`/`FAILED`

Azione:

1. `tx-reconcile`
2. se safe: `tx-resubmit-safe --intent-id <id>`
3. verificare tracciabilita `run_id` + `review_queue_id`.

## Rollback e procedura pause

## Pause immediata

```bash
python -m prediction_market_bot.main pause --config config/app.yaml --agents-config config/agents.yaml --reason "incident_<id>"
```

## Resume controllato

```bash
python -m prediction_market_bot.main resume --config config/app.yaml --agents-config config/agents.yaml
```

## Rollback operativo

1. Pausare il sistema.
2. Tornare a config/versione nota stabile.
3. Rieseguire:
   - `validate-startup`
   - `healthcheck`
   - smoke `run-once`
4. Riprendere scheduler solo con esito verde.

## Workflow giornaliero operatore

1. `validate-startup`
2. `healthcheck --json`
3. `status --json`
4. gestione review queue pendente
5. `tx-status` + `tx-reconcile`
6. verifica report ultimo run (`last-report`)
7. chiusura turno: valutare `pause` se manutenzione o anomalia

## Log handling

- log strutturati su stdout
- rotazione opzionale file via `observability.logs`:
  - `file_path`
  - `rotate_max_bytes`
  - `rotate_backup_count`
- retention consigliata in staging: 7-14 giorni
