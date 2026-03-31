# Incident Response Runbook

Operational runbook for the Prediction Market Bot. Follow these procedures under pressure — every step is actionable.

---

## 1. Emergency Procedures

### 1.1 Pause All Trading (Immediate)

**CLI (preferred):**
```bash
python -m prediction_market_bot.main pause \
  --config config/app.yaml --agents-config config/agents.yaml \
  --reason "incident: <brief description>"
```

**Docker:**
```bash
docker exec prediction-market-worker python -m prediction_market_bot.main pause \
  --config config/app.yaml --agents-config config/agents.yaml \
  --reason "incident: <brief description>"
```

**Verify:** `python -m prediction_market_bot.main status --config config/app.yaml --agents-config config/agents.yaml`

### 1.2 Global Circuit Breaker

The circuit breaker auto-triggers on daily stop-loss breach. To manually activate:

```bash
python -m prediction_market_bot.main pause \
  --config config/app.yaml --agents-config config/agents.yaml \
  --reason "circuit_breaker: manual activation"
```

### 1.3 Full Kill Switch

```bash
# Stop all containers
docker compose down

# Or kill processes directly
pkill -f "prediction_market_bot.main run-scheduler"
pkill -f "prediction_market_bot.ui.server"
```

---

## 2. Common Incidents

### 2.1 Pipeline Failure

**Symptoms:** Healthcheck fails, no new pipeline_summaries artifacts.

**Diagnosis:**
```bash
# Check recent run status
python -m prediction_market_bot.main status --config config/app.yaml --agents-config config/agents.yaml

# Check logs
tail -100 data/logs/scheduler.err.log

# Replay last run for details
python -m prediction_market_bot.main replay-run --config config/app.yaml --agents-config config/agents.yaml --run-id <run_id>
```

**Recovery:**
1. Fix the root cause (config, provider, data issue)
2. Run a single iteration to verify: `python -m prediction_market_bot.main run-once --config config/app.yaml --agents-config config/agents.yaml`
3. Resume scheduler if paused

### 2.2 Stuck Transaction

**Symptoms:** TX in PENDING state for > 10 minutes.

**Diagnosis:**
```bash
python -m prediction_market_bot.main tx-status \
  --config config/app.yaml --agents-config config/agents.yaml \
  --json
```

**Recovery:**
```bash
# Force reconciliation
python -m prediction_market_bot.main tx-reconcile \
  --config config/app.yaml --agents-config config/agents.yaml \
  --json
```

If TX is permanently stuck, pause trading and investigate RPC provider health.

### 2.3 High Review Queue Backlog

**Symptoms:** Many PENDING_REVIEW items accumulating.

**Diagnosis:**
```bash
python -m prediction_market_bot.main review-list \
  --config config/app.yaml --agents-config config/agents.yaml \
  --status pending_review --json
```

**Recovery:** Process items via UI or CLI `review-approve` / `review-reject`. If backlog is due to operator absence, consider pausing until coverage is restored.

### 2.4 Database Corruption

**Symptoms:** SQLite integrity check fails, application errors on DB reads.

**Recovery:**
```bash
# 1. Pause immediately
python -m prediction_market_bot.main pause --config config/app.yaml --agents-config config/agents.yaml --reason "db_corruption"

# 2. Verify corruption
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml

# 3. Restore from backup
python -m prediction_market_bot.main restore-backup \
  --config config/app.yaml --agents-config config/agents.yaml \
  --backup-path data/backups/<latest>.db

# 4. Verify restored DB
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml

# 5. Resume
python -m prediction_market_bot.main resume --config config/app.yaml --agents-config config/agents.yaml
```

### 2.5 UI Unresponsive

**Diagnosis:**
```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/ready
```

**Recovery:**
```bash
# Docker
docker restart prediction-market-ui

# Or direct process restart
pkill -f "prediction_market_bot.ui.server"
python -m prediction_market_bot.ui.server \
  --config config/app.yaml --agents-config config/agents.yaml \
  --host 0.0.0.0 --port 8080 &
```

### 2.6 Provider API Failure

**Symptoms:** Healthcheck shows provider errors, stale data events increasing.

**Diagnosis:** Check `/metrics` endpoint for `pm_bot_stale_data_events_total` and `pm_bot_live_source_failures_total`.

**Recovery:** If provider is down externally, the system will use cached data. For extended outages, pause trading to prevent stale-data decisions.

---

## 3. Monitoring & Alerting

### 3.1 Key Metrics (GET /metrics)

| Metric | Alert Threshold | Meaning |
|--------|----------------|---------|
| `pm_bot_pipeline_failures_total` | Any increase | Pipeline run failed |
| `pm_bot_review_queue_depth` | > 10 | Review backlog growing |
| `pm_bot_tx_pending_count` | > 0 for > 10m | Stuck transactions |
| `pm_bot_tx_failed_count` | Any increase | TX failures |
| `pm_bot_stale_data_events_total` | Sustained increase | Data freshness issues |
| `pm_bot_live_source_failures_total` | > 3 in 5m | Provider API failing |

### 3.2 Health Endpoints

| Endpoint | Expected | Meaning |
|----------|----------|---------|
| `GET /health` | 200 | App is alive |
| `GET /ready` | 200 | App can serve traffic |
| `GET /metrics` | 200 | Prometheus metrics |

### 3.3 Log Locations

| Log | Path |
|-----|------|
| Scheduler stdout | `data/logs/scheduler.out.log` |
| Scheduler stderr | `data/logs/scheduler.err.log` |
| UI stdout | `data/logs/ui.out.log` |
| UI stderr | `data/logs/ui.err.log` |
| Pipeline artifacts | `data/artifacts/` (JSONL) |

---

## 4. Severity Levels & Escalation

| Level | Description | Response Time | Authority |
|-------|-------------|---------------|-----------|
| **P1** | Trading loss, stuck live TX, data corruption | < 15 min | Admin — pause immediately |
| **P2** | Pipeline failures, provider outage, review backlog | < 1 hour | Operator — investigate & fix |
| **P3** | Degraded metrics, slow responses, config drift | < 4 hours | Operator — schedule fix |
| **P4** | Cosmetic UI issues, non-critical warnings | Next business day | Any team member |

**P1 immediate actions:** Pause trading, notify team, begin investigation.

---

## 5. Recovery Procedures

### 5.1 Backup & Restore

```bash
# Create backup
python -m prediction_market_bot.main create-backup \
  --config config/app.yaml --agents-config config/agents.yaml

# List backups
ls -la data/backups/

# Restore
python -m prediction_market_bot.main restore-backup \
  --config config/app.yaml --agents-config config/agents.yaml \
  --backup-path data/backups/<file>.db
```

### 5.2 Config Rollback

Config is in `config/app.yaml` — use git to revert:
```bash
git diff config/app.yaml
git checkout HEAD -- config/app.yaml
```

### 5.3 Full Restart Sequence

```bash
# 1. Pause
python -m prediction_market_bot.main pause --config config/app.yaml --agents-config config/agents.yaml --reason "restart"

# 2. Stop services
docker compose down

# 3. Create backup
python -m prediction_market_bot.main create-backup --config config/app.yaml --agents-config config/agents.yaml

# 4. Verify DB
python -m prediction_market_bot.main db-verify --config config/app.yaml --agents-config config/agents.yaml

# 5. Start services
docker compose up -d

# 6. Verify health
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/ready

# 7. Resume
python -m prediction_market_bot.main resume --config config/app.yaml --agents-config config/agents.yaml
```

---

## 6. Post-Incident

### 6.1 Evidence Collection

```bash
# Export pipeline artifacts for the affected run
python -m prediction_market_bot.main replay-run \
  --config config/app.yaml --agents-config config/agents.yaml \
  --run-id <run_id>

# Generate report
python -m prediction_market_bot.main generate-report \
  --config config/app.yaml --agents-config config/agents.yaml \
  --run-id <run_id>

# Collect TX history
python -m prediction_market_bot.main tx-status \
  --config config/app.yaml --agents-config config/agents.yaml \
  --run-id <run_id> --json > evidence/tx_status.json
```

### 6.2 Post-Mortem Template

```
## Incident Post-Mortem: [Title]

**Date:** YYYY-MM-DD
**Severity:** P1/P2/P3/P4
**Duration:** HH:MM
**Affected run IDs:** [list]

### Timeline
- HH:MM — Incident detected
- HH:MM — Trading paused
- HH:MM — Root cause identified
- HH:MM — Fix applied
- HH:MM — Trading resumed

### Root Cause
[Description]

### Impact
- Trades affected: N
- Financial impact: $X
- Data loss: Y/N

### Resolution
[What was done to fix it]

### Action Items
- [ ] Preventive measure 1
- [ ] Preventive measure 2
```

### 6.3 Timeline Reconstruction

Pipeline artifacts in `data/artifacts/` are append-only JSONL files with timestamps. Use `replay-run` to reconstruct the exact sequence of events for any run ID.
