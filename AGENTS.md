# AGENTS.md

## 1. Missione del progetto

Questo repository implementa una piattaforma ordinata per **prediction market trading agentico**.
Il prodotto non deve generare “segnali” astratti, ma gestire un ciclo disciplinato e auditabile:

1. scansione mercati
2. ricerca parallela
3. calibrazione probabilistica
4. sizing del rischio
5. esecuzione ordine
6. settlement
7. postmortem
8. apprendimento operativo

L’obiettivo architetturale è impedire che il codice ricada in un monolite runtime come nel progetto legacy.

---

## 2. Regole non negoziabili

### 2.1 Runtime vs research
- **Il runtime non allena modelli**.
- Training, tuning, backtest e analisi esplorative vivono fuori dal loop operativo.
- Il runtime consuma solo artefatti versionati: config, modelli, prompt, policy.

### 2.2 Una sola source of truth
- Una sola configurazione attiva per ogni agente.
- Una sola definizione attiva per il dominio prediction-market.
- Una sola pipeline decisionale attiva.
- Nessuna duplicazione di logiche `prediction` o `risk` in moduli paralleli.

### 2.3 Contratti forti
Ogni passaggio del flusso deve produrre un artefatto tipizzato e persistibile:
- `MarketCandidate`
- `ResearchPacket`
- `PredictionResult`
- `RiskDecision`
- `OrderIntent`
- `ExecutionResult`
- `SettledTrade`
- `PostmortemReport`

Se un agente non lascia un artefatto leggibile, l’architettura si sta degradando.

### 2.4 Adapters obbligatori
- Nessuna chiamata HTTP diretta nella business logic.
- Nessuna libreria venue-specific in `domain/` o `agents/`.
- Tutte le integrazioni passano da interfacce/porte e adapter.

### 2.5 Dominio corretto
Nel nuovo progetto i concetti centrali sono:
- `market`
- `outcome`
- `probability`
- `edge`
- `bankroll`
- `settlement`
- `postmortem`

Non sono concetti centrali:
- `ticker`
- `ATR`
- `LONG/SHORT` come semantica universale
- `TP/SL` come modello dominante del dominio

---

## 3. Struttura del repository e responsabilità

```text
prediction-market-bot/
  AGENTS.md
  README.md
  pyproject.toml
  config/
    agents.yaml
    app.yaml
  docs/
    ANALISI_DETTAGLIATA.md
    REFACTOR_PLAN.md
    LEGACY_TO_TARGET_MAP.md
  src/
    prediction_market_bot/
      app/
      domain/
      interfaces/
      agents/
      orchestration/
      main.py
  tests/
```

### `config/`
Contiene la configurazione umana e machine-readable.

- `agents.yaml`: catalogo agenti, soglie, metriche, policy
- `app.yaml`: runtime, storage, scheduler, observability, feature flags

### `docs/`
Contiene documentazione di prodotto e migrazione.
Qui si descrive il **perché** delle scelte e il piano operativo.

### `src/prediction_market_bot/domain`
Contiene il linguaggio del dominio:
- enum
- dataclass
- value objects impliciti
- modelli del ciclo agentico

Qui non si fanno chiamate esterne e non si legge config runtime da file.

### `src/prediction_market_bot/interfaces`
Contiene le porte astratte:
- market data provider
- research source
- trade executor
- repository/event sink

Qui non si mette business logic di decisione.

### `src/prediction_market_bot/agents`
Contiene la logica degli agenti.
Ogni agente:
- riceve input tipizzato
- restituisce output tipizzato
- non allena modelli
- non dipende da framework web
- non scrive direttamente SQL non mediato

### `src/prediction_market_bot/orchestration`
Contiene il coordinamento del flusso.
Qui si orchestra, ma non si duplicano regole rischio, prediction o execution.

### `tests/`
Contiene test unitari, smoke test e test di integrazione controllati.

---

## 4. Catalogo agenti e mandato operativo

## 4.1 ScanAgent
### Responsabilità
- leggere i mercati attivi
- filtrare per liquidità, volume, spread, tempo a risoluzione
- rilevare price move anomali
- emettere candidati ordinati

### Input
- `Sequence[MarketSnapshot]`

### Output
- `list[MarketCandidate]`

### Non fa
- scraping social
- sentiment analysis
- sizing bankroll
- esecuzione ordini

### KPI
- `candidates_count`
- `rejection_count`
- `avg_scan_score`
- `avg_liquidity_selected`

## 4.2 ResearchAgent
### Responsabilità
- interrogare fonti parallele
- raccogliere evidenze e sintesi
- pesare credibilità delle fonti
- stimare narrative strength e disagreement

### Input
- `MarketCandidate`

### Output
- `ResearchPacket`

### Non fa
- order placement
- bankroll sizing
- override del mercato senza evidenze

### KPI
- `findings_count`
- `evidence_strength`
- `disagreement_score`
- `source_coverage_ratio`

## 4.3 PredictionAgent
### Responsabilità
- fondere mercato, narrativa e segnali strutturali
- stimare fair probability
- stimare edge vs prezzo di mercato
- attribuire confidence

### Input
- `MarketCandidate`
- `ResearchPacket`

### Output
- `PredictionResult`

### Non fa
- sizing
- execution
- persistenza diretta

### KPI
- `fair_yes_prob`
- `edge_bps`
- `confidence`
- `calibration_error` (offline)

## 4.4 RiskAgent
### Responsabilità
- applicare le policy di bankroll
- calcolare fractional Kelly
- imporre cap per posizione/evento/categoria
- bloccare trade con edge o confidence insufficienti

### Input
- `PredictionResult`
- bankroll state / exposure state

### Output
- `RiskDecision`

### Non fa
- cambiare fair probability
- riscrivere le regole del prediction engine

### KPI
- `approved_rate`
- `stake_usd`
- `blocked_by_reason`
- `avg_kelly_fraction`

## 4.5 ExecutionAgent
### Responsabilità
- tradurre la decisione in `OrderIntent`
- inviare l’ordine al venue adapter
- restituire l’esito di submission/fill/rejection

### Input
- `RiskDecision`
- `PredictionResult`

### Output
- `ExecutionResult`

### Non fa
- sizing autonomo
- decisioni narrative
- bypass delle guardie rischio

### KPI
- `submitted_count`
- `fill_rate`
- `reject_rate`
- `avg_slippage_bps`

## 4.6 SettlementAgent
### Responsabilità
- leggere lo stato di risoluzione del mercato
- chiudere il lifecycle del trade
- produrre PnL finale e artefatto di settlement

### Input
- `ExecutionResult`
- market resolution state

### Output
- `SettledTrade`

### Non fa
- aprire ordini nuovi
- reinterpretare la policy rischio

### KPI
- `settled_count`
- `settled_pnl`
- `resolution_latency`

## 4.7 PostmortemAgent
### Responsabilità
- attivarsi dopo loss o comportamento inatteso
- classificare root cause
- produrre action item
- aggiornare backlog operativo/dataset backlog

### Input
- `SettledTrade`
- `PredictionResult`
- `ResearchPacket`

### Output
- `PostmortemReport`

### Non fa
- mutare direttamente modelli in produzione
- introdurre regole silenziose non documentate

### KPI
- `root_causes_count`
- `action_items_count`
- `repeat_failure_rate`

---

## 5. Artefatti obbligatori per ogni run

Ogni esecuzione completa o parziale deve poter essere ricostruita tramite:
- `run_id`
- timestamp UTC
- agent version / config version
- input snapshot
- output artefacts
- decision reasons
- failure reason se presente

### Minimo auditabile
Per ogni trade approvato devono esistere almeno:
- snapshot del mercato
- research packet
- prediction result
- risk decision
- execution result
- settlement record
- eventuale postmortem

---

## 6. Regole di configurazione

### 6.1 File di configurazione
- `config/agents.yaml` definisce catalogo e policy agenti.
- `config/app.yaml` definisce environment, storage, scheduling e observability.

### 6.2 Priorità
Ordine di priorità consigliato:
1. valori hard-coded nei test
2. config di ambiente locale
3. `app.yaml`
4. `agents.yaml`
5. override runtime espliciti e tracciati

### 6.3 Cosa non fare
- aggiungere soglie in più file diversi
- avere threshold duplicati in codice e YAML senza source of truth
- introdurre parametri “temporanei” non documentati

---

## 7. Policy di naming e stile

### Codice
- codice in inglese
- classi agenti con suffisso `Agent`
- dataclass del dominio con nomi sostantivi chiari
- enum per stati e side

### Documentazione
- documentazione operativa in italiano
- commenti brevi, non narrativi, su scelte non ovvie

### Naming semantico
Usare:
- `market_id`
- `selected_side`
- `fair_yes_prob`
- `stake_usd`
- `resolution_state`

Evitare nel nuovo dominio, se non come adapter legacy:
- `ticker`
- `long`
- `short`
- `tp/sl`

---

## 8. Standard per adapter esterni

Ogni adapter reale deve avere:
1. interfaccia o protocol dedicato
2. implementazione reale
3. implementazione fake/mock
4. smoke test
5. gestione timeout/retry
6. normalizzazione degli errori

### Adapter market data
Devono produrre snapshot normalizzati con:
- prezzo YES
- liquidità
- volume 24h
- spread bps
- hours to resolution
- status
- venue

### Adapter research
Ogni finding deve includere almeno:
- tipo fonte
- nome fonte
- summary
- sentiment
- credibilità
- URL se disponibile

### Adapter execution
Devono normalizzare almeno:
- submission accepted / rejected
- fill price
- order id venue
- rejection reason
- dry-run mode

---

## 9. Persistenza e storage

Il progetto nuovo deve prevedere almeno quattro classi logiche di storage:

1. **Operational DB**
   - markets
   - predictions
   - risk decisions
   - orders
   - settlements

2. **Research store**
   - findings raw
   - summaries
   - stance metadata

3. **Audit/event log**
   - eventi di pipeline
   - errori normalizzati
   - transizioni di stato

4. **Offline research store**
   - dataset per training/calibrazione
   - benchmark e backtest

### Regola fondamentale
Gli agenti non devono conoscere i dettagli del backend di storage.

---

## 10. Osservabilità minima obbligatoria

Per ogni agente servono:
- log strutturati
- metriche base
- contatore errori
- durata esecuzione
- contatore skip/reject/block

### Dashboard minima corretta
La control plane deve mostrare dati veri, non placeholder hard-coded.
Non sono ammessi endpoint che fingono stato runtime se non collegati alla realtà operativa.

---

## 11. Testing policy

### Livelli richiesti
- unit test per agenti puri
- smoke test end-to-end in dry-run
- integration test per adapter reali critici
- regression tests su bug fix rilevanti

### Criteri minimi
Una feature non è completa senza:
- contratto input/output
- implementazione
- fallback/failure mode documentato
- almeno uno smoke o unit test

---

## 12. Workflow di sviluppo

### Prima di aggiungere una feature
Verificare:
- in quale bounded context vive
- quale artefatto produce
- quale agente ne è owner
- quale configurazione la governa
- quali failure mode introduce

### Pull request ideale
Deve includere:
- motivo della modifica
- impatto su artefatti o contratti
- note di migrazione config se necessarie
- test eseguiti

---

## 13. Regole di migrazione dal legacy

### Ammesso
- riuso di concetti buoni
- riuso di logging pattern
- riuso di notifier e explainability come concetto

### Vietato
- import runtime dal vecchio `train_*.py`
- trascinare nel nuovo progetto gli script root come package
- copiare il vecchio schema `ticker -> signal -> long/short` come dominio principale
- mantenere due decision engine attivi in parallelo

### Pattern corretto
Dal legacy si estrae una capacità e la si ricolloca nel bounded context giusto.

---

## 14. Failure modes da gestire esplicitamente

- market data incompleta
- market status ambiguo
- fonti research non disponibili
- confidence insufficiente
- edge insufficiente o negativo
- esposizione già satura
- ordine rifiutato dal venue
- fill parziale o non riconciliato
- settlement ambiguo
- postmortem non eseguibile

Per ogni failure mode serve:
- stato esplicito
- reason code leggibile
- audit event
- comportamento di fallback chiaro

---

## 15. Definition of done

Una feature è completa solo se include:
- design nel bounded context corretto
- contratto input/output
- implementazione coerente
- configurazione tracciabile
- audit minimo
- test minimo
- documentazione operativa necessaria

---

## 16. Anti-pattern da evitare

- mega file monolitici
- logica business dentro API/UI
- training online nel runtime
- placeholder permanenti usati come stato reale
- duplicazione di soglie in tre posti diversi
- adapter che ritornano payload raw senza normalizzazione
- naming legacy trascinato nel dominio nuovo

---

## 17. Regola finale

Se emerge un dubbio di progettazione, scegliere sempre la soluzione che:
- riduce accoppiamento
- aumenta auditabilità
- preserva contratti chiari
- impedisce il ritorno del monolite legacy
