# Project Journal

Track key design choices, learnings, and next actions.

---

## 2025-05-15  
- Initialized `macro_playbook_agent` package.  
- Defined vision: build MacroAnalystAgent layers (data → signals → regimes → playbooks).

## 2025-05-16  
- Collected 13 macro indicators; saved raw CSVs; created `macro_catalog.csv` metadata.  
- Wrote individual ingestion scripts (`fetch_cpi.py`, `fetch_iip.py`…).

## 2025-05-17  
- Built `verify_data.py`; ingested all series into Postgres; confirmed via `verify_data`.

## 2025-05-18  
- Created `transforms.py` with core functions.  
- Drafted initial `signal_catalog.csv` for CPI; implemented and tested `trend_engine.py`.

## 2025-05-19  
- Added IIP signals; removed hard-coded CPI filter; validated outputs.  
- Discussed rationale for YoY, MA3, Δ, Z-score windows.

## 2025-05-20  
- Integrated Crude oil: daily→monthly avg, 30-day vol, resample logic.  
- Updated catalog & engine; ran `test_crude.py`; validated monthly avg & vol.

## 2025-05-21  
- Generalized engine to use `analysis_freq`, `agg_method`, `effective_lag_days` from catalog.  
- Populated full `signal_catalog.csv` for all 20+ series.

## 2025-05-22  
- Wrote and ran `test_trend_engine.py`; end-to-end validation with zero error.  
- Debated regime classification approaches; decided to anchor to known historical periods first.

## 2025-05-23  
- Finalized manifest files (`catalog_status.csv`, `signal_manifest.csv`, `feature_backlog.md`).  
- Pausing macro layer to begin building stock & sector data layers next.

---

_Notes & Decisions_  
- Always index signals on **actionable dates**, not raw period-ends.  
- Maintain separation: resampling/lagging in engine, transforms in `transforms.py`.  
- Next major milestone: regime classifier & narrative integration.
- MOVE DATABASE TO DOCKER 