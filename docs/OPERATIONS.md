# OPERATIONS (Beta-Live Staging)

## Obiettivo

Questa guida descrive l'operativita in ambiente **beta-live staging**:

- provider live in sola lettura
- execution venue reale sempre disabilitata
- sandbox-chain usata come lane transazionale non-trading
- audit JSONL + stato operativo SQLite

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

Ruoli:

- `viewer`: sola lettura (tab/health/status)
- `operator`: puo fare review actions, `run-once`, `tx-reconcile`
- `admin`: puo fare tutto l'operator + `pause/resume` + `admin-settings`

Regole:

- tutte le azioni mutanti richiedono sessione autenticata e ruolo valido
- ogni azione UI mutante viene auditata con `acting_user` e `acting_role`
- non esporre mai segreti o credenziali in template/log

## Comandi operativi principali

```bash
python -m prediction_market_bot.main validate-startup --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main healthcheck --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main run-scheduler --config config/app.yaml --agents-config config/agents.yaml --interval-sec 60
python -m prediction_market_bot.main status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-reconcile --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-resubmit-safe --config config/app.yaml --agents-config config/agents.yaml --intent-id <intent_id> --json
python -m prediction_market_bot.ui.server --config config/app.yaml --agents-config config/agents.yaml --host 127.0.0.1 --port 8080
docker compose up -d --build prediction-market-worker prediction-market-ui
```

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

1. Eseguire `validate-startup`.
2. Eseguire `healthcheck`.
3. Verificare:
   - guardrail dry-run valide
   - path storage scrivibili (`data/`)
   - connessione Operational DB disponibile
   - config sandbox-chain coerente (`submit_tx`, signer/unlocked sender, rpc_url, contract_address)

Se `validate-startup` fallisce, non avviare scheduler.

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
