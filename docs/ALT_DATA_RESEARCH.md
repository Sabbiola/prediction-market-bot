# Alternative Data Research Contract

## Scopo

Definire il contratto ufficiale per integrare dati alternativi (news/social) e segnali derivati in modo leakage-safe, misurabile e separato dal runtime decisionale.

Obiettivo:

- aumentare la qualita informativa del research stack
- senza introdurre scorciatoie non auditabili nel path operativo

## Classi sorgente supportate

Classi ufficiali in scope:

- `news_rss_web`: Google News / RSS / web news indicizzabili
- `reddit`: post e commenti da subreddit rilevanti
- `x`: post pubblici (API ufficiali)

Nota:

- qualunque nuova classe sorgente deve avere adapter dedicato, policy di provenance e test di normalizzazione.

## Boundary architetturali

- ingestion e normalizzazione alt-data vivono nel percorso offline research (`strategy_research/*`) o in shadow pipeline dedicata.
- nessuna logica di fetch/parsing rumorosa direttamente in `PredictionAgent` o in policy `RiskAgent`.
- runtime puo consumare solo feature/versioni promosse e documentate.

## Provenance minima obbligatoria

Ogni record (raw e normalized) deve contenere almeno:

- `source_class` (`news_rss_web`/`reddit`/`x`)
- `source_name`
- `source_record_id` (id esterno stabile)
- `source_url` (se disponibile)
- `author_handle_or_domain` (se disponibile)
- `published_at_utc` (timestamp fonte)
- `fetched_at_utc` (timestamp ingestion)
- `market_id`/`event_id` associati
- `query_context` (query/topic/filter)
- `ingestion_run_id`

Record senza provenance completa non devono entrare nel dataset silver/gold.

## Allineamento temporale (anti-leakage)

Regole obbligatorie:

- il join su market/event deve usare `decision_timestamp_utc`
- `published_at_utc` deve essere `<= decision_timestamp_utc` per training/eval leakage-safe
- record con timestamp mancante o ambiguo:
  - ammessi solo in layer raw
  - esclusi da feature promozionali
- ogni trasformazione deve preservare `alignment_reason` e `is_time_aligned`

## Linking evidenze->eventi/mercati

Prima dell'uso feature engineering, le evidenze news/social devono passare da un linker offline deterministico:

- package: `strategy_research/linkage`
- comandi: `build-linkage`, `inspect-linkage`, `verify-linkage-quality`

Regole minime linker:

- entity extraction deterministic-first (no LLM requirement)
- similarity su titolo evento/mercato + token overlap
- alias resolution esplicita da config (`strategy_research.linkage.alias_map`)
- vincoli temporali: un match e valido solo se `published_at_utc <= decision_timestamp_utc`
- gestione esplicita ambiguita/incertezza:
  - `linked`
  - `ambiguous`
  - `unresolved`
  - `stale_evidence`

Vietato forzare un singolo match quando l'ambiguita supera la soglia configurata.
Le decisioni di linkage devono restare tracciabili con candidate score e reason code.

## Livelli segnale consentiti

1. **Offline research only**
   - segnali grezzi rumorosi
   - embedding/feature sperimentali
   - segnali non stabili o non calibrati
2. **Shadow mode**
   - segnali che hanno superato benchmark offline minimi
   - visibili in report confronto, non vincolano execution
3. **Promoted runtime inference**
   - solo feature deterministiche/versionate
   - con parity check runtime/offline
   - con gate model promotion superato

## Requisiti di valutazione prima dell'uso runtime

Prima di usare segnali alt-data in runtime (anche solo shadow):

- benchmark out-of-time rispetto a baseline senza alt-data
- analisi leakage e coverage per classe sorgente
- stabilita su finestre temporali multiple (walk-forward)
- documentazione in model card:
  - contributo atteso
  - failure modes
  - rollback trigger

Prima di promoted runtime:

- evidenza che il contributo resta positivo anche con fee/slippage assumptions
- parity warning rate sotto soglia gate
- runbook operativo aggiornato con degradazioni/fallback

## Capability matrix (environment + credenziali)

| Capability | Local dev (no creds) | Local dev (with creds) | Shared staging | Beta-live |
|---|---|---|---|---|
| News/RSS ingest offline | ON (sample/fake) | ON | ON | ON |
| Reddit ingest offline | OFF | ON | ON | ON |
| X ingest offline | OFF | ON | ON | ON |
| Alt-data in shadow runtime reports | OFF | Optional | ON (guarded) | ON (guarded) |
| Alt-data in promoted runtime features | OFF | OFF | Optional (gate) | ON only after gate |
| LLM enrichment experimental | OFF by default | Optional | Optional | OFF by default |

Regole operative:

- senza credenziali: fallback esplicito a provider mock/static.
- in staging/beta-live: nessun fail-open silenzioso; capability non disponibile deve essere visibile in health/status.

## Divieti espliciti

- usare segnali alt-data non allineati temporalmente come feature di training/promozione
- promuovere feature social/news senza provenance e audit trail
- bypassare review/risk guardrail con motivazione "news urgent"
- introdurre addestramento online o threshold tuning implicito in runtime
