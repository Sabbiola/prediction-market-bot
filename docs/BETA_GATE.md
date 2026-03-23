# Beta-Live Release Gate

Questo documento definisce il gate minimo obbligatorio prima di promuovere una release in ambiente beta-live staging.

## Obiettivo

- Rendere la readiness beta-live esplicita, misurabile e ripetibile.
- Bloccare regressioni sui percorsi operativi principali:
  - `PAPER_LIVE` con provider live astratti
  - review queue bloccante (human-in-the-loop)
  - approvazione operatore -> esecuzione
  - persistenza open positions
  - settlement lane asincrona
  - sandbox-chain tx path
  - replay/report da artefatti persistiti

## Prerequisiti

- Nessuna credenziale venue reale richiesta.
- Python 3.11.
- Dipendenze installate con:

```bash
make install
```

## Comando Gate Ufficiale

Eseguire sempre:

```bash
make ci-beta-acceptance
```

Il target esegue:

```bash
pytest -q -m acceptance tests/acceptance
```

In CI devono risultare verdi anche i job dedicati UI:

- `UI Control Plane (Routes + Rendering)`
- `Beta-Live Acceptance Gate` (incluso path UI end-to-end)

## Checklist di Rilascio (PASS/FAIL)

1. Startup validation passa in configurazione staging (`validate-startup`) senza errori bloccanti.
2. Suite acceptance beta-live verde (`make ci-beta-acceptance`).
3. I test confermano che in `PAPER_LIVE`/`SANDBOX_CHAIN` l’esecuzione non parte senza `APPROVED`.
4. I test confermano persistenza di `model_rationale` e `operator_rationale`.
5. I test confermano che le open positions sopravvivono ai restart.
6. I test confermano che la settlement lane chiude posizioni in passaggio separato.
7. I test confermano tx sandbox tracciabile (`run_id` + `review_queue_id`) e riconciliabile.
8. Replay e report generation da artefatti persistiti risultano operativi.
9. I test UI confermano operativita end-to-end su `Review Queue` e `Sandbox TX`, inclusi path bloccati e audit trail azioni.
10. I test UI confermano monitoraggio operativo (widget live + stale-data counters), banner incident e feed eventi audit.
11. Il tab `Reports` espone shortcut replay/evaluation consistenti con i comandi CLI documentati.
12. Il control plane UI e deployabile separatamente in staging (`prediction-market-ui`) con health/readiness verdi.
13. La acceptance suite include il path UI: login -> overview read -> review approve -> sandbox tx reconcile view.
14. Promotion gate v2 verificato:
    - `evaluate-model-promotion` produce evidence completa e criteri espliciti.
    - `model-promotion-status` mostra decisione gate coerente con runtime mode.
15. Rollback safety verificata:
    - `rollback-model-v2` forza fallback euristico senza cambiare guardrail review/risk.
    - `clear-model-v2-rollback` ripristina il gate normale.
16. Drift monitoring operativo:
    - `drift-status` espone segnali su feature shift, regime shift, research coverage, confidence collapse.
    - segnali warning/critical sono leggibili e azionabili lato operatore.
17. Alt-data + LLM promoted path (sandbox-only) verificato:
    - attivazione controllata solo in `SANDBOX_CHAIN` dopo gate `model_v2_allowed`
    - UI espone `active_source_set`, `enrichment_coverage`, `disagreement_vs_baseline`
    - failure capability sorgenti (credential/capability missing) e visibile in `validate-startup`
    - fallback graceful a baseline `model_v2` e auditabile quando path alt/LLM non disponibile
18. Review Queue UI workflow verificato:
    - ogni candidato mostra contesto decisionale minimo (`market/title`, fair vs market prob, edge, confidence, stake, rationale, evidence coverage)
    - lifecycle include visibilita `PENDING_REVIEW/APPROVED/REJECTED/EXPIRED/EXECUTED`
    - approvazione/rifiuto richiede rationale + conferma esplicita e produce feedback + audit log leggibile
    - deep-link operativi disponibili verso tab `Prediction`, `Risk`, `Sandbox TX`, `Position`
19. Incident & historical-debug workflow verificato:
    - `Overview`/`System` mostrano incident feed strutturato (`when`, `what failed`, `affected`, `reason_code`, deep-link)
    - da un incidente e possibile navigare rapidamente a `Run`, `Review`, `Sandbox TX`, `Position`, `Settlement`, `Reports`
    - tab `Reports` espone navigator run-level + shortcut replay/report utilizzabili per triage storico

Se anche un solo punto fallisce: **release bloccata**.

## Dress Rehearsal Finale: SANDBOX_CHAIN + Model v2 Promoted

Prima del go/no-go finale eseguire un rehearsal completo e ripetibile in `SANDBOX_CHAIN` con `model_v2` attivo.

Sequenza minima obbligatoria:

1. Promozione controllata (`promote-model-v2`) con rationale operatore e versione artifact esplicita.
2. Verifica gate (`model-promotion-status`) con decisione:
   - `reason=model_v2_allowed`
   - `effective_engine=model_v2`
3. `validate-startup` in configurazione rehearsal.
4. Primo `run-once` con provider live astratti attivi (market + research) e review item in `PENDING_REVIEW`.
5. `review-approve` con rationale operatore.
6. Secondo `run-once` con submit sandbox tx attivato.
7. `tx-status` + `tx-reconcile` con stato transazione riconciliabile.
8. Verifica open positions / settlement lane (`paper-portfolio-state`, `run-settlement-lane`).
9. `replay-run` + `generate-report` per evidenza auditabile.
10. UI control-plane:
    - `Overview` e `Prediction` mostrano `active_model_version`
    - visibilita su approval-rate summary, disagreement buckets, drift alert
    - se path enriched attivo: visibilita su source set, enrichment coverage, disagreement vs baseline.

Output evidence da allegare al change record:

- output JSON di `model-promotion-status`
- artifact review decision + operator rationale
- artifact tx intent/attempt/receipt/reconcile
- report markdown/json della run rehearsal
- screenshot/export UI con model visibility

## Messaggi Operatore

La suite acceptance usa assert con prefisso `[beta-gate]` e testo operativo (cosa è mancato e cosa verificare).  
Usare questi messaggi come guida immediata per triage e rollback decision.

## Rollback/Pause Decision

Se il gate fallisce in staging:

1. Non promuovere la release.
2. Mettere in pausa l’operatività (`pause`) se il deployment è già partito.
3. Aprire fix PR con root-cause e test di regressione.
4. Rieseguire integralmente `make ci-beta-acceptance` prima di un nuovo tentativo.
