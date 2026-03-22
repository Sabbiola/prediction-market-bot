# Strategy Research Contract

## Obiettivo

Questo documento definisce il contratto tra:

- percorso research offline (dataset, training, valutazione)
- runtime operativo (scoring, risk, review, execution paper/sandbox)

Obiettivo target:

- massimizzare expectancy positiva e performance risk-adjusted
- senza promesse di profitto garantito

## Boundary non negoziabili

- nessun training/tuning nel runtime
- nessun auto-tuning online di threshold o pesi
- nessun uso di informazioni post-resolution nelle feature
- nessun bypass dei guardrail di review/risk per "far tornare" i risultati

Il runtime consuma solo artefatti versionati promossi da research:

- model artifact
- feature spec
- threshold policy documentata
- model card

## Definizione label (mercati risolti)

Label primaria per modello binario YES/NO:

- `label_yes = 1` se il mercato risolve YES
- `label_yes = 0` se il mercato risolve NO

Casi esclusi dal training/eval primaria:

- mercati cancellati/void
- risoluzioni ambigue
- mercati senza timestamp decisionale affidabile

Regola temporale:

- la label usa solo lo stato finale di risoluzione
- tutte le feature devono essere congelate a `decision_timestamp_utc`

## Regole corpus evidenze research

Per il corpus offline evidenze (`strategy_research/research_corpus`):

- ogni finding deve essere associato a:
  - `market_id`
  - `event_id`
  - `decision_timestamp_utc`
  - provenance completa (`source_name`, `source_type`, query, source identity)
- la persistenza normalizzata e valida solo se:
  - `published_at_utc` e disponibile
  - `published_at_utc <= decision_timestamp_utc`
- finding post-decision o non allineabili temporalmente devono essere esclusi dal dataset training leakage-safe
- la verifica obbligatoria passa da `verify-research-alignment`

## Split leakage-safe

Split obbligatori per tempo (mai random puro):

1. `train`: finestre storiche piu vecchie
2. `validation`: finestra successiva
3. `test_out_of_time`: finestra piu recente non vista

Regole:

- split su `decision_timestamp_utc` (o fallback `market_close_timestamp_utc`)
- gruppi per `market_id` per evitare fuga di informazioni duplicate
- embargo temporale minima tra train e test quando necessario

## Contratto feature store offline

Il training usa un feature store offline versionato:

- build: `build-feature-dataset`
- inspect contract: `inspect-feature-schema`
- verify parity: `verify-feature-parity`

Regole obbligatorie:

- feature generate solo al `decision_timestamp_utc`
- nessun uso di snapshot/trade/orderbook successivi al decision timestamp
- nessun finding research con `published_at_utc > decision_timestamp_utc`
- schema versionato esplicito (`feature_schema_version`) su ogni riga
- parity schema/righe verificata prima di benchmark e training

Gruppi minimi coperti dal contratto v1:

- market price
- spread/liquidity
- volume/activity
- time-to-resolution
- momentum/volatility
- category/event metadata
- research aggregate
- contradiction/diversity/freshness

## Filosofia research features

Il segnale research non deve fermarsi a una media sentiment pesata.
La pipeline deve estrarre un bundle deterministico multi-dimensione, riusabile:

- nel runtime (`ResearchPacket.feature_bundle`) per inference futura
- nel feature store offline (`f_research_*`) per training/calibrazione

Dimensioni minime coperte:

- market relevance score
- timeliness/decay
- source credibility priors
- contradiction/conflict score
- source diversity
- entity/event alignment
- evidence novelty

Regole:

- default deterministico e riproducibile
- nessuna dipendenza LLM/semantic nel path default
- eventuali feature semantiche restano opzionali, esplicite e disattivate di default
- backward compatibility sui campi research legacy (`weighted_sentiment`, `evidence_strength`, `disagreement_score`)

## Training e calibration lab (offline only)

Il laboratorio di training vive fuori dal runtime serving path:

- package: `src/prediction_market_bot/strategy_research/training/`
- input: `feature_rows.jsonl` leakage-safe generato dal feature store
- output: artefatti esperimenti, modelli baseline, calibration runs, model cards

Comandi:

- `train-baseline-models`
- `calibrate-model`
- `compare-models`
- `generate-model-card`

Modelli supportati:

- logistic regression baseline
- tree baseline (decision stump)
- xgboost candidate (opzionale, dipendenza extra research)

Calibrazione probabilita:

- Platt scaling
- Isotonic calibration

Artefatti minimi attesi per run:

- metriche per split (`train/validation/test`)
- metriche per finestra temporale
- feature importance summary
- model metadata serializzato
- curve di calibrazione pre/post (per run calibrato)
- model card markdown generata automaticamente

Dipendenze opzionali research:

- installare extras dedicate quando serve training avanzato:
  - `pip install ".[research]"`

Nota:

- anche con extras installate, nessuna logica di training entra nel runtime operativo.
- la promozione resta governata dai gate documentati in `docs/MODEL_PROMOTION.md`.

## Benchmark da battere

Ogni candidato modello deve battere almeno:

1. benchmark `market_implied`:
   - probabilita uguale al prezzo mercato al timestamp decisionale
2. benchmark `fifty_fifty`:
   - probabilita costante 0.50
3. benchmark `category_prior`:
   - prior per categoria stimato solo sul train split leakage-safe
4. benchmark `heuristic_prediction_agent`:
   - euristica attuale del `PredictionAgent` ricostruita offline
5. benchmark `research_only`:
   - solo segnali research aggregati (`weighted_sentiment`, `evidence_strength`, `disagreement`)
6. benchmark `momentum_structure`:
   - baseline semplice con segnali di struttura/momentum temporale

Se il candidato non batte il benchmark principale (`market_implied`) su test out-of-time, non puo essere promosso.

Policy split benchmark:

- default: split temporale `train/validation/test`
- opzionale: walk-forward con finestre temporali progressive
- vietato usare random shuffle cross-time nei benchmark promozionali
- tutte le statistiche di prior devono usare solo dati `train` del fold corrente

## Metriche primarie obbligatorie

- `Brier score`
- `Log loss`
- `Calibration error` (ECE o metrica equivalente dichiarata)
- `Approval rate` dopo policy risk/review applicata
- `Realized edge vs expected edge`
- `Paper ROI` con assunzioni esplicite di fee/slippage
- `Sandbox ROI` con assunzioni esplicite di fee/slippage

Output minimo benchmark:

- `build-labels`: genera label leakage-safe riproducibili
- `run-benchmarks`: produce metriche per baseline e split
- `compare-benchmarks`: produce delta metriche tra due run benchmark
- `run-ablation-study`: misura il contributo incrementale di research/news/reddit/x/LLM su split leakage-safe
- `compare-alt-data-variants`: confronta varianti ablation rispetto a un riferimento
- `run-walk-forward`: simula execution/accounting leakage-safe su finestre temporali progressive
- `generate-strategy-report`: produce report markdown riproducibile del run walk-forward

Nota:

- ROI va sempre accompagnato da numero trade, dispersione e drawdown
- nessuna metrica singola puo giustificare da sola la promozione

## Metodologia ablation alt-data/LLM

L'ablation study deve dimostrare se il segnale aggiuntivo migliora o degrada il comportamento predittivo rispetto al solo mercato.

Varianti minime richieste:

- `market_only_baseline`
- `market_plus_research_baseline`
- `market_plus_news`
- `market_plus_reddit`
- `market_plus_x`
- `market_plus_alt_data_without_llm`
- `market_plus_alt_data_with_llm_enrichment`

Metriche richieste per split:

- `Brier score`
- `Log loss`
- `Calibration error`
- `Approval rate` e delta vs `market_only_baseline`
- `Realized vs expected edge` (`edge_capture_ratio`)
- `Walk-forward robustness` (stabilita cross-fold della variante)

Regole:

- nessun random shuffle cross-time
- varianti valutate sullo stesso set leakage-safe
- output report in formato tabellare, adatto a review promozione

## Contratto alt-data feature store (offline)

Per segnali news/social/LLM il repository usa un feature store dedicato e versionato:

- build: `build-alt-feature-dataset`
- inspect contract: `inspect-alt-feature-schema`
- verify parity: `verify-alt-feature-parity`

Regole obbligatorie:

- join su market/event passa solo da linkage normalizzato
- le feature sono generate solo al `decision_timestamp_utc`
- evidenze senza `published_at_utc` non entrano nel dataset alt
- evidenze con `published_at_utc > decision_timestamp_utc` vengono scartate (anti-leakage)
- schema versionato esplicito (`feature_schema_version=alt-v1`) su ogni riga
- parity schema/righe verificata prima di training, benchmark o ablation

Dimensioni minime alt-data coperte in `alt-v1`:

- news volume/burstiness
- source diversity
- freshness/decay
- source credibility priors
- contradiction score
- novelty score
- catalyst strength score
- reddit attention features
- x attention features
- market-linked coverage + enrichment coverage

## Assunzioni simulatore walk-forward

Il simulatore strategico offline:

- usa solo record ordinati per `decision_timestamp_utc`
- valuta predizioni al timestamp decisionale corretto (nessun accesso a dati futuri)
- applica threshold di approvazione (`min_confidence`, `min_edge_bps`)
- simula fill con ipotesi esplicite di `slippage_bps`
- simula fee con ipotesi esplicite di `fee_bps`
- simula evoluzione bankroll con sizing configurabile (`base_position_pct`, `max_position_pct`, `min_stake_usd`)
- chiude le posizioni al `resolved_at_utc` (o fallback esplicito con warning)

Metriche minime del simulatore:

- ROI / PnL
- max drawdown
- turnover
- approval rate
- realized edge vs expected edge
- calibration by cohort

## Contratto con runtime beta-live

Runtime target attuale:

- modalita: `DRY_RUN_STATIC`, `PAPER_LIVE`, `SANDBOX_CHAIN`, `LIVE_DISABLED`
- execution modes: `PAPER`, `SHADOW_SIGN`, `SANDBOX_CHAIN`, `LIVE_DISABLED`
- venue live posting resta disabilitato

Ricerca strategica deve produrre artefatti compatibili con questo flusso, senza introdurre dipendenze runtime da notebook/script di training.

## Regole di promozione

La promozione da research a shadow scoring sandbox avviene solo tramite gate formali.

Riferimento operativo:

- `docs/MODEL_PROMOTION.md`
- `docs/MODEL_CARD_TEMPLATE.md`
- `docs/DATASETS.md`
- `docs/ALT_DATA_RESEARCH.md`
- `docs/LLM_ENRICHMENT.md`
- `docs/NEWS_SOCIAL_SOURCE_POLICY.md`

## Contratto alt-data e LLM enrichment

Per integrazioni news/social e layer LLM valgono regole aggiuntive:

- classi sorgente ufficiali: `news_rss_web`, `reddit`, `x`
- provenance e timestamp alignment obbligatori prima di qualunque uso modellistico
- uso LLM consentito solo come enrichment tracciabile, mai come decision engine autonomo

Policy di utilizzo segnali:

- `offline_research_only`: segnali sperimentali/rumorosi ammessi
- `shadow_mode`: solo segnali con evidenza minima out-of-time e report comparativo
- `promoted_runtime`: solo feature deterministiche/versionate con gate superato

Dettaglio completo in:

- `docs/ALT_DATA_RESEARCH.md`
- `docs/LLM_ENRICHMENT.md`
- `docs/NEWS_SOCIAL_SOURCE_POLICY.md`

Comandi research per enrichment strutturato:

- `enrich-alt-data`
- `inspect-llm-enrichment`

Output minimi persistiti e valutabili:

- `relevance_score`
- `extracted_claims`
- `contradiction_score` + `contradiction_indicators`
- `novelty_score`
- `catalyst_class`
- `structured_summary`
- metadata tracciabilita (`provider_name`, `model_id`, `prompt_version`, `determinism_mode`, `fallback_used`)

Nota:

- i segnali LLM arricchiti restano nel percorso research/evidence finche non superano gate espliciti.
- non sono autorizzati a guidare direttamente risk/review/execution.

## Pratiche vietate

- online training nel runtime
- backtest cherry-picked senza split out-of-time
- feature con leakage post-resolution
- includere nel training finding research pubblicati dopo il decision timestamp
- usare finding senza provenance/source identity tracciabile
- tuning soglie non documentato/versionato
- modifiche modello in produzione senza model card e gate evidence
