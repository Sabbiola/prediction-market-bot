# Development

Linee guida operative minime per lo sviluppo del progetto.

## Workflow

- usare `src/` come source root
- mantenere il runtime in modalita dry-run/paper-live come percorso sempre testabile
- aggiungere test insieme a ogni nuovo agente o adapter

## Strato CLI

Il runtime CLI e organizzato in moduli dedicati:

- `src/prediction_market_bot/main.py`: entrypoint sottile + backward compatibility import
- `src/prediction_market_bot/cli/parser.py`: solo argparse
- `src/prediction_market_bot/cli/app.py`: dispatch comandi
- `src/prediction_market_bot/cli/commands/`:
  - `run_commands.py`
  - `report_commands.py`
  - `replay_commands.py`
  - `review_commands.py`
  - `tx_commands.py`
  - `ops_commands.py`
  - `health_commands.py`

Regole:

- non mettere business logic dentro setup parser
- evitare dipendenze circolari tra command module
- mantenere command names/flag backward-compatible

## Live Research adapters

Struttura ingestion live research:

- `src/prediction_market_bot/infrastructure/research/adapters/`: moduli source-specific
- `src/prediction_market_bot/infrastructure/research/normalizer.py`: mapping deterministico a `ResearchFinding`
- `src/prediction_market_bot/infrastructure/research/pipeline.py`: orchestrazione ingestione/deduplica/persistenza
- `src/prediction_market_bot/infrastructure/live_research.py`: shim compatibile

Per aggiungere una nuova source:

1. creare un adapter in `adapters/<source>.py` estendendo `StructuredHttpResearchSource`
2. implementare solo:
   - `build_url`
   - `extract_records`
   - `normalize_record`
3. riusare `ResearchFindingNormalizer` per relevance/sentiment/confidence/provenance
4. registrare la source in `build_live_research_sources` (pipeline)
5. aggiungere test unitari adapter+pipeline (happy path, payload malformed, retry/cache)

Vincoli:

- niente logica di orchestrazione dentro adapter
- niente parsing raw in UI o agent layer
- preservare provenance (`source`, `endpoint`, `query`, `record_id`) in ogni finding

## Repository Hygiene

- non committare cache/tooling locali:
- `.mypy_cache/`
- `.pytest_cache/`
- `.ruff_cache/`
- `__pycache__/`
- `*.pyc`
- non committare output locali runtime:
- `data/artifacts/*`
- `data/audit/*`
- mantenere solo placeholder espliciti per directory dati:
- `data/artifacts/.gitkeep`
- `data/audit/.gitkeep`
- mantenere materiale legacy fuori dal runtime (`solana-memecoin-bot-main/` o `legacy_reference/`) e mai importarlo nel codice applicativo

## Comandi Base

```bash
python -m pytest -q
python -m prediction_market_bot.main db-current-version --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-upgrade --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-backup --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-restore --config config/app.yaml --agents-config config/agents.yaml --backup-file <backup.sqlite3> --force --json
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main settle-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.ui.server --config config/app.yaml --agents-config config/agents.yaml --host 127.0.0.1 --port 8080
python -m pytest -q tests/unit/test_ui_web_app.py
python -m pytest -q tests/integration/test_ui_auth_rbac_integration.py
python -m pytest -q tests/acceptance/test_beta_live_acceptance.py -k "ui_control_plane_path"
docker compose up -d --build prediction-market-worker prediction-market-ui
```

Su PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
python -m prediction_market_bot.main db-current-version --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-upgrade --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-backup --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main db-restore --config config/app.yaml --agents-config config/agents.yaml --backup-file <backup.sqlite3> --force --json
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main settle-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.ui.server --config config/app.yaml --agents-config config/agents.yaml --host 127.0.0.1 --port 8080
python -m pytest -q tests/unit/test_ui_web_app.py
python -m pytest -q tests/integration/test_ui_auth_rbac_integration.py
python -m pytest -q tests/acceptance/test_beta_live_acceptance.py -k "ui_control_plane_path"
docker compose up -d --build prediction-market-worker prediction-market-ui
```

## Operational DB migrations

Flow locale consigliato:

1. `db-current-version` per vedere `current/latest/pending`.
2. `db-upgrade` per applicare migrazioni pending.
3. `validate-startup` prima di `run-once` o scheduler.

Config (`config/app.yaml`):

- `storage.operational_db.driver`: attualmente `sqlite` (path verso supporto postgres)
- `storage.operational_db.backup_dir`: directory snapshot backup `.sqlite3`
- `storage.operational_db.dsn`: DSN postgres (usato quando `driver=postgres`)
- `storage.operational_db.dsn_env`: env var fallback DSN (default `OPERATIONAL_DB_DSN`)
- `storage.operational_db.auto_migrate_on_boot`: se `true`, applica migrazioni automaticamente al bootstrap repository
- `storage.operational_db.require_up_to_date`: se `true`, startup validation fallisce con schema non aggiornato

## Local Postgres staging-like

Setup rapido:

1. Installare dipendenza postgres:
   - `pip install ".[postgres]"`
2. Avviare Postgres locale:
   - `docker compose --profile staging-postgres up -d postgres-staging`
3. Configurare DSN:
   - bash: `export OPERATIONAL_DB_DSN="postgresql://pm_bot:pm_bot@127.0.0.1:5432/prediction_market_bot"`
   - PowerShell: `$env:OPERATIONAL_DB_DSN="postgresql://pm_bot:pm_bot@127.0.0.1:5432/prediction_market_bot"`
4. Impostare `storage.operational_db.driver: postgres` e usare `dsn` o `dsn_env`.
5. Eseguire:
   - `db-upgrade`
   - `validate-startup`
   - `healthcheck`

Nota: SQLite resta il default consigliato per sviluppo leggero locale.

## Local backup/restore workflow

Percorso rapido consigliato in locale:

1. Eseguire `run-once` su DB seedato.
2. Eseguire `db-backup --label local-test --json`.
3. Eseguire eventuali run/modifiche.
4. Ripristinare con `db-restore --backup-file <backup.sqlite3> --force --json`.
5. Validare con `db-verify --json`.
6. Rieseguire `validate-startup` prima di ripartire con scheduler.

## UI Auth locale (staging-like)

Per provare auth/RBAC della control plane:

1. Abilitare `ui_auth.enabled: true` in `config/app.yaml`.
2. Configurare secret sessione:
   - bash: `export PM_BOT_UI_SESSION_SECRET='<secret>'`
   - PowerShell: `$env:PM_BOT_UI_SESSION_SECRET='<secret>'`
3. Configurare hash password utenti via env:
   - `PM_BOT_UI_VIEWER_PASSWORD_HASH`
   - `PM_BOT_UI_OPERATOR_PASSWORD_HASH`
   - `PM_BOT_UI_ADMIN_PASSWORD_HASH`
4. Per staging-like forte, impostare `ui_auth.require_password_hashes: true`.
5. Avviare `python -m prediction_market_bot.ui.server ...` e usare `/login`.

Formato hash supportato:

- `pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>`
- usare almeno `150000` iterazioni

Snippet rapido per generare hash locale:

```bash
python - <<'PY'
import hashlib, os
password = "replace-me"
salt = os.urandom(16)
digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 150000)
print(f"pbkdf2_sha256$150000${salt.hex()}${digest.hex()}")
PY
```

## Secret provider e CLI RBAC locale

- secret provider default: `security.secrets.backend=env`
- opzionale secret-manager command backend: `security.secrets.backend=command`
- per testare RBAC CLI privilegiato:
  - `security.cli_auth.enabled: true`
  - `PM_BOT_ACTOR_USER=<user>`
  - `PM_BOT_ACTOR_ROLE=viewer|operator|admin`

## Alerting locale/staging-like

- per abilitare webhook alerting:
  - `alerting.enabled: true`
  - `alerting.webhook.enabled: true`
  - `alerting.webhook.webhook_url` oppure env `PM_BOT_ALERT_WEBHOOK_URL`
- per evitare rumore durante sviluppo:
  - mantenere `alerting.enabled: false` in locale default
  - usare `alerting.dedupe_window_sec` > 0 anche in staging-like test

## Outbound network hardening locale

- default dev: `http.enforce_allowed_hosts: false`
- staging-like consigliato:
  - `http.enforce_allowed_hosts: true`
  - compilare `http.allowed_hosts` con endpoint necessari
  - mantenere `http.max_response_bytes` su valore finito

## CI security checks

- workflow CI include:
  - `pip-audit` per vulnerabilita dipendenze
  - `bandit` per static security scan su `src/prediction_market_bot`

## Performance profiling workflow

Obiettivo: misurare prima di ottimizzare.

Commandi consigliati:

```bash
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --profile
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --profile
python -m prediction_market_bot.main evaluate-window --config config/app.yaml --agents-config config/agents.yaml --date-from 2026-03-01 --date-to 2026-03-14 --profile
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --profile
python -m prediction_market_bot.main generate-eval-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id> --profile
```

Note:

- profiling summary stampato su `stderr` per non sporcare stdout machine-readable
- per profiling persistente in ambiente locale/staging-like:
  - `observability.performance.enable_cli_profile: true`
- per individuare stage runtime lenti:
  - impostare `observability.performance.slow_stage_threshold_ms`
  - monitorare evento `slow_stage_detected`

## Riferimenti

- [BETA_SCOPE.md](BETA_SCOPE.md)
- [OPERATIONS.md](OPERATIONS.md)
