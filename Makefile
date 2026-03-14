RUN_ID ?= local-smoke-run

.PHONY: install lint typecheck test test-all beta-acceptance run smoke-dry-run ci-quality ci-smoke ci-beta-acceptance ci format

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

format:
	ruff format src tests

typecheck:
	mypy src

test:
	pytest -q -m "not acceptance"

test-all:
	pytest -q

beta-acceptance:
	pytest -q -m acceptance tests/acceptance

run:
	python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml

smoke-dry-run:
	python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml --run-id $(RUN_ID)

ci-quality: lint typecheck test

ci-smoke: smoke-dry-run

ci-beta-acceptance: beta-acceptance

ci: ci-quality ci-smoke ci-beta-acceptance
