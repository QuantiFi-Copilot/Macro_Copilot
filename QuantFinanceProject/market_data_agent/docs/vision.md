# MarketDataAgent Vision

## 1. Purpose  
Provide a single, reliable “source of truth” for all Indian-equity price and volume data, plus a rich library of derived signals, so that every downstream agent can ask for exactly the data or feature it needs without re-calculating or inconsistently processing raw market feeds.

## 2. Core Responsibilities  
- **Ingestion**  
  - Download and upsert daily EOD OHLCV for a defined universe (e.g. Nifty 100)  
  - Ingest corporate-action metadata (splits, dividends) to adjust historical prices  
  - Pull index series (Nifty 50, India VIX) as benchmarks  
- **Storage**  
  - Maintain a normalized `price_series` table in Postgres/TimescaleDB  
  - Store a `signal_library` table with daily feature values per ticker  
- **Signal Generation**  
  Compute & persist every trading day a standard set of signals such as:  
  - **Returns:** 1 D, 5 D, 21 D, 63 D  
  - **Trend:** 50 D vs. 200 D MA crossover flags, Bollinger band positions  
  - **Volatility:** 20 D realized vol, 14 D ATR, rolling beta vs. Nifty  
  - **Momentum:** 3-month & 6-month total returns  
  - **Liquidity:** volume spike flags, turnover ratio (volume/float)  
  - **Valuation:** trailing P/E, P/E z-score, revenue‐growth yoy  
  - **Oscillators:** RSI 14 D, MACD histogram, on-balance volume divergence  

## 3. API & Usage  
- **Batch mode** (daily cron):  
  1. `fetch_prices()` → upsert raw series  
  2. `compute_signals()` → write `signal_library`  
- **Query mode** (sync/async):  
  ```python
  price_agent.query(
      ticker="RELIANCE.NS",
      fields=[
        "return_5D", "realized_vol_20D", "PE_zscore", "RSI_14D"
      ]
  )
