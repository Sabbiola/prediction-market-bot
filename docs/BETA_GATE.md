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

## Checklist di Rilascio (PASS/FAIL)

1. Startup validation passa in configurazione staging (`validate-startup`) senza errori bloccanti.
2. Suite acceptance beta-live verde (`make ci-beta-acceptance`).
3. I test confermano che in `PAPER_LIVE`/`SANDBOX_CHAIN` l’esecuzione non parte senza `APPROVED`.
4. I test confermano persistenza di `model_rationale` e `operator_rationale`.
5. I test confermano che le open positions sopravvivono ai restart.
6. I test confermano che la settlement lane chiude posizioni in passaggio separato.
7. I test confermano tx sandbox tracciabile (`run_id` + `review_queue_id`) e riconciliabile.
8. Replay e report generation da artefatti persistiti risultano operativi.

Se anche un solo punto fallisce: **release bloccata**.

## Messaggi Operatore

La suite acceptance usa assert con prefisso `[beta-gate]` e testo operativo (cosa è mancato e cosa verificare).  
Usare questi messaggi come guida immediata per triage e rollback decision.

## Rollback/Pause Decision

Se il gate fallisce in staging:

1. Non promuovere la release.
2. Mettere in pausa l’operatività (`pause`) se il deployment è già partito.
3. Aprire fix PR con root-cause e test di regressione.
4. Rieseguire integralmente `make ci-beta-acceptance` prima di un nuovo tentativo.
