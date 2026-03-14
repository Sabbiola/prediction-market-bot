# Development

Linee guida operative minime per lo sviluppo del progetto.

## Workflow

- usare `src/` come source root
- mantenere il runtime in modalita dry-run/paper-live come percorso sempre testabile
- aggiungere test insieme a ogni nuovo agente o adapter

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

## UI Auth locale (staging-like)

Per provare auth/RBAC della control plane:

1. Abilitare `ui_auth.enabled: true` in `config/app.yaml`.
2. Configurare secret sessione:
   - bash: `export PM_BOT_UI_SESSION_SECRET='<secret>'`
   - PowerShell: `$env:PM_BOT_UI_SESSION_SECRET='<secret>'`
3. Configurare password utenti via env:
   - `PM_BOT_UI_VIEWER_PASSWORD`
   - `PM_BOT_UI_OPERATOR_PASSWORD`
   - `PM_BOT_UI_ADMIN_PASSWORD`
4. Avviare `python -m prediction_market_bot.ui.server ...` e usare `/login`.

## Riferimenti

- [BETA_SCOPE.md](BETA_SCOPE.md)
- [OPERATIONS.md](OPERATIONS.md)
