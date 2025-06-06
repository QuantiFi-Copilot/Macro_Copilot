# Macro Feature Backlog

## ✅ Completed Features

- **Project scaffolding**  
  - Created `macro_playbook_agent/` Python package with `__init__.py`, `setup.py`  
- **Raw data ingestion**  
  - Wrote `fetch_*.py` scripts for CPI, IIP, WPI, GDP (real & nominal), PFCE, MBIN, Repo, USDINR, 10Y, Gold, Crude  
  - Populated `data/raw/*.csv` and ingested into `macro_series` Postgres table  
  - Verified ingestion end-to-end with `verify_data.py`  
- **Signal catalog**  
  - Built initial `signal_catalog.csv` for CPI  
  - Extended to IIP, Crude, Gold, USDINR, Repo, MBIN, WPI, 10Y, GDP, PFCE  
  - Added metadata columns: `analysis_freq`, `agg_method`, `effective_lag_days`  
- **Transforms library** (`transforms.py`)  
  - `compute_yoy`, `compute_delta`, `compute_ma`, `compute_zscore`, `compute_volatility`  
- **Trend engine** (`trend_engine.py`)  
  - Fetch + resample raw series to real-world cadence (monthly, quarterly, etc.)  
  - Shift by publication lags (e.g. CPI +10d, GDP +45d)  
  - Apply transforms via catalog metadata  
  - Handle daily series (Crude, Gold, USDINR, 10Y) into monthly buckets  
- **Testing**  
  - `test_crude.py`: validated monthly averages and 30-day vol for crude  
  - `test_trend_engine.py`: end-to-end checks (all signals vs manual) with zero error  

## 📦 Immediate Next Steps

1. **Regime classification module**  
   - Build `regime_classifier.py` (rule-based v0.1 against CPI_Z, IIP_Z, Crude_Delta, Repo_Delta)  
2. **EDA & clustering**  
   - Notebook for correlations, PCA, k-means/HMM on full signal matrix  
3. **Visualization layer**  
   - Quick Streamlit/notebook to plot signals + regimes  
4. **Ground-truth regime labels**  
   - Create `regime_labels.csv` with 4–5 known periods (2008, 2013, 2020, 2022)  

## 🔮 Longer-Term Macro Goals

- **Supervised regime classifier** trained on labeled periods  
- **Narrative engine** (LLM prompts) to generate playbooks per regime  
- **Asset-mapping layer**: map regimes → sector/asset weights  
- **Alert system**: real-time signal thresholds + notifications  
- **API/CLI**: expose signals & regimes to other modules (stocks, sectors)  
