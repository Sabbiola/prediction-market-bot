# Operations

## Scopo

Questa guida definisce come operare il sistema in beta paper-trading.
Focus:

- esecuzione controllata delle run
- verifica artefatti e logs
- gestione incidenti operativi
- condizioni di rollback

## Prerequisiti operativi

- ambiente Python 3.11+ funzionante
- dipendenze installate
- config valide:
  - `config/app.yaml`
  - `config/agents.yaml`
- guardrail live disattivata:
  - `app.mode: dry-run`
  - `venue.dry_run: true`
  - `feature_flags.allow_live_execution: false`

## Workflow operatore

## 1. Pre-run checklist

- confermare branch/versione runtime attesa
- confermare che i test sono verdi
- confermare che la directory dati e scrivibile
- verificare che non ci siano override config non tracciati

## 2. Esecuzione run

Comando standard:

```bash
python -m prediction_market_bot.main run --config config/app.yaml --agents-config config/agents.yaml
```

Il comando deve produrre:

- stage logs strutturati
- `PipelineSummary` nel log finale
- artefatti JSONL per ogni fase pipeline

## 3. Post-run checklist

- controllare `pipeline_end` e coerenza contatori (`executed`, `settled`, `wins`, `losses`, `skipped`)
- verificare presenza file JSONL attesi in artifacts
- verificare presenza audit events con `run_id`
- verificare che ogni market candidato abbia chain completa:
  - research -> prediction -> risk -> execution -> settlement -> postmortem

## Runbook incidenti

## Incident classes

- **P0**: guardrail safety violata (es. live flag attiva)
- **P1**: run fallita senza output auditabile
- **P2**: run completata ma con artefatti incompleti/incoerenti
- **P3**: warning non bloccanti (degrado qualita dati o segnali)

## Azioni immediate

- fermare nuove run se incidente P0/P1
- congelare modifiche config finche non e identificata la causa
- conservare logs e artifacts della run impattata
- aprire postmortem operativo con causa e azioni

## Condizioni di rollback

Eseguire rollback operativo della release/config se avviene almeno una condizione:

- runtime non resta in `dry-run`
- `allow_live_execution` risulta `true`
- crash ripetuti del pipeline coordinator
- assenza o corruzione di artifact JSONL critici
- mismatch sistematico tra summary e artefatti persistiti
- blocchi rischio non applicati correttamente
- regressione severa nella classificazione settlement/postmortem

## Azioni di rollback

1. fermare l'esecuzione (manuale o scheduler)
2. ripristinare configurazione nota-stabile
3. ripristinare versione applicativa precedente
4. rieseguire smoke run dry-run
5. riaprire il traffico beta solo dopo esito verde dei gate

## Gate operativi giornalieri

Prima di dichiarare il sistema "operativo" nel giorno corrente:

- ultimi test verdi
- ultima run dry-run completata con audit completo
- nessun incidente P0/P1 aperto
- backlog action items postmortem in stato gestibile

## KPI minimi da monitorare in beta

- `candidates_count`
- `approved_rate`
- `orders_submitted_total` (dry-run)
- `settled_count`
- `settled_pnl` (simulato)
- `postmortems_total`
- `pipeline_failures_total`
