# Development

Linee guida operative minime per lo sviluppo del progetto.

## Workflow

- usare `src/` come source root
- mantenere il runtime in modalità dry-run come percorso sempre testabile
- aggiungere test insieme a ogni nuovo agente o adapter

## Comandi base

```bash
python -m pytest -q
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
```

Su PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
python -m prediction_market_bot.main generate-report --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
```

## Riferimenti

- [BETA_SCOPE.md](BETA_SCOPE.md)
- [OPERATIONS.md](OPERATIONS.md)
