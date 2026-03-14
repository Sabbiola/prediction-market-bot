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
```

Su PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main settle-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
```

## Riferimenti

- [BETA_SCOPE.md](BETA_SCOPE.md)
- [OPERATIONS.md](OPERATIONS.md)
