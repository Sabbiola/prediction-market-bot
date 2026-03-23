# News & Social Source Policy

## Scopo

Stabilire policy operative per integrazione di fonti news/social:

- qualita e tracciabilita dei dati
- rispetto dei confini legali/API
- controlli su rumor/noise prima dell'uso modellistico

## Classi sorgente ufficiali

- `news_rss_web`
- `reddit`
- `x`

Ogni classe richiede adapter dedicato con:

- timeout/retry bounded
- normalizzazione errori
- fake/mock test adapter
- mapping esplicito su schema normalized

## Acquisizione consentita

Consentito:

- API ufficiali
- feed RSS pubblici
- endpoint documentati con termini d'uso compatibili
- Google News discovery via endpoint RSS/search/topic pubblici

Non consentito:

- scraping non autorizzato
- workaround per aggirare rate limits/ToS
- raccolta non tracciabile a fonte/orario
- dipendere da workflow legacy di Publisher Center RSS submission

## Requisiti qualita e deduplica

Per ogni classe sorgente:

- deduplica per `source_record_id` + fingerprint contenuto
- source identity stabile (`domain`, `subreddit`, `account_handle`)
- classificazione affidabilita per classe/fonte
- tagging rumor/low-confidence esplicito

## Timestamp policy

Campi obbligatori:

- `published_at_utc`
- `fetched_at_utc`
- `decision_timestamp_utc` del market associato

Regole:

- segnali pre-decisione: `published_at_utc <= decision_timestamp_utc`
- record non allineabili: raw-only, esclusi da feature promote path

## Capability matrix (env + credenziali)

| Source class | Env | No credentials | Credentials present |
|---|---|---|---|
| news_rss_web | local dev | ON (public/sample) | ON |
| reddit | local dev | OFF | ON |
| x | local dev | OFF | ON |
| news_rss_web | shared staging | ON (guarded) | ON |
| reddit | shared staging | OFF | ON |
| x | shared staging | OFF | ON |
| news_rss_web/reddit/x | beta-live | OFF unless explicitly enabled and validated | ON only with health + policy checks |

Regole di abilitazione:

- feature attiva solo se:
  - endpoint configurato
  - credenziali richieste disponibili
  - startup validation/health passano
- in caso contrario: capability `disabled_unavailable` esplicita (no fail-open silenzioso)

Assunzioni capability per classi sorgente:

- `news_rss_web`:
  - tipicamente non richiede OAuth
  - `supports_search`/`supports_live_polling` attesi
  - query supportate:
    - topic/category query
    - keyword/entity query
- `reddit`:
  - accesso API pratico basato su OAuth
  - `requires_oauth=true` come default operativo
  - usare endpoint OAuth (`https://oauth.reddit.com`) con `Authorization: Bearer <token>`
  - rispettare header rate-limit (`x-ratelimit-remaining`, `x-ratelimit-reset`, `x-ratelimit-used`)
  - nessun percorso non-OAuth nel runtime/offline path ufficiale
- `x`:
  - accesso endpoint/rate limits dipendono da piano e metodo auth
  - non assumere capability uniformi: definire esplicitamente da config
  - integrazione opzionale e disabled-by-default
  - per query `account` richiedere capability/config esplicita per thread/context expansion e auth mode compatibile

## Uso segnali per stadio

- **Offline research only**:
  - tutte le classi sorgente, inclusi segnali rumorosi/sperimentali
- **Shadow mode**:
  - solo segnali con quality minima e ablation positiva
- **Promoted runtime**:
  - solo segnali deterministici/versionati con gate superato

## Audit e incident response

Ogni ingestion run deve produrre:

- contatori per classe sorgente (`fetched`, `dropped`, `aligned`, `deduped`)
- reason codes su scarti (`missing_timestamp`, `unsupported_format`, `rate_limited`)
- eventi audit per failure ricorrenti e degradazioni coverage

Su incident:

- capability puo essere disabilitata per classe sorgente senza fermare il runtime core
- fallback a set segnali stabile/deterministico

## Verifica operativa minima news

Per sorgenti `news_rss_web`:

- eseguire `verify-news-source` prima di un nuovo backfill in staging
- usare `backfill-news` per costruire corpus raw+normalized
- usare `inspect-news-corpus` per controllare dedup/count/checkpoint

## Verifica operativa minima Reddit

Per sorgenti `reddit`:

- configurare `alt_data.sources.reddit` con:
  - `enabled: true`
  - `adapter: reddit_oauth_adapter`
  - `credential_env: REDDIT_ACCESS_TOKEN`
- eseguire `verify-reddit-oauth` prima di un nuovo backfill in staging
- usare `backfill-reddit` per costruire corpus raw+normalized (submissions e commenti opzionali)
- usare `inspect-reddit-corpus` per controllare dedup/count/checkpoint

## Verifica operativa minima X

Per sorgenti `x`:

- configurare `alt_data.sources.x` con:
  - `enabled: true`
  - `adapter: x_api_adapter`
  - `credential_env: X_BEARER_TOKEN`
- eseguire `verify-x-source` prima di un nuovo backfill in staging
- usare `backfill-x` per costruire corpus raw+normalized (keyword e/o account query)
- usare `inspect-x-corpus` per controllare dedup/count/checkpoint
- se capability/auth mode non supportano il tipo query richiesto, il comando deve fallire esplicitamente (no fallback silenzioso)
