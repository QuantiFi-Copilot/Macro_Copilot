# EarningsAgent Vision

## 1. Purpose

To serve as the system's core **quantitative analysis engine**. The EarningsAgent's primary directive is to ingest, parse, and standardize structured and semi-structured corporate earnings releases. It transforms raw financial statements from diverse, inconsistent formats into a clean, auditable, and analysis-ready database of corporate performance.

Its purpose is to answer one question with utmost accuracy: **"What were the company's financial results?"** It is the foundational layer of quantitative truth upon which other, more interpretive agents will build.

## 2. Core Responsibilities

The agent's duties are broken down into four key functions: Ingestion, Analysis, Scoring, and Storage.

### A. Ingestion Layer

The agent must be architected to reliably source financial data from official filings.

* **Primary Sources:** Programmatically download quarterly and annual financial reports directly from exchange websites (BSE/NSE) and company investor relations pages. The system will prioritize XBRL filings and fall back to PDF documents.
* **Data Points:** Revenue, Net Income, EPS, Operating Margin, Free Cash Flow, Debt-to-Equity, and other key line items from the Income Statement, Balance Sheet, and Cash Flow Statement.
* **Consensus Data:** Ingest pre-earnings analyst consensus estimates (EPS, Revenue) to enable surprise calculations.

### B. Analysis & Signal Generation Layer

This is the analytical core where raw data is converted into intelligence.

* **Quantitative Signals (The "What"):**
    * **Earnings Surprise:** `(Actual EPS - Consensus EPS) / |Consensus EPS|`
    * **Revenue Surprise:** `(Actual Revenue - Consensus Revenue) / Consensus Revenue`
    * **Growth Trajectory:** Calculate YoY, QoQ, and 3-year CAGR for key line items.
    * **Quality of Earnings:** Calculate the ratio of Cash Flow from Operations to Net Income. A low ratio can be a red flag.
    * **Margin Analysis:** Calculate and track Gross, Operating, and Net Profit Margins over time.
    * **Fundamental Ratios:** Calculate standard ratios like P/E, P/S, P/B, EV/EBITDA, Debt/Equity after combining with data from the `MarketDataAgent`.

### C. Scoring & Output Layer

The agent synthesizes the above signals into a standardized, machine-readable output.

* **Earnings Quality Score (EQS):** A composite score (e.g., 1-100) combining surprise metrics, margin stability, and cash flow quality.
* **Red Flag Array:** A list of binary flags for quantitative issues detected (e.g., `[inventory_buildup, margin_degradation, high_debt_level, negative_ocf]`).

## 3. Storage

The data architecture must support robust time-series analysis of fundamental data.

* **PostgreSQL / TimescaleDB:**
    * `earnings_data.quarterly_fundamentals`: A table storing all standardized, "Core" financial data.
    * `earnings_data.custom_kpis`: A flexible table using a `JSONB` column to store industry-specific quantitative KPIs (e.g., Gross NPAs for banks).
    * `earnings_data.raw_documents`: A table to store the source XBRL/PDF files for audit and reprocessing.

## 4. API & Usage

The **EarningsAgent** must expose a clean, logical API for other system components to query quantitative data.

* `get_latest_earnings(ticker)`: Returns the most recent structured financial report.
* `get_fundamental_history(ticker, periods=8)`: Returns trailing 8 quarters of "Core" fundamental data.
* `get_custom_kpis(ticker, quarter)`: Returns the industry-specific KPIs for a given period.