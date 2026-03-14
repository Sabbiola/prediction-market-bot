# Beta Scope

## Obiettivo beta-live

Questa beta definisce un rilascio staging-safe per prediction markets con:

- runtime disciplinato e auditabile
- decisione/rischio nel backend runtime
- human-in-the-loop obbligatorio nelle modalita beta-live
- execution non-live su `PAPER` e lane reale di rehearsal su `SANDBOX_CHAIN`

La beta non include venue live order posting.

## In-scope

- pipeline end-to-end:
  - `ScanAgent`
  - `ResearchAgent`
  - `PredictionAgent`
  - `RiskAgent`
  - `ExecutionAgent`
  - `SettlementAgent`
  - `PostmortemAgent`
- runtime modes:
  - `DRY_RUN_STATIC`
  - `PAPER_LIVE`
  - `SANDBOX_CHAIN`
  - `LIVE_DISABLED`
- execution modes:
  - `PAPER`
  - `SHADOW_SIGN`
  - `SANDBOX_CHAIN`
  - `LIVE_DISABLED`
- review queue bloccante con rationale modello/operatore
- settlement lane separata dal ciclo decisionale
- sandbox transaction rehearsal lane con reconcile/resubmit-safe
- persistenza operativa SQLite + artefatti JSONL append-only
- observability operativa (`healthcheck`, `status`, metriche, audit events)
- release gate beta-live (quality + smoke + acceptance)
- autenticazione staging forte su control-plane (session + RBAC + password hash PBKDF2)
- secret resolution centralizzata (`security.secrets`) con backend env/command
- **operator UI minima (control-plane)**, limitata a:
  - visualizzazione stato runtime, queue, posizioni, tx
  - azioni operatore gia previste dal runtime (review/tx/pause-resume)
  - nessuna logica decisionale nel layer UI

## Out-of-scope

- venue live execution e gestione fondi reali
- bypass di risk/review guardrails
- training online e auto-tuning nel runtime
- smart order routing multi-venue
- workflow UI non auditabili
- funzionalita UI che implementano prediction/risk/execution logic

## Assunzioni operative

- `feature_flags.allow_live_execution` resta `false`
- review bloccante attiva in `PAPER_LIVE` e `SANDBOX_CHAIN`
- sandbox-chain usata come rehearsal lane transazionale, non come trading lane
- ogni run e azione operatore resta correlabile tramite `run_id`
- runtime e UI condividono la stessa source of truth operativa (backend runtime + storage)

## Rischi principali

- disallineamento tra runtime e UI se i contratti non restano stabili
- deriva della UI verso business logic non prevista
- gap tra comportamento paper/sandbox e venue reali
- code review/settlement in accumulo senza adeguata operativita giornaliera

## Release gates (Go/No-Go)

Una release beta e promuovibile solo se tutti i gate sono verdi:

1. quality (`lint`, `typecheck`, test base)
2. smoke dry-run
3. smoke beta-live (provider mocked + sandbox lane)
4. beta-live acceptance suite
5. startup validation e healthcheck coerenti in staging

## Criteri di uscita dalla beta

- stabilita operativa dimostrata su finestra continua di run
- coda review/settlement/tx gestibile con workflow operatore
- UI minima control-plane validata senza introdurre logica di business
- hardening successivo approvato (SSO/OIDC, incident tooling avanzato)
