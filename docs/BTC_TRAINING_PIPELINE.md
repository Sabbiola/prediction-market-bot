# BTC Up/Down Training Pipeline

## Scopo

Pipeline dedicata al training di modelli predittivi per i mercati Polymarket
**BTC Up/Down 5m** e **BTC Up/Down 15m** (es. `btc-updown-5m-*`, `btc-updown-15m-*`).

Obiettivo: produrre un artifact runtime con **PnL Sharpe positivo** su holdout,
superiore al benchmark `market_implied` (50%), utilizzando segnali tecnici
multi-exchange e deep learning ensemble.

Confine architetturale: questa pipeline e interamente offline.
Il runtime operativo consuma solo l'artifact JSON/PTH prodotto — non dipende
da nessuno script di training durante `run-once` o lo scheduler.

---

## Struttura file

```text
scripts/
  btc_fetch_ohlcv.py          # Fetch OHLCV Binance (1m/5m/15m/1h)
  btc_fetch_coinbase.py       # Fetch OHLCV Coinbase Exchange API (CB lead-lag)
  btc_fetch_derivatives.py    # Fetch funding rate, LS ratio, OI da Binance
  btc_train_v2.py             # Script training unificato (corrente)
  btc_train_model.py          # Script training v1 (legacy, mantenuto per reference)
  btc_train_exhaustive.py     # Grid search esteso v1 (legacy)

data/btc/
  ohlcv_5m.jsonl              # Binance BTC/USDT 5m OHLCV
  ohlcv_15m.jsonl             # Binance BTC/USDT 15m OHLCV
  coinbase_5m.jsonl           # Coinbase BTC-USD 5m OHLCV
  coinbase_15m.jsonl          # Coinbase BTC-USD 15m OHLCV
  derivatives.jsonl           # Funding rate, LS ratio, OI
  training_log.jsonl          # Storico run di training

data/models/v4/
  btc_5m_best.json            # Artifact principale per agents.yaml (5m)
  btc_5m_best_lgbm.model      # Modello LightGBM serializzato
  btc_5m_lstm.pth             # Pesi LSTM (se torch installato)
  btc_5m_tcn.pth              # Pesi TCN (se torch installato)
  btc_15m_best.json           # Artifact principale per agents.yaml (15m)
  ...

src/prediction_market_bot/agents/
  btc_feature_enricher.py     # Feature computation per il runtime live
```

---

## Flusso di training

```
1. Fetch dati (offline, resumable)
   btc_fetch_ohlcv.py       → data/btc/ohlcv_{interval}.jsonl
   btc_fetch_coinbase.py    → data/btc/coinbase_{interval}.jsonl
   btc_fetch_derivatives.py → data/btc/derivatives.jsonl

2. Build feature dataset (btc_train_v2.py)
   - Allineamento timestamp Binance + Coinbase per indice
   - 32 feature (v2): Binance tecnici + CB lead-lag + derivatives + gap_open + rsi_1h
   - Label: 1 se close > open (candela UP), 0 altrimenti

3. Temporal split 70/15/15 (no leakage)
   - Scaler fittato SOLO su train set
   - Val set per selezione modello
   - Test set blindato (mai usato per model selection, solo per report finale)

4. Hyperparameter search per algoritmo
   - Se Optuna installato: Bayesian TPE search con walk-forward CV 3-fold
   - Altrimenti: grid search su configurazioni predefinite
   - Metrica selezione: pnl_sharpe (non AUC)

5. Training modelli
   - LightGBM (Optuna, 50 trial)
   - XGBoost   (Optuna, 50 trial)
   - CatBoost  (Optuna, 40 trial)
   - Logistic Regression (Optuna, 30 trial, elasticnet)
   - LSTM      (lr=3e-4, early stopping patience=10, grad clip=0.5)
   - TCN       (lr=3e-4, early stopping patience=10, grad clip=0.5)

6. OOF Stacking (5-fold)
   - Meta-LR allenata su predizioni out-of-fold dei modelli base
   - Impara quando fidarsi di ciascun modello

7. Weighted ensemble
   - Media pesata per pnl_sharpe di ogni modello
   - Peso 0 per modelli con sharpe negativo

8. Selezione best model per pnl_sharpe su val set

9. Calibrazione isotonic regression su val set

10. Export artifact
    - btc_{interval}_best.json (per agents.yaml)
    - btc_{interval}_best_lgbm.model / .pth
    - Metriche holdout nel JSON per audit

11. Auto-promotion check (solo con --live)
    - Gate: val_pnl_sharpe > 0.5, val_pnl > 0, n_trades >= 50, test_pnl > -0.02
    - Se tutti i gate passano: stampa istruzioni promozione (update manuale agents.yaml)
```

---

## Feature set v2 (32 feature)

### Binance tecnici (20)
| Feature | Descrizione |
|---|---|
| `f_btc_prev_return_1c` | Return candela precedente |
| `f_btc_prev_return_3c` | Return 3 candele (15m a 5m) |
| `f_btc_prev_return_6c` | Return 6 candele (30m) |
| `f_btc_prev_return_12c` | Return 12 candele (1h) |
| `f_btc_prev_return_24c` | Return 24 candele (2h) |
| `f_btc_prev_return_48c` | Return 48 candele (4h) |
| `f_btc_rsi_7` | RSI(7) normalizzato [-1,1] |
| `f_btc_rsi_14` | RSI(14) normalizzato [-1,1] |
| `f_btc_rsi_21` | RSI(21) normalizzato [-1,1] |
| `f_btc_volume_ratio` | Volume corrente / MA20 volume (log) |
| `f_btc_volatility_6c` | Realized vol 6 candele (%) |
| `f_btc_volatility_12c` | Realized vol 12 candele (%) |
| `f_btc_volatility_24c` | Realized vol 24 candele (%) |
| `f_btc_bb_position_10` | Bollinger Band position (BB10), centrato a 0 |
| `f_btc_bb_position_20` | Bollinger Band position (BB20), centrato a 0 |
| `f_btc_macd` | MACD (EMA12-EMA26) normalizzato per prezzo |
| `f_decision_hour_utc_sin` | Ora UTC ciclica (sin) |
| `f_decision_hour_utc_cos` | Ora UTC ciclica (cos) |
| `f_decision_weekday_sin` | Giorno settimana ciclico (sin) |
| `f_decision_weekday_cos` | Giorno settimana ciclico (cos) |

### Coinbase lead-lag (6)
| Feature | Descrizione |
|---|---|
| `f_btc_cb_return_1c` | Return candela precedente Coinbase |
| `f_btc_cb_return_3c` | Return 3 candele Coinbase |
| `f_btc_cb_bn_spread_1c` | Spread CB-BN return 1c (CB guida) |
| `f_btc_cb_bn_spread_3c` | Spread CB-BN return 3c |
| `f_btc_cb_vol_dominance` | Dominanza volume CB vs BN |
| `f_btc_cb_momentum_lead` | Momentum CB in anticipo su BN |

Le feature CB sono 0.0 se il file Coinbase non e disponibile (graceful degradation).

### Derivatives (4)
| Feature | Descrizione |
|---|---|
| `f_btc_funding_rate` | Funding rate futuro (proxy sentiment overextension) |
| `f_btc_ls_ratio_log` | Long/Short ratio (log) |
| `f_btc_taker_ratio_log` | Taker buy/sell ratio (log) |
| `f_btc_oi_change_pct` | Variazione Open Interest (%) |

Le feature derivatives sono 0.0 se il file non e disponibile.

### Cross-timeframe e gap (2)
| Feature | Descrizione |
|---|---|
| `f_btc_gap_open` | `(curr_open - prev_close) / prev_close` — gap all'apertura |
| `f_btc_rsi_1h` | RSI(14) su barre 1h resampleate dai 5m |

---

## Parity con il runtime live

**Regola fondamentale:** le feature calcolate in `btc_train_v2.py` (training)
e in `btc_feature_enricher.py` (runtime) devono essere identiche.

Bug gia corretti:
- **Scaler leakage** (fix 2026-04): scaler ora fittato solo su train set
- **Time feature mismatch** (fix 2026-04): enricher ora usa `open_time_ms`
  dell'ultima candela completata invece di `datetime.now()` — coerente con training

`feature_schema_version: btc-v2-5m` / `btc-v2-15m` nel JSON artifact identifica
il set di feature v2. Il runtime valida la versione schema prima dell'inferenza.

---

## Dipendenze

```bash
# Minimo (solo tree models)
pip install numpy scikit-learn lightgbm xgboost catboost

# Raccomandato (Bayesian search)
pip install optuna

# Deep learning (LSTM + TCN)
pip install torch  # CPU build:
# pip install torch --index-url https://download.pytorch.org/whl/cpu

# Fetch dati (gia nell'env base)
pip install requests
```

---

## Comandi operativi

```bash
# 1. Fetch dati (richiede rete non aziendale — Binance/Coinbase bloccati da alcune reti)
python scripts/btc_fetch_ohlcv.py --interval 5m --months 6
python scripts/btc_fetch_ohlcv.py --interval 15m --months 6
python scripts/btc_fetch_coinbase.py --interval 5m --months 6
python scripts/btc_fetch_coinbase.py --interval 15m --months 6
python scripts/btc_fetch_derivatives.py --months 1  # limite API: 30gg

# 2. Training completo (con Optuna, ~30-60min su CPU)
python scripts/btc_train_v2.py --interval all --lookback-months 6 --optuna-trials 50

# 3. Training rapido (no deep learning, no Optuna)
python scripts/btc_train_v2.py --interval 5m --algorithms lgbm,xgb,lr

# 4. Live retrain giornaliero (per cron)
python scripts/btc_train_v2.py --interval all --live --lookback-months 6 --optuna-trials 30

# 5. Solo 15m (dopo aver gia trainato 5m)
python scripts/btc_train_v2.py --interval 15m --lookback-months 6
```

---

## Promozione artifact in agents.yaml

Dopo un training con holdout_pnl > 0 e val_pnl_sharpe > 0.5:

```yaml
# config/agents.yaml — sezione btc_updown_agent
model_artifact_path: data/models/v4/btc_5m_best.json
feature_schema_version: btc-v2-5m
min_edge_bps: 500          # abbassare da 750 quando calibrazione corretta
```

Per il mercato 15m:
```yaml
model_artifact_path: data/models/v4/btc_15m_best.json
feature_schema_version: btc-v2-15m
```

Verifica runtime dopo promozione:
```bash
python -m prediction_market_bot.main validate-startup \
  --config config/app.staging.yaml --agents-config config/agents.yaml --json
```

---

## Gate di promozione

| Gate | Soglia | Note |
|---|---|---|
| `val_pnl_sharpe` | > 0.5 | Minimo accettabile |
| `val_pnl` | > 0.0 | PnL cumulativo positivo su val |
| `val_n_trades` | >= 50 | Statisticamente significativo |
| `test_pnl` | > -0.02 | Holdout non catastrofico |

Il test set e **blindato** — non usato per selezione modello, solo per
il report finale. Promuovere solo se tutti i gate passano.

---

## Metriche di riferimento (baseline)

| Modello | Approccio | Note |
|---|---|---|
| `market_implied` | p=0.5 sempre | Benchmark di riferimento primario |
| `majority_class` | classe piu frequente | Baseline triviale |
| `btc_updown_5m_v1` | LR 10 feature, scaler con leakage | Modello legacy (superato) |
| `btc_updown_5m_v2` | Ensemble LGBM/XGB/CB/LR/LSTM/TCN, 32 feature | Target attuale |

Il modello v2 supera il benchmark se `holdout_pnl > 0` con almeno 50 trade.

---

## Note architetturali

- **Training offline obbligatorio**: nessun auto-tuning nel runtime
- **Sliding window**: `--lookback-months 6` bilancia stazionarieta e volume dati
- **Walk-forward CV**: la metrica di ottimizzazione Optuna e lo Sharpe medio su 3 fold
  temporali — molto piu onesta del single val split che sovrastima di 3-10x
- **LSTM/TCN**: utili come segnale ensemble ma non come modello primario su CPU
  (troppo lenti per retrain giornaliero); usare `--algorithms lgbm,xgb,catboost,lr`
  per training notturno veloce
- **Calibrazione**: isotonic regression su val set — corregge le probabilita grezze
  verso la frequenza empirica; necessaria per un `edge_bps` affidabile in runtime
