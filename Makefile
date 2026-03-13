RUN_ID ?= local-smoke-run

.PHONY: install lint typecheck test run smoke-dry-run ci-quality ci-smoke ci format

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

format:
	ruff format src tests

typecheck:
	mypy src

test:
	pytest -q

run:
	python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml

smoke-dry-run:
	python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --run-id $(RUN_ID)

ci-quality: lint typecheck test

ci-smoke: smoke-dry-run

ci: ci-quality ci-smoke
