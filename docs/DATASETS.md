# Datasets Policy

## Scopo

Definire struttura, versioning e controlli dei dataset usati per research/training offline.

Il runtime non deve dipendere da pipeline di training dataset-building durante l'esecuzione operativa.

Policy collegate per news/social e LLM enrichment:

- `docs/ALT_DATA_RESEARCH.md`
- `docs/NEWS_SOCIAL_SOURCE_POLICY.md`
- `docs/LLM_ENRICHMENT.md`

## Data lake storico mercati risolti

Il repository supporta ingestion offline riproducibile dei mercati risolti tramite CLI:

- `backfill-historical-markets`
- `inspect-dataset`
- `verify-dataset`
- `backfill-research-evidence`
- `inspect-research-corpus`
- `verify-research-alignment`
- `backfill-news`
- `inspect-news-corpus`
- `verify-news-source`
- `backfill-reddit`
- `inspect-reddit-corpus`
- `verify-reddit-oauth`
- `backfill-x`
- `inspect-x-corpus`
- `verify-x-source`
- `build-linkage`
- `inspect-linkage`
- `verify-linkage-quality`
- `enrich-alt-data`
- `inspect-llm-enrichment`
- `build-alt-feature-dataset`
- `inspect-alt-feature-schema`
- `verify-alt-feature-parity`

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/data_ingest/`

Confine architetturale:

- il subsystem e fuori dal path runtime serving (`run-once`, scheduler, UI)
- nessuna dipendenza del loop operativo da data build storico

## Corpus offline evidenze research

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/research_corpus/`

Obiettivo:

- persistere evidenze research grezze + normalizzate in forma durevole
- mantenere provenance/source identity per audit e deduplica
- garantire allineamento temporale con il punto decisionale di mercato

Storage layout per `corpus_id`:

```text
<base_dir>/<corpus_id>/
  raw/
    source_payloads.jsonl
  normalized/
    market_decisions.jsonl
    evidence_findings.jsonl
  checkpoints/
    corpus_checkpoint.json
  corpus_manifest.json
```

## Schema normalizzato corpus evidenze

- `market_decisions`:
  - `market_id`, `event_id`, `market_title`, `market_category`
  - `decision_timestamp_utc`, `resolved_outcome`, `resolved_at_utc`
  - `source_dataset_id`
- `evidence_findings`:
  - `market_id`, `event_id`, `decision_timestamp_utc`, `source_dataset_id`
  - `source_name`, `source_type`, `query`
  - `source_record_id`, `finding_identity`
  - `summary`, `sentiment`, `credibility`, `url`
  - `provenance`, `published_at_utc`, `fetched_at_utc`
  - `is_time_aligned`, `alignment_reason`
- `source_payloads` (raw):
  - `market_id`, `event_id`, `decision_timestamp_utc`
  - `source_name`, `source_type`, `query`
  - `source_record_id`, `fetched_at_utc`
  - `payload`

## Dataset canonico per prediction research

Unita logica:

- una riga per decision point (`run_id`, `market_id`, `selected_side`, timestamp decisionale)

Colonne minime:

- `market_id`
- `decision_timestamp_utc`
- `market_yes_prob_at_decision`
- `feature_market_*`
- `feature_research_*`
- `feature_structure_*`
- `label_yes`
- `resolution_timestamp_utc`
- `data_quality_flags`

## Feature store offline versionato

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/features/`

Comandi:

- `build-feature-dataset`
- `inspect-feature-schema`
- `verify-feature-parity`

Layout per dataset `dataset_id` e schema `v1`:

```text
<base_dir>/<dataset_id>/
  derived/
    feature_store/
      v1/
        feature_rows.jsonl
        feature_schema.json
        feature_manifest.json
```

Regole:

- ogni riga include `feature_schema_version`
- `feature_rows.jsonl` e leakage-safe rispetto a `decision_timestamp_utc`
- `verify-feature-parity` fallisce su mismatch colonne/versione
- il contratto schema e la source of truth tra training offline e serving runtime

## Training lab artifacts (offline)

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/training/`

Comandi:

- `train-baseline-models`
- `calibrate-model`
- `compare-models`
- `generate-model-card`

Layout artifact per dataset:

```text
<base_dir>/<dataset_id>/
  derived/
    training_lab/
      runs/
        train-<timestamp>/
          summary.json
          models/
            logistic_regression_baseline.json
            tree_baseline.json
            xgboost_candidate.json (se disponibile)
          predictions/
            predictions_<model>_train.jsonl
            predictions_<model>_validation.jsonl
            predictions_<model>_test.jsonl
      calibration_runs/
        cal-<timestamp>/
          calibration.json
          summary.json
          eval_predictions_calibrated.jsonl
      model_cards/
        <model>_<train_run_id>.md
      latest_training_run.json
      latest_calibration_run.json
```

Regole:

- training/calibrazione solo offline (mai nel runtime loop)
- metriche minime: brier, log loss, calibration error, accuracy
- output richiesto per split e finestre temporali
- model card generata automaticamente e versionata con run id
- eventuali dipendenze pesanti (es. xgboost) solo via extras research

Schema v1 (gruppi minimi):

- market price
- spread/liquidity
- volume/activity
- time features
- momentum/volatility
- category/event metadata
- research aggregate
- contradiction/diversity/freshness

Research features esplicite nel feature store (`f_research_*`):

- legacy aggregate:
  - `f_research_findings_count`
  - `f_research_weighted_sentiment`
  - `f_research_evidence_strength`
  - `f_research_avg_credibility`
  - `f_research_disagreement`
  - `f_research_source_diversity`
  - `f_research_contradiction_rate`
  - `f_research_freshness_hours`
- enriched dimensions:
  - `f_research_market_relevance`
  - `f_research_timeliness_decay`
  - `f_research_source_credibility_prior`
  - `f_research_conflict_score`
  - `f_research_entity_event_alignment`
  - `f_research_evidence_novelty`
  - `f_research_effective_credibility`

Nota:

- il bundle research deriva da feature engineering deterministica condivisa tra runtime e offline.

## Alt-data feature store offline versionato

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/alt_features/`

Comandi:

- `build-alt-feature-dataset`
- `inspect-alt-feature-schema`
- `verify-alt-feature-parity`

Layout per dataset `dataset_id` e schema `alt-v1`:

```text
<base_dir>/<dataset_id>/
  derived/
    alt_feature_store/
      alt-v1/
        alt_feature_rows.jsonl
        alt_feature_schema.json
        alt_feature_manifest.json
```

Regole:

- dataset costruito da linkage + corpora news/reddit/x + enrichment normalizzato
- ogni riga include `feature_schema_version=alt-v1`
- le evidenze sono ammesse solo se `published_at_utc <= decision_timestamp_utc`
- evidenze senza `published_at_utc` o post-decision sono scartate e conteggiate nel manifest
- `verify-alt-feature-parity` fallisce su mismatch colonne/versione e timestamp/label non validi

Schema v1 (feature minime coperte):

- `f_alt_news_volume_24h`
- `f_alt_news_burstiness_24h`
- `f_alt_source_diversity`
- `f_alt_freshness_decay`
- `f_alt_source_credibility_prior`
- `f_alt_contradiction_score`
- `f_alt_novelty_score`
- `f_alt_catalyst_strength_score`
- `f_alt_reddit_mentions_24h`
- `f_alt_reddit_attention`
- `f_alt_x_mentions_24h`
- `f_alt_x_attention`
- `f_alt_market_linked_coverage`
- `f_alt_enrichment_coverage`

## Storage layout (raw/normalized separati)

Layout per dataset `dataset_id`:

```text
<base_dir>/<dataset_id>/
  raw/
    events.jsonl
    markets.jsonl
    market_snapshots.jsonl
    orderbook_snapshots.jsonl
    trades.jsonl
    resolutions.jsonl
  normalized/
    events.jsonl
    markets.jsonl
    market_snapshots.jsonl
    orderbook_snapshots.jsonl
    trades.jsonl
    resolutions.jsonl
  checkpoints/
    ingest_checkpoint.json
  dataset_manifest.json
```

Layer `raw`:

- payload esterni quasi originali per audit/provenance

Layer `normalized`:

- schema stabile per analisi, eval e training offline
- timestamp UTC normalizzati
- campi numerici convertiti
- outcome/side/status normalizzati

## Schema normalizzato minimo per tabella

- `events`: `event_id`, `title`, `category`, `start_at_utc`, `end_at_utc`
- `markets`: `market_id`, `event_id`, `question`, `category`, `status`, `resolved_outcome`, `resolved_at_utc`, `yes_price_last`
- `market_snapshots`: `market_id`, `snapshot_at_utc`, `yes_price`, `best_bid`, `best_ask`, `liquidity_usd`, `volume_24h_usd`
- `orderbook_snapshots`: `market_id`, `snapshot_at_utc`, `best_bid`, `best_ask`, `bid_size`, `ask_size`
- `trades`: `market_id`, `trade_id`, `timestamp_utc`, `price_yes`, `size`, `side`
- `resolutions`: `market_id`, `resolved_outcome`, `resolved_at_utc`, `resolution_status`

## Label policy

- `label_yes = 1` se risolto YES
- `label_yes = 0` se risolto NO
- mercati `void/cancelled/ambiguous` esclusi dal dataset primario

Comando operativo:

- `build-labels` genera dataset labels riproducibile da `normalized/markets.jsonl` + `normalized/resolutions.jsonl`

Schema minimo `labels.jsonl`:

- `row_id`
- `market_id`
- `event_id`
- `category`
- `market_title`
- `decision_timestamp_utc`
- `resolved_at_utc`
- `resolved_outcome`
- `label_yes`
- `market_yes_prob_at_decision`
- `liquidity_usd`
- `volume_24h_usd`
- `structure_momentum`
- `structure_score`
- `scan_score`
- `research_weighted_sentiment`
- `research_evidence_strength`
- `research_disagreement_score`
- `research_findings_count`
- `data_quality_flags`

## Time-split leakage-safe

Split obbligatorio:

- train
- validation
- test_out_of_time

Regole:

- split solo temporali, mai random puro globale
- grouping per `market_id` per evitare contaminazione
- feature costruite solo con dati disponibili al `decision_timestamp_utc`
- supporto walk-forward con finestre train/validation/test progressive
- statistiche baseline (es. category prior) calcolate solo sul train del fold

Comandi benchmark:

- `run-benchmarks`:
  - `--split-mode holdout|walk-forward`
  - metriche per baseline su split train/validation/test
- `compare-benchmarks`:
  - delta metriche tra due benchmark run
- `run-walk-forward`:
  - simulazione leakage-safe con accounting bankroll/fill/slippage su fold temporali
- `generate-strategy-report`:
  - report markdown riproducibile del run walk-forward

## Dataset versioning

Ogni dataset deve avere identificativo immutabile:

- `dataset_id` (esempio: `pm_2026q1_v001`)

Manifest obbligatorio:

- source artifacts usati
- filtri applicati
- schema colonne
- split boundaries
- record counts
- hash/checksum
- commit SHA di costruzione

## Checkpointing e resumability

Il checkpoint contiene almeno:

- `dataset_id`
- `cursor` paginazione (se presente)
- `processed_market_ids`
- `started_at_utc`
- `updated_at_utc`
- `completed_at_utc`

Comportamento:

- ingestion riprende da checkpoint senza duplicare mercati gia processati
- `--reset-checkpoint` forza ripartenza del cursore
- i rerun standard sono idempotenti rispetto al dataset corrente

## Processo ingestion

`backfill-historical-markets` esegue:

1. fetch pagine mercati risolti
2. fetch metadata evento/market e serie storiche disponibili (snapshots/orderbook/trades)
3. persistenza layer raw
4. normalizzazione e persistenza layer normalized
5. aggiornamento checkpoint e manifest

`backfill-research-evidence` esegue:

1. carica timeline decisionale da `normalized/markets.jsonl` del dataset storico
2. ingestione evidenze da source adapter research con query per mercato
3. persistenza payload grezzi in `raw/source_payloads.jsonl`
4. normalizzazione findings con provenance/source identity
5. filtro temporal alignment (`published_at_utc <= decision_timestamp_utc`)
6. deduplica su chiave stabile (`market_id`, `decision_timestamp_utc`, `source_name`, `finding_identity`)
7. aggiornamento checkpoint e manifest corpus

## Corpus offline news (Google News / RSS-style)

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/news_corpus/`
- adapter: `src/prediction_market_bot/infrastructure/alt_data/adapters/rss_news_adapter.py`

Comandi:

- `backfill-news`
- `inspect-news-corpus`
- `verify-news-source`

Storage layout per `corpus_id`:

```text
<base_dir>/<corpus_id>/
  raw/
    news_payloads.jsonl
  normalized/
    news_articles.jsonl
  checkpoints/
    news_checkpoint.json
  news_manifest.json
```

Schema normalizzato `news_articles` (minimo):

- `source_id`, `source_class`, `source_name`
- `query`, `query_kind` (`topic`/`keyword`)
- `dedup_key`, `source_record_id`
- `title`, `summary`
- `article_url`, `article_domain`, `publisher`
- `published_at_utc`, `fetched_at_utc`
- `source_metadata`

Schema raw `news_payloads`:

- `source_id`, `source_class`, `source_name`
- `query`, `query_kind`
- `dedup_key`, `source_record_id`
- `fetched_at_utc`
- `payload`

Regole:

- deduplica deterministica su `dedup_key`
- nessuna dipendenza da workflow Publisher Center legacy
- provenance e timestamp preservati per utilizzo leakage-safe nei dataset successivi

Rate-limit e resilienza:

- retry/backoff gestiti dal client HTTP strutturato
- throttling tra richieste configurabile (`strategy_research.historical_data_ingest.throttle_sec`)
- throttling corpus research configurabile (`strategy_research.research_corpus.throttle_sec`)

## Corpus offline Reddit (OAuth)

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/reddit_corpus/`
- adapter capability-gated: `src/prediction_market_bot/infrastructure/alt_data/adapters/reddit_oauth_adapter.py`

Comandi:

- `backfill-reddit`
- `inspect-reddit-corpus`
- `verify-reddit-oauth`

Storage layout per `corpus_id`:

```text
<base_dir>/<corpus_id>/
  raw/
    reddit_payloads.jsonl
    reddit_subreddits.jsonl
  normalized/
    reddit_evidence.jsonl
    reddit_subreddits.jsonl
  checkpoints/
    reddit_checkpoint.json
  reddit_manifest.json
```

Schema normalizzato `reddit_evidence` (minimo):

- `source_id`, `source_class`, `source_name`
- `query`, `query_kind` (`subreddit`/`keyword`)
- `evidence_kind` (`submission`/`comment`)
- `dedup_key`, `source_record_id`
- `post_id`, `comment_id`, `parent_post_id`
- `subreddit`, `subreddit_id`
- `title`, `body`, `author`
- `score`, `num_comments`
- `permalink_url`, `external_url`
- `created_at_utc`, `fetched_at_utc`
- `subreddit_metadata`, `source_metadata`

Schema `reddit_subreddits`:

- `source_id`, `source_class`, `source_name`
- `subreddit`, `subreddit_id`
- `fetched_at_utc`
- `metadata` (title, description, subscribers, over18, url)

Schema raw `reddit_payloads`:

- `source_id`, `source_class`, `source_name`
- `query`, `query_kind`, `evidence_kind`
- `dedup_key`, `source_record_id`
- `fetched_at_utc`
- `payload`

Regole:

- OAuth obbligatorio (`alt_data.sources.reddit.credential_env`)
- deduplica deterministica su `dedup_key`
- submissions e comments configurabili (`include_comments`)
- checkpoint con cursore per query (`query_cursors`) per resumability
- modalita incremental esplicita (`backfill-reddit --incremental`) senza reset del corpus
- provenance + timestamp preservati per linking leakage-safe

## Corpus offline X (optional, capability-gated)

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/x_corpus/`
- adapter capability-gated: `src/prediction_market_bot/infrastructure/alt_data/adapters/x_api_adapter.py`

Comandi:

- `backfill-x`
- `inspect-x-corpus`
- `verify-x-source`

Storage layout per `corpus_id`:

```text
<base_dir>/<corpus_id>/
  raw/
    x_payloads.jsonl
  normalized/
    x_evidence.jsonl
  checkpoints/
    x_checkpoint.json
  x_manifest.json
```

Schema normalizzato `x_evidence` (minimo):

- `source_id`, `source_class`, `source_name`
- `query`, `query_kind` (`keyword`/`account`)
- `dedup_key`, `source_record_id`
- `post_id`, `post_url`
- `author_id`, `author_username`
- `text`, `lang`, `conversation_id`
- `public_metrics`
- `created_at_utc`, `fetched_at_utc`
- `source_metadata`

Schema raw `x_payloads`:

- `source_id`, `source_class`, `source_name`
- `query`, `query_kind`
- `dedup_key`, `source_record_id`
- `fetched_at_utc`
- `payload`

Regole:

- integrazione opzionale e disabled-by-default
- deduplica deterministica su `dedup_key`
- query `account` ammesse solo quando capability/config consentono thread-context expansion e auth mode compatibile
- mismatch capability/auth/endpoint deve fallire con reason code esplicito (no fail-open)

## Linkage offline evidenze->eventi/mercati

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/linkage/`

Comandi:

- `build-linkage`
- `inspect-linkage`
- `verify-linkage-quality`

Storage layout per `linkage_id`:

```text
<base_dir>/<linkage_id>/
  raw/
    linkage_candidates.jsonl
  normalized/
    linkage_results.jsonl
  checkpoints/
    linkage_checkpoint.json
  linkage_manifest.json
```

Schema normalizzato `linkage_results` (minimo):

- `linkage_id`, `dataset_id`
- `evidence_key`, `source_class`, `source_name`, `source_record_id`, `dedup_key`
- `state` (`linked` / `ambiguous` / `unresolved` / `stale_evidence`)
- `confidence`, `ambiguity_score`
- `matched_market_id`, `matched_event_id`, `decision_timestamp_utc`
- `evidence_title`, `evidence_url`, `published_at_utc`, `fetched_at_utc`
- `extracted_entities`, `matched_aliases`
- `top_candidates` (score/ragioni per traceability)
- `rationale`, `created_at_utc`

Schema raw `linkage_candidates`:

- metadata evidenza (`evidence_key`, `source_*`, `published_at_utc`)
- candidato mercato/evento e breakdown score
- `reason_codes` e `matched_aliases`

Regole:

- linking deterministico con entity extraction rules-first
- soglia similarita + vincolo temporale (`published_at_utc <= decision_timestamp_utc`)
- alias resolution configurabile (`strategy_research.linkage.alias_map`)
- stati ambigui/non risolti espliciti: nessun forcing silenzioso della corrispondenza
- rerun idempotenti con checkpoint + dedup su `evidence_key`

## Corpus enrichment LLM (offline research)

Percorso implementativo:

- `src/prediction_market_bot/strategy_research/llm_enrichment/`

Comandi:

- `enrich-alt-data`
- `inspect-llm-enrichment`

Storage layout per `enrichment_id`:

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

Schema normalizzato `enrichment_records` (minimo):

- identificativi e linkage:
  - `enrichment_id`, `linkage_id`, `evidence_key`
  - `source_class`, `source_name`, `source_record_id`, `dedup_key`
  - `market_id`, `event_id`, `decision_timestamp_utc`
  - `linkage_state`, `linkage_confidence`
- metadata enrichment:
  - `provider_name`, `model_id`, `prompt_version`
  - `determinism_mode`, `status`, `fallback_used`, `failure_reason`
  - `normalization_violations`
- output strutturato (`output`):
  - `relevance_score`
  - `extracted_claims`
  - `contradiction_score`
  - `contradiction_indicators`
  - `novelty_score`
  - `catalyst_class`
  - `structured_summary`

Regole:

- pipeline solo offline research (nessun impatto diretto su execution runtime)
- fallback deterministico supportato quando provider esterno non disponibile
- output validato/normalizzato prima della persistenza
- tracciabilita completa prompt/provider/output per audit e analisi ablation

## Qualita minima

Check obbligatori:

- no null su campi chiave (`market_id`, `decision_timestamp_utc`, `label_yes`)
- no duplicati sulla chiave logica (`market_id`, `decision_timestamp_utc`, `selected_side`)
- coerenza monotona temporale per split
- copertura sufficiente per classi YES/NO
- `verify-research-alignment`:
  - nessun finding post-decision
  - nessun finding senza provenance/source identity
  - nessun duplicato su chiave finding normalizzata

## Assunzioni economiche standard

Per metriche economiche (edge/ROI) ogni dataset-eval deve dichiarare:

- fee model usato
- slippage model usato
- size/risk policy assunta

Senza queste assunzioni non e consentito usare i risultati per promozione.

## Pratiche vietate

- iniettare feature post-resolution
- usare finding senza timestamp pubblicazione per training leakage-safe
- cambiare split dopo aver visto risultati test
- backfill manuale non tracciato
- dataset non versionati usati per decisioni di promozione
- usare ingestion storico nel path runtime serving
