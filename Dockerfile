FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY docs ./docs

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

RUN useradd --create-home --shell /bin/bash botuser \
    && mkdir -p /app/data \
    && chown -R botuser:botuser /app

USER botuser

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
  CMD python -m prediction_market_bot.main healthcheck --config config/app.yaml --agents-config config/agents.yaml --json || exit 1

CMD ["python", "-m", "prediction_market_bot.main", "run-scheduler", "--config", "config/app.yaml", "--agents-config", "config/agents.yaml", "--interval-sec", "60"]
