# Model Promotion Policy

## Scopo

Definire il percorso obbligatorio per promuovere un modello da research offline a shadow scoring in ambiente beta-live.

Obiettivo:

- migliorare qualita decisionale misurata
- senza indebolire safety/runtime guardrails

Per segnali alternativi news/social e LLM enrichment si applicano anche:

- `docs/ALT_DATA_RESEARCH.md`
- `docs/NEWS_SOCIAL_SOURCE_POLICY.md`
- `docs/LLM_ENRICHMENT.md`

## Stages di promozione

## Stage A - Research Candidate

Input minimo:

- dataset versionato con manifest
- feature spec congelata
- training config versionata
- report metriche offline
- model card compilata

Gate:

- riproducibilita completa (stesso commit + stessa config -> stessi risultati)
- nessun leakage noto

## Stage B - Offline Qualification

Valutazione su `test_out_of_time` leakage-safe.

Gate minimi:

- Brier migliore del benchmark `market_implied`
- Log loss migliore del benchmark `market_implied`
- calibration error entro soglia dichiarata
- report `realized edge vs expected edge` coerente
- walk-forward strategy simulation documentata (`run-walk-forward`) con:
  - ROI/PnL, max drawdown, turnover, approval rate
  - realized edge vs expected edge
  - calibration by cohort
- confronto esplicito contro baseline obbligatorie:
  - `market_implied`
  - `fifty_fifty`
  - `category_prior`
  - `heuristic_prediction_agent`
  - `research_only`
  - `momentum_structure`
- ablation obbligatoria per segnali alt-data/LLM:
  - `run-ablation-study` con varianti market/research/news/reddit/x/alt-without-llm/alt-with-llm
  - `compare-alt-data-variants` con riferimento `market_only_baseline` e delta espliciti
- split temporali senza random shuffle:
  - holdout `train/validation/test`, oppure
  - walk-forward leakage-safe
- training/calibration evidence obbligatoria:
  - output `train-baseline-models` con metriche split/window
  - output `compare-models` con ranking e delta metriche
  - output `calibrate-model` con curve pre/post e delta metriche
  - output `generate-model-card` compilato e versionato

Se un gate fallisce: stop promozione.

## Stage C - PAPER_LIVE Shadow Scoring

I modelli shadow producono score/rationale in runtime, ma non abilitano venue live.

Gate:

- nessuna regressione operativa su review/risk/execution
- approval rate entro range atteso (no collasso a 0 o saturazione)
- stabilita su finestra minima di run consecutive documentata
- shadow scoring obbligatorio side-by-side:
  - path primario runtime resta euristico (nessun cambio comportamento execution)
  - path secondario v2 viene calcolato nello stesso run e persistito come `prediction_shadow_comparisons`
  - path opzionale `alt+LLM shadow` (quando abilitato) viene calcolato nello stesso run e persistito nello stesso artifact
- parity check offline/online esplicito:
  - mismatch feature/schema produce warning visibile (`prediction_parity_warnings`)
  - warning espliciti richiesti per:
    - `missing_source_coverage`
    - `feature_schema_version_mismatch` / `missing_required_features`
    - `enrichment_pipeline_failure`
  - mismatch non deve essere silenzioso
- confronto evidence-based obbligatorio tramite report:
  - `generate-shadow-report` con:
    - prediction delta
    - confidence delta
    - approval-rate delta
    - disagreement buckets
    - delta calibrazione/edge disponibili per path v2 e alt+LLM shadow

## Stage D - SANDBOX_CHAIN Shadow Scoring

Il modello resta in percorso rehearsal:

- transazioni su sandbox
- nessun live venue order posting

Gate:

- nessuna regressione su tx lifecycle (`intent -> attempt -> receipt -> reconcile`)
- tracciabilita completa `run_id` + review decision
- ROI paper/sandbox riportato con fee/slippage assumptions

## Stage E - Promotion Decision

Decisione formale con evidenze:

- benchmark comparison
- metriche primarie complete
- model card aggiornata
- risk note e rollback plan

Output:

- artifact promoted versionato
- changelog promozione
- rollback trigger espliciti

## Metriche richieste in ogni review

- Brier score
- Log loss
- Calibration error
- Approval rate
- Realized edge vs expected edge
- Paper ROI (fee/slippage esplicite)
- Sandbox ROI (fee/slippage esplicite)

Evidence minima da allegare:

- output `build-labels` con manifest dataset
- output `run-benchmarks` (run id + aggregate split `test`)
- output `compare-benchmarks` contro ultimo modello promoted o baseline candidata
- output `run-ablation-study` con tabella varianti e metriche per split
- output `compare-alt-data-variants` con delta vs riferimento
- output `run-walk-forward` con payload completo metriche/assunzioni
- output `generate-strategy-report` per review operativa
- output `train-baseline-models` (run id + best model + feature importance + metriche per split/window)
- output `compare-models` (ranking su split test leakage-safe)
- output `calibrate-model` (metodo usato + brier/log loss pre/post + curve calibrazione)
- output `generate-model-card` (metadata, rischi, decisione, rollback trigger)

## Contratto artifact runtime (obbligatorio)

Per promuovere un modello nel runtime `PredictionAgent` v2, gli artifact devono includere contratto esplicito:

- model artifact (`prediction_model_v2`):
  - `artifact_version`
  - `model_name`
  - `model_version`
  - `feature_schema_version`
  - `feature_columns`
  - `required_features`
  - `algorithm_payload`
- calibration artifact (`prediction_calibration_v2`, opzionale ma raccomandato):
  - `calibration_version`
  - `model_version` referenziato
  - `feature_schema_version`
  - `method` (`platt`/`isotonic`)
  - `parameters`

Gate runtime safety:

- parity check feature obbligatorio (`required_features` presenti nel bundle runtime-safe)
- mismatch schema/versione deve fallire in modo chiaro
- fallback euristico ammesso solo finche la promozione non e formalmente completata

## Trigger di rollback

- degrado persistente vs benchmark
- drift di calibrazione oltre soglia
- incremento failure operativi (review/tx/reconcile)
- mismatch tra expected edge e realized edge non spiegato

## Gate runtime esplicito (v2 replace v1)

Per permettere a `model_v2` di sostituire il path euristico in runtime:

1. `model_promotion.enabled=true`
2. runtime mode in `model_promotion.required_runtime_modes` (default `PAPER_LIVE`, `SANDBOX_CHAIN`)
3. stato operatore con promozione esplicita:
   - `model_v2_promoted=true`
   - `model_v2_promoted_model_version` allineato con `model_artifact.model_version`
4. rollback non attivo (`model_v2_rollback_active=false`)

Se il gate blocca:

- con `fallback_to_heuristic=true`: runtime forza euristico con reason code esplicito
- con `fallback_to_heuristic=false`: startup/run falliscono in modo esplicito

Rollback operativo:

- `rollback-model-v2` forza fallback euristico anche se `fallback_to_heuristic=false`
- `clear-model-v2-rollback` riapre il percorso promotion gate normale

## Criteri gate implementati (evidence)

L'evaluation `evaluate-model-promotion` controlla:

- benchmark outperformance
  - delta Brier e log loss vs `market_implied` >= soglie config
  - non regressione vs `heuristic_prediction_agent`
- calibration quality
  - `calibration_error <= max_calibration_error`
  - `calibrated_brier - raw_brier <= max_calibration_brier_increase`
- approval-rate sanity
  - `approval_rate_min <= model_v2_rate <= approval_rate_max`
- realized-edge consistency
  - `edge_capture_ratio >= min_edge_capture_ratio`
  - `realized_vs_expected_edge_ratio >= min_realized_vs_expected_edge_ratio`
- feature/schema compatibility
  - artifact schema == expected runtime schema
  - nessuna required feature mancante
  - parity warning rate <= `max_shadow_parity_warning_rate`

Threshold default in `config/app.yaml -> model_promotion`.

## Drift monitoring implementato

`drift-status` e `model-promotion-status` espongono segnali:

- `input_feature_distribution_shift`
- `market_regime_shift`
- `research_coverage_degradation`
- `prediction_confidence_collapse`

Stati:

- `ok`
- `warning`
- `critical`
- `insufficient_data`

I risultati sono persistiti come artifact auditabili (`model_drift_reports`) e riflessi in operator control state.

## Threshold tuning workflow riproducibile

Il tuning non e automatico in runtime.

Workflow minimo:

1. registrare proposta con `record-threshold-tuning` (rationale + ticket + valori proposti)
2. rieseguire benchmark/walk-forward/evaluation shadow con stessi input
3. allegare delta metriche e decisione in review promozione
4. applicare override config solo dopo approvazione operatore

Ogni proposta viene salvata in artifact/event log (`model_threshold_tuning_records`, `model_threshold_tuning_recorded`).

## Pratiche vietate

- tuning threshold senza change log e approvazione
- esclusione manuale run sfavorevoli senza criterio ex-ante
- promozione basata su singolo periodo favorevole
- qualunque scorciatoia che introduca online training nel runtime
