# ── Stage 1: build ──
FROM python:3.11.9-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ── Stage 2: runtime ──
FROM python:3.11.9-slim-bookworm AS runtime

LABEL org.opencontainers.image.source="https://github.com/your-org/prediction-market-bot" \
      org.opencontainers.image.description="Prediction Market Bot"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY config ./config

RUN useradd --create-home --shell /bin/bash botuser \
    && mkdir -p /app/data \
    && chown -R botuser:botuser /app

USER botuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
  CMD python -m prediction_market_bot.main healthcheck --config config/app.yaml --agents-config config/agents.yaml --json || exit 1

CMD ["python", "-m", "prediction_market_bot.main", "run-scheduler", "--config", "config/app.yaml", "--agents-config", "config/agents.yaml", "--interval-sec", "60"]
