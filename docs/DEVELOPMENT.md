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

## Alternative data adapter contract (news/social)

Contract capability-gated:

- `src/prediction_market_bot/infrastructure/alt_data/base.py`
- `src/prediction_market_bot/infrastructure/alt_data/capabilities.py`
- `src/prediction_market_bot/infrastructure/alt_data/models.py`
- `src/prediction_market_bot/infrastructure/alt_data/registry.py`

Config source registration:

- `config/app.yaml -> alt_data.enabled`
- `config/app.yaml -> alt_data.sources.<source_id>`

Capacita supportate per source:

- `requires_oauth`
- `requires_user_context`
- `supports_backfill`
- `supports_live_polling`
- `supports_search`
- `supports_thread_context_expansion`

Per aggiungere una nuova source adapter:

1. aggiungere entry config in `alt_data.sources` con capability esplicite
2. aggiungere adapter implementation (package source-specific)
3. registrare/validare tramite `AltDataAdapterRegistry`
4. coprire test:
   - registration config-driven
   - capability gating
   - errori credenziali chiari

Regole:

- nessun fail-open se sorgente abilitata richiede credenziali mancanti
- usare startup validation per bloccare configurazioni incoerenti
- non introdurre business logic prediction/risk nel layer adapter

## Offline historical data ingest (strategy research)

Subsystem dedicato:

- `src/prediction_market_bot/strategy_research/data_ingest/`

Comandi:

- `backfill-historical-markets`
- `inspect-dataset`
- `verify-dataset`

Obiettivo:

- costruire un data lake riproducibile dei mercati risolti
- separare layer raw e normalized
- mantenere training/research fuori dal runtime serving path

Config (`config/app.yaml`):

- `strategy_research.historical_data_ingest.base_dir`
- `strategy_research.historical_data_ingest.default_dataset_id`
- endpoint storici (`markets/events/snapshots/orderbook/trades/resolutions`)
- `page_size`, `max_pages_per_run`, `throttle_sec`
- `include_orderbook`, `include_trades`

## Offline research evidence corpus

Subsystem dedicato:

- `src/prediction_market_bot/strategy_research/research_corpus/`

Comandi:

- `backfill-research-evidence`
- `inspect-research-corpus`
- `verify-research-alignment`
- `build-labels`
- `run-benchmarks`
- `compare-benchmarks`
- `run-walk-forward`
- `generate-strategy-report`
- `train-baseline-models`
- `calibrate-model`
- `compare-models`
- `generate-model-card`

## Offline news corpus (Google News / RSS)

Subsystem dedicato:

- `src/prediction_market_bot/strategy_research/news_corpus/`
- adapter capability-gated: `src/prediction_market_bot/infrastructure/alt_data/adapters/rss_news_adapter.py`

Comandi:

- `backfill-news`
- `inspect-news-corpus`
- `verify-news-source`

Workflow locale consigliato:

1. abilitare `alt_data.enabled: true` e `alt_data.sources.news_rss_web.enabled: true`
2. verificare configurazione/endpoint:
   - `verify-news-source --topic WORLD --json`
3. eseguire backfill news:
   - `backfill-news --topic WORLD --keyword inflation --json`
4. ispezionare corpus:
   - `inspect-news-corpus --json`
5. verificare consistenza corpus:
   - `verify-news-source --json`

Config (`config/app.yaml`):

- `strategy_research.news_corpus.base_dir`
- `strategy_research.news_corpus.default_corpus_id`
- `strategy_research.news_corpus.source_id`
- `strategy_research.news_corpus.limit_per_query`
- `strategy_research.news_corpus.throttle_sec`
- `strategy_research.news_corpus.language`
- `strategy_research.news_corpus.region`

## Offline Reddit corpus (OAuth)

Subsystem dedicato:

- `src/prediction_market_bot/strategy_research/reddit_corpus/`
- adapter capability-gated: `src/prediction_market_bot/infrastructure/alt_data/adapters/reddit_oauth_adapter.py`

Comandi:

- `backfill-reddit`
- `inspect-reddit-corpus`
- `verify-reddit-oauth`

## Offline LLM enrichment corpus

Subsystem dedicato:

- `src/prediction_market_bot/strategy_research/llm_enrichment/`

Comandi:

- `enrich-alt-data`
- `inspect-llm-enrichment`

Workflow locale consigliato:

1. costruire corpus e linkage (`backfill-news/reddit/x`, `build-linkage`)
2. abilitare `strategy_research.llm_enrichment.enabled: true`
3. eseguire enrichment:
   - `enrich-alt-data --json`
4. ispezionare output:
   - `inspect-llm-enrichment --json`

Note:

- default provider deterministico (`provider: deterministic`)
- provider esterni ammessi solo con `allow_external_provider: true`
- fallback deterministico configurabile (`enable_fallback`)

Workflow locale consigliato:

1. abilitare `alt_data.enabled: true` e `alt_data.sources.reddit.enabled: true`
2. configurare credenziale OAuth in env:
   - `REDDIT_ACCESS_TOKEN`
3. verificare OAuth/source:
   - `verify-reddit-oauth --subreddit worldnews --json`
4. eseguire backfill corpus:
   - `backfill-reddit --subreddit worldnews --keyword election --include-comments --json`
5. ispezionare corpus:
   - `inspect-reddit-corpus --json`

Config (`config/app.yaml`):

- `strategy_research.reddit_corpus.base_dir`
- `strategy_research.reddit_corpus.default_corpus_id`
- `strategy_research.reddit_corpus.source_id`
- `strategy_research.reddit_corpus.limit_per_query`
- `strategy_research.reddit_corpus.max_pages_per_query`
- `strategy_research.reddit_corpus.include_comments`
- `strategy_research.reddit_corpus.comment_limit_per_post`
- `strategy_research.reddit_corpus.throttle_sec`

## Offline X corpus (optional, capability-gated)

Subsystem dedicato:

- `src/prediction_market_bot/strategy_research/x_corpus/`
- adapter capability-gated: `src/prediction_market_bot/infrastructure/alt_data/adapters/x_api_adapter.py`

Comandi:

- `backfill-x`
- `inspect-x-corpus`
- `verify-x-source`

Workflow locale consigliato:

1. abilitare `alt_data.enabled: true` e `alt_data.sources.x.enabled: true`
2. configurare credenziale OAuth in env:
   - `X_BEARER_TOKEN`
3. scegliere auth mode in `strategy_research.x_corpus.auth_mode`:
   - `bearer` per query keyword/search
   - `user_context` per query account quando capability/endpoint lo consentono
4. verificare source/capability:
   - `verify-x-source --keyword macro --json`
5. eseguire backfill corpus:
   - `backfill-x --keyword macro --account analyst --json`
6. ispezionare corpus:
   - `inspect-x-corpus --json`

Config (`config/app.yaml`):

- `strategy_research.x_corpus.base_dir`
- `strategy_research.x_corpus.default_corpus_id`
- `strategy_research.x_corpus.source_id`
- `strategy_research.x_corpus.limit_per_query`
- `strategy_research.x_corpus.max_pages_per_query`
- `strategy_research.x_corpus.auth_mode`
- `strategy_research.x_corpus.search_endpoint_path`
- `strategy_research.x_corpus.user_lookup_endpoint_path_template`
- `strategy_research.x_corpus.user_posts_endpoint_path_template`
- `strategy_research.x_corpus.throttle_sec`

Workflow locale consigliato:

1. costruire/aggiornare prima il dataset mercati storico (`backfill-historical-markets`)
2. eseguire backfill del corpus evidenze:
   - `backfill-research-evidence --source-dataset-id <dataset_id> --json`
3. ispezionare conteggi/layout:
   - `inspect-research-corpus --json`
4. verificare allineamento temporale/provenance:
   - `verify-research-alignment --json`
5. generare labels leakage-safe:
   - `build-labels --dataset-id <dataset_id> --json`
6. eseguire benchmark baseline:
   - `run-benchmarks --dataset-id <dataset_id> --split-mode holdout --json`
7. confrontare benchmark run:
   - `compare-benchmarks --dataset-id <dataset_id> --json`
8. eseguire simulazione strategica walk-forward leakage-safe:
   - `run-walk-forward --dataset-id <dataset_id> --baseline-name heuristic_prediction_agent --eval-split test --json`
9. generare report markdown del run walk-forward:
   - `generate-strategy-report --dataset-id <dataset_id> --run-id <walk_run_id>`
10. training offline baseline:
   - `train-baseline-models --dataset-id <dataset_id> --json`
11. confronto modelli:
   - `compare-models --dataset-id <dataset_id> --split test --json`
12. calibrazione probabilita:
   - `calibrate-model --dataset-id <dataset_id> --run-id <train_run_id> --model-name logistic_regression_baseline --method platt --json`
13. generazione model card:
   - `generate-model-card --dataset-id <dataset_id> --run-id <train_run_id> --model-name logistic_regression_baseline`

Config (`config/app.yaml`):

- `strategy_research.research_corpus.base_dir`
- `strategy_research.research_corpus.default_corpus_id`
- `strategy_research.research_corpus.source_dataset_id`
- `strategy_research.research_corpus.enabled_sources`
- `strategy_research.research_corpus.limit_per_source`
- `strategy_research.research_corpus.throttle_sec`
- `strategy_research.research_corpus.require_published_at`
- `strategy_research.research_corpus.drop_unaligned`

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
python -m prediction_market_bot.main backfill-historical-markets --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main inspect-dataset --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-dataset --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main backfill-research-evidence --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main inspect-research-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-research-alignment --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main backfill-news --config config/app.yaml --agents-config config/agents.yaml --topic WORLD --keyword inflation --json
python -m prediction_market_bot.main inspect-news-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-news-source --config config/app.yaml --agents-config config/agents.yaml --topic WORLD --json
python -m prediction_market_bot.main backfill-reddit --config config/app.yaml --agents-config config/agents.yaml --subreddit worldnews --include-comments --json
python -m prediction_market_bot.main inspect-reddit-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-reddit-oauth --config config/app.yaml --agents-config config/agents.yaml --subreddit worldnews --json
python -m prediction_market_bot.main backfill-x --config config/app.yaml --agents-config config/agents.yaml --keyword macro --json
python -m prediction_market_bot.main inspect-x-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-x-source --config config/app.yaml --agents-config config/agents.yaml --keyword macro --json
python -m prediction_market_bot.main build-labels --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --json
python -m prediction_market_bot.main run-benchmarks --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --split-mode holdout --json
python -m prediction_market_bot.main compare-benchmarks --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --json
python -m prediction_market_bot.main run-walk-forward --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --baseline-name heuristic_prediction_agent --eval-split test --json
python -m prediction_market_bot.main generate-strategy-report --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --run-id <walk_run_id>
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
python -m prediction_market_bot.main backfill-historical-markets --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main inspect-dataset --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-dataset --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main backfill-research-evidence --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main inspect-research-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-research-alignment --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main backfill-news --config config/app.yaml --agents-config config/agents.yaml --topic WORLD --keyword inflation --json
python -m prediction_market_bot.main inspect-news-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-news-source --config config/app.yaml --agents-config config/agents.yaml --topic WORLD --json
python -m prediction_market_bot.main backfill-reddit --config config/app.yaml --agents-config config/agents.yaml --subreddit worldnews --include-comments --json
python -m prediction_market_bot.main inspect-reddit-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-reddit-oauth --config config/app.yaml --agents-config config/agents.yaml --subreddit worldnews --json
python -m prediction_market_bot.main backfill-x --config config/app.yaml --agents-config config/agents.yaml --keyword macro --json
python -m prediction_market_bot.main inspect-x-corpus --config config/app.yaml --agents-config config/agents.yaml --json
python -m prediction_market_bot.main verify-x-source --config config/app.yaml --agents-config config/agents.yaml --keyword macro --json
python -m prediction_market_bot.main build-labels --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --json
python -m prediction_market_bot.main run-benchmarks --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --split-mode holdout --json
python -m prediction_market_bot.main compare-benchmarks --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --json
python -m prediction_market_bot.main run-walk-forward --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --baseline-name heuristic_prediction_agent --eval-split test --json
python -m prediction_market_bot.main generate-strategy-report --config config/app.yaml --agents-config config/agents.yaml --dataset-id historical-markets --run-id <walk_run_id>
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
