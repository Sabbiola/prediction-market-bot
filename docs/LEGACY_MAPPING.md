# Mappa Legacy

## 10. Mappatura precisa legacy -> target

| Legacy | Funzione reale | Target | Azione |
|---|---|---|---|
| `neurosynth/bot.py` | orchestrazione monolitica | `orchestration/coordinator.py` + agenti | splittare |
| `core/database.py` | DB runtime | nuovo schema prediction-market | ridisegnare |
| `ingestion/price_fetcher.py` | OHLCV ticker | market scanner provider | concetto riusabile, codice no |
| `ingestion/news_fetcher.py` | news headline fetch | social/news/rss adapters | rifare/adattare |
| `intelligence/llm_analyzer.py` | sentiment/event extraction | research pipeline | riusare idea, astrarre |
| `intelligence/gating.py` | decision logic | prediction decision engine | unificare o sostituire |
| `intelligence/gating_engine.py` | model-aware gating | prediction/calibration service | rifattorizzare |
| `intelligence/ml_predictor.py` | feature engineering + ML | offline training + online inference | separare |
| `execution/risk_calculator.py` | sizing ATR-based | edge/Kelly sizing | riscrivere dominio |
| `execution/position_monitor.py` | stop/TP monitor | settlement monitor | sostituire logica centrale |
| `execution/gtrade_executor.py` | live trade adapter | venue executor prediction market | rimpiazzare |
| `core/signal_logger.py` | decision logging | audit/event log | riusare concetto |
| `dashboard/app.py` | operator UI | API/UI separate | spezzare |
| `ticker_configs.py` | ticker-specific config | market/venue/agent config | sostituire |
