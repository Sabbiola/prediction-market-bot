# OPERATIONS (Beta-Live Staging)

## Obiettivo

Questa guida descrive l'operativita in ambiente **beta-live staging**:

- provider live in sola lettura
- execution venue reale sempre disabilitata
- sandbox-chain usata come lane transazionale non-trading
- audit JSONL + stato operativo SQLite

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
```

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
   - container Docker/compose.
2. Verificare da `status`:
   - `paused=false`
   - `pending_review_count`
   - `pending_settlement_count`
   - `runtime_metrics`

## 3. Operativita continua

1. Controllare periodicamente `status --json`.
2. Monitorare metriche prom textfile in `observability.metrics.path`.
3. Monitorare review queue:
   - `review-list --status pending_review`
4. Per sandbox tx:
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
