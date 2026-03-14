# Prediction Market Bot

Piattaforma multi-agent per prediction markets orientata a **paper-live**, **sandbox-chain** e **operator control plane**.

## Visione

L'obiettivo del progetto non e costruire un bot che spara ordini alla cieca.
L'obiettivo e costruire una **macchina disciplinata per cercare opportunita asimmetriche**, valutarle con segnali quantitativi e narrativi, filtrarlas con rischio rigoroso, farle passare da review umana e provarne l'esecuzione in ambiente sicuro.

In termini pratici:

- dati live veri
- ricerca live vera
- probabilita fair e edge spiegabili
- review umana obbligatoria
- execution paper o sandbox-chain
- zero venue live order posting nella fase attuale

## Missione operativa beta-live

Il progetto punta a costruire una control plane affidabile per operare in staging su prediction markets con:

- selezione disciplinata delle opportunita
- pricing e rationale spiegabili
- risk gating rigoroso
- review umana obbligatoria nelle modalita beta-live
- execution sicura (`PAPER` o `SANDBOX_CHAIN`) senza posting live su venue
- auditabilita completa end-to-end

Il sistema e **risk-first** e **operator-first**:
la priorita e ridurre errori operativi, mantenere guardrail attivi e migliorare la qualita decisionale nel tempo.

## Modalita operative

### 1. DRY_RUN_STATIC
Per sviluppo locale, test e verifica rapida della pipeline.

### 2. PAPER_LIVE
Usa market data e research live, ma l'esecuzione resta paper.
Questa e la modalita principale della beta-live.

### 3. SANDBOX_CHAIN
Usa dati live, review umana e **transazioni reali su sandbox/test harness**, senza fare ordini reali sul venue.
Serve a validare:

- signing
- nonce
- submit
- receipt
- retry/reconcile
- audit della lane transazionale

### 4. LIVE_DISABLED
Venue live execution volutamente non attiva.

## Principi architetturali

- nessuna business logic in UI
- domain separato da infrastructure
- provider esterni sempre dietro interfaces/adapters
- stato operativo separato da audit artifacts
- review umana bloccante nelle modalita beta-live
- settlement separato dal ciclo decisionale
- transazioni sandbox tracciate da `run_id` e review decision
- training offline, mai nel runtime operativo

## Pipeline del sistema

1. **Scanner**
   - filtra mercati per liquidita, spread, volume, tempo alla risoluzione
   - produce candidate markets

2. **Research**
   - raccoglie findings multi-source
   - stima evidence strength e disagreement
   - produce narrative summary

3. **Prediction**
   - combina probabilita implicita e segnali di ricerca
   - produce fair probability, edge, confidence e rationale

4. **Risk**
   - applica threshold, caps, Kelly frazionato e hard blocks
   - decide se il trade puo entrare in review

5. **Review Queue**
   - human-in-the-loop obbligatorio nelle modalita beta-live
   - approvazione o rifiuto operatore con rationale

6. **Execution**
   - paper execution oppure sandbox-chain lane
   - mai venue live order posting in questa fase

7. **Settlement Lane**
   - gestisce open positions e pending settlements in passaggio separato

8. **Replay / Reports / Evaluation**
   - ogni run e ricostruibile, spiegabile e verificabile

## Valore della control plane

L'obiettivo non e "fare piu trade", ma operare meglio:

- trasformare dati e ricerca in decisioni tracciabili
- bloccare automaticamente i casi che non superano il rischio
- mantenere separazione netta tra runtime e supervisione operatore
- usare replay/report/postmortem come leva di miglioramento continuo

## UI / Control Plane scope

La web UI operator-first espone una shell tabbed con read-model dedicati.
Ogni motore ha la sua scheda/tab:

- Overview
- Scanner
- Research
- Prediction
- Risk
- Review Queue
- Execution
- Settlement
- Sandbox TX
- Reports
- System / Health

La control plane include anche:

- widget live di monitoraggio operativo (source failures, queue depth, open positions, pending settlements, tx states, stale data counters)
- banner incident/status con linguaggio operatore
- incident/events feed costruito server-side da audit log JSONL
- pannello report con shortcut replay/evaluation CLI

La UI non contiene logica di business: e un piano di controllo e osservazione sopra il runtime.
La strategia e formalizzata in `docs/ADR_0001_OPERATOR_CONTROL_PLANE_WEB_UI.md`.

## Stato attuale

Il progetto e oggi in una beta-live tecnica avanzata:

- pipeline multi-agent presente
- persistence SQLite + JSONL presente
- review queue bloccante presente
- settlement lane separata presente
- sandbox-chain lane presente
- acceptance suite presente
- staging docs presenti
- web control-plane backend foundation presente (FastAPI + template server-side)

## Backend Operational DB

Supporto attuale:

- `sqlite`: default locale/dev, path file (`storage.operational_db.path`)
- `postgres`: staging/beta-live condiviso, DSN (`storage.operational_db.dsn` o `storage.operational_db.dsn_env`)

Guardrail:

- backend selezionato in `storage.operational_db.driver`
- fail-fast su config invalida (es. `driver=postgres` senza DSN)
- migrazioni gestite via `db-init`/`db-upgrade` su entrambi i backend

## Auth e Secret Management (staging condiviso)

- session auth e RBAC attivi sulla UI (`viewer`, `operator`, `admin`)
- supporto password hash `pbkdf2_sha256` per utenti UI (`ui_auth.require_password_hashes=true`)
- lockout su tentativi falliti (`ui_auth.max_failed_attempts`, `ui_auth.lockout_seconds`)
- provider segreti centralizzato (`security.secrets`) con backend:
  - `env` (default)
  - `command` (integrazione con secret manager/CLI esterna)
- azioni CLI privilegiate proteggibili con RBAC (`security.cli_auth.enabled=true`)
  - `pause`/`resume` richiedono `admin`
  - `review-approve`/`review-reject` richiedono `operator` o `admin`
  - `tx-resubmit-safe` e `db-restore` richiedono `admin`

## Alerting, Network Policy e CI Security

- alerting esterno configurabile via `alerting.webhook` (Slack/webhook compatibile)
- eventi critici alertabili: startup/healthcheck failure, schema mismatch, repeated live source failures, tx reconcile failure, pipeline critical failure
- dedupe anti-alert-storm via `alerting.dedupe_window_sec`
- policy outbound HTTP centralizzata:
  - timeout/retry bounded
  - host allowlist opzionale (`http.enforce_allowed_hosts`, `http.allowed_hosts`)
  - limite payload risposta (`http.max_response_bytes`)
- CI include scans dedicate:
  - dependency vulnerability scan (`pip-audit`)
  - static security scan (`bandit`)

## Profiling e performance guardrails

- profiling CLI opzionale (`--profile`) su:
  - `run-once`
  - `replay-run`
  - `evaluate-window`
  - `generate-report`
  - `generate-eval-report`
- guardrail stage lenti:
  - `observability.performance.slow_stage_threshold_ms`
  - evento `slow_stage_detected`
- UI polling hardening:
  - cache short-TTL lato read-model (`observability.performance.ui_poll_cache_ttl_sec`)
  - header `X-Request-Duration-Ms`
  - header `X-Poll-Suggested-Interval-Ms`

## Cosa manca per production-ready paper/sandbox

- hardening UI (grafici, incident console, UX operativa)
- auth enterprise (SSO/OIDC e provisioning centralizzato)
- refactor dei file molto grandi
- packaging/staging piu robusto
- migrazioni DB mature
- backup/recovery
- observability piu forte lato UI
- release gate che includa anche la control plane web

## Comandi principali

```bash
# startup / health
python -m prediction_market_bot.main db-current-version --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-init --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-upgrade --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-backup --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-restore --config config/app.yaml --agents-config config/agents.yaml --backup-file <backup.sqlite3> --force --json
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main validate-startup --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main healthcheck --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main status --config config/app.yaml --agents-config config/agents.yaml --json

# web control plane (foundation)
python -m prediction_market_bot.ui.server --config config/app.yaml --agents-config config/agents.yaml --host 127.0.0.1 --port 8080
# poi apri: /, /health, /ready
# auth API: /api/auth/login, /api/auth/logout, /api/auth/session
# incidents feed API: /api/incidents
# tab API read-only: /api/tabs/overview, /api/tabs/scanner, /api/tabs/research,
# /api/tabs/prediction, /api/tabs/risk, /api/tabs/review-queue, /api/tabs/execution,
# /api/tabs/settlement, /api/tabs/sandbox-tx, /api/tabs/reports, /api/tabs/system
# operator actions (POST): /api/actions/run-once, /api/actions/pause, /api/actions/resume,
# /api/actions/review-approve, /api/actions/review-reject, /api/actions/tx-reconcile,
# /api/actions/tx-resubmit-safe, /api/actions/admin-settings

# staging auth vars (se ui_auth.enabled=true)
export PM_BOT_UI_SESSION_SECRET='<long-random-secret>'
export PM_BOT_UI_VIEWER_PASSWORD_HASH='pbkdf2_sha256$150000$<salt_hex>$<digest_hex>'
export PM_BOT_UI_OPERATOR_PASSWORD_HASH='pbkdf2_sha256$150000$<salt_hex>$<digest_hex>'
export PM_BOT_UI_ADMIN_PASSWORD_HASH='pbkdf2_sha256$150000$<salt_hex>$<digest_hex>'

# run control
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main run-scheduler --config config/app.yaml --agents-config config/agents.yaml --interval-sec 60
python -m prediction_market_bot.main pause --config config/app.yaml --agents-config config/agents.yaml --reason "incident_<id>"
python -m prediction_market_bot.main resume --config config/app.yaml --agents-config config/agents.yaml

# review queue
python -m prediction_market_bot.main review-list --config config/app.yaml --agents-config config/agents.yaml --status pending_review --json
python -m prediction_market_bot.main review-show --config config/app.yaml --agents-config config/agents.yaml --queue-id <queue_id> --json
python -m prediction_market_bot.main review-approve --config config/app.yaml --agents-config config/agents.yaml --queue-id <queue_id> --operator-id <operator> --rationale "<rationale>"
python -m prediction_market_bot.main review-reject --config config/app.yaml --agents-config config/agents.yaml --queue-id <queue_id> --operator-id <operator> --rationale "<rationale>"

# settlement / report / replay
python -m prediction_market_bot.main run-settlement-lane --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main last-report --config config/app.yaml --agents-config config/agents.yaml --format markdown

# sandbox tx
python -m prediction_market_bot.main tx-status --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-reconcile --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main tx-resubmit-safe --config config/app.yaml --agents-config config/agents.yaml --intent-id <intent_id> --json
```

Setup Postgres (staging-like):

```bash
pip install ".[postgres]"
docker compose --profile staging-postgres up -d postgres-staging
export OPERATIONAL_DB_DSN="postgresql://pm_bot:pm_bot@127.0.0.1:5432/prediction_market_bot"
# su Windows PowerShell:
# $env:OPERATIONAL_DB_DSN="postgresql://pm_bot:pm_bot@127.0.0.1:5432/prediction_market_bot"
```

## Staging Docker (Worker + UI separati)

```bash
docker compose up -d --build prediction-market-worker prediction-market-ui
docker compose ps
docker compose logs -f prediction-market-worker
docker compose logs -f prediction-market-ui
```

Health/readiness UI:

```bash
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/ready
```

Nota operativa: il worker non dipende silenziosamente dalla UI; i due processi restano separati.

## Safety stance

- live venue execution disabilitata
- nessuna promessa di rendimento
- nessuna logica di bypass dei risk guardrails
- audit completo delle decisioni e delle azioni operatore
- sandbox-chain come rehearsal lane, non come trading lane

## Roadmap sintetica

1. UI docs + ADR
2. web backend foundation
3. overview + system tabs
4. tabs read-only per motore
5. review + tx actions in UI
6. auth / RBAC
7. monitoring e incident console
8. packaging staging + beta gate UI
9. refactor big files
10. deployment hardening

## In una frase

Il progetto punta a diventare una **piattaforma professionale per selezionare, validare e operare opportunita asimmetriche sui prediction markets con rischio controllato, audit totale e transizioni graduali da paper a sandbox prima di qualunque esposizione reale.**
