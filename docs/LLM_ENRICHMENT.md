# LLM Enrichment Policy

## Scopo

Definire cosa e consentito e cosa e vietato nell'uso di LLM per arricchimento research, mantenendo separazione netta tra sperimentazione e runtime operativo.

## Principio base

LLM e un layer di arricchimento, non un sostituto dei guardrail quantitativi/risk.

- default: path deterministico non-LLM
- LLM attivabile solo con feature flag/config esplicite
- nessuna dipendenza runtime hard da chiamate LLM esterne

## Task LLM consentiti

Task permessi (offline o shadow, salvo promozione esplicita):

- estrazione strutturata da testo (`entities`, `claims`, `stance`)
- sintesi evidence multi-source con citazioni provenance-aware
- classificazione topic/event relevance
- scoring contraddizione/coerenza tra fonti
- quality tagging (`low_signal`, `ambiguous_claim`, `duplicate_story`)

## Task LLM vietati

Sempre vietati:

- decidere direttamente `approved/rejected` trade
- bypassare `RiskAgent` o review queue
- auto-tuning online di soglie/model parameters
- usare LLM output senza provenance verso source records
- usare conoscenza post-resolution per arricchire esempi pre-decisione
- qualunque promessa o logica orientata a "profitto garantito"

## Contratti output LLM

Ogni output LLM deve includere:

- `llm_model_id`
- `prompt_version`
- `enrichment_task`
- `input_record_ids`
- `output_schema_version`
- `created_at_utc`
- `determinism_mode` (`strict`/`best_effort`)
- `safety_flags`

Se manca il contratto, output non utilizzabile in feature pipeline.

## Determinismo e riproducibilita

Per uso research valutabile:

- temperatura bassa/controllata
- prompt versionato
- schema output validato
- fallback deterministic parser quando possibile

Per promoted runtime:

- output LLM ammesso solo se trasformato in feature stabili/versionate
- parity checks obbligatori contro feature contract runtime

## Livelli di utilizzo consentiti

1. **Offline only**
   - tutti i task consentiti
   - incluso confronto modelli/prompt
2. **Shadow mode**
   - enrichment visibile in report/comparison
   - execution invariata
3. **Promoted runtime**
   - solo task/feature passati da gate formale
   - fallback deterministico obbligatorio

## Requisiti di valutazione

Prima di usare LLM signals oltre offline:

- quality eval task-specific (precision/recall o agreement proxy)
- ablation vs baseline non-LLM su split out-of-time
- analisi costo/latenza/robustezza
- failure modes documentati (`hallucination`, `missing context`, `prompt drift`)

Per promoted runtime:

- evidenza in `MODEL_PROMOTION` (benchmark + calibration + approval-rate sanity)
- drift monitoring dedicato su coverage/consistency enrichment
- rollback plan esplicito e testato

## Sicurezza e compliance operativa

- mai loggare segreti/token/prompt sensibili con dati identificativi non necessari
- redaction obbligatoria su payload e alert
- usare provider/API ufficiali e policy d'uso conformi

## Implementazione corrente (research only)

Bounded context offline dedicato:

- `src/prediction_market_bot/strategy_research/llm_enrichment/`
  - `provider.py`: interfaccia provider + provider deterministico + external stub
  - `service.py`: pipeline enrichment con fallback e checkpoint
  - `models.py`: schema normalizzato output + validazione
  - `storage.py`: persistenza raw/normalized + manifest

CLI disponibili:

- `enrich-alt-data`
- `inspect-llm-enrichment`

Layout artefatti per `enrichment_id`:

```text
<base_dir>/<enrichment_id>/
  raw/
    prompt_requests.jsonl
    provider_outputs.jsonl
  normalized/
    enrichment_records.jsonl
  checkpoints/
    llm_enrichment_checkpoint.json
  llm_enrichment_manifest.json
```

Regole operative correnti:

- default fail-closed (`strategy_research.llm_enrichment.enabled: false`)
- default provider deterministico (`provider: deterministic`)
- provider esterno ammesso solo con `allow_external_provider: true`
- fallback deterministico configurabile (`enable_fallback`)
- nessun impatto sul runtime execution path (`run-once`, review/risk/execution invariati)
