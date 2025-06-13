# EarningsAgent Vision

## 1. Purpose

To serve as the system's core **fundamental analysis engine**. The EarningsAgent's primary directive is to ingest, parse, and analyze corporate earnings releases—transforming both structured financial data and unstructured management commentary into a coherent, multi-faceted assessment of a company's health, trajectory, and underlying narrative.

It moves beyond simply reporting a company's EPS. It aims to answer the critical questions a human analyst would:
* Did the company *really* beat expectations, or was it due to one-off accounting tricks?
* What is the underlying tone of the management? Are they confident or evasive?
* What are the key risks and opportunities they are highlighting for the future?
* How does this quarter's performance fit into the company's multi-year story?

The ultimate output is not just data, but a set of **quantified signals and qualitative flags** that other agents, particularly the `LLM + Intuition Engine` and `StrategyGenerator`, can consume to make informed decisions.

## 2. Core Responsibilities

The agent's duties are broken down into four key functions: Ingestion, Analysis, Scoring, and Storage.

### A. Ingestion Layer

The agent must be architected to reliably source heterogeneous data types from multiple providers. Redundancy is key.

* **Structured Financial Data:**
    * **Source:** Programmatically access quarterly and annual financial statements (Income Statement, Balance Sheet, Cash Flow) via robust APIs (e.g., Financial Modeling Prep, Alpha Vantage, or direct from exchange data vendors).
    * **Data Points:** Revenue, Net Income, EPS (Diluted), Operating Margin, Free Cash Flow, Debt-to-Equity, etc.
    * **Consensus Data:** Ingest pre-earnings analyst consensus estimates (EPS, Revenue) and post-earnings revisions to compute "surprise" factors.

* **Unstructured Text Data:**
    * **Source:** Fetch earnings call transcripts, official press releases, and the Management Discussion & Analysis (MD&A) section from annual reports.
    * **Format:** The system must handle various formats like JSON (from APIs), HTML (from web scraping), and PDFs.

### B. Analysis & Signal Generation Layer

This is the analytical core where raw data is converted into intelligence. This layer will be heavily reliant on both traditional financial calculations and LLM-driven interpretation.

* **Quantitative Signals (The "What"):**
    * **Earnings Surprise:** `(Actual EPS - Consensus EPS) / |Consensus EPS|`
    * **Revenue Surprise:** `(Actual Revenue - Consensus Revenue) / Consensus Revenue`
    * **Guidance Analysis:** Quantify the change in company guidance (e.g., `% change in guided revenue/EPS` from previous quarter).
    * **Growth Trajectory:** Calculate YoY, QoQ, and 3-year CAGR for key line items.
    * **Quality of Earnings:** Identify the ratio of Cash Flow from Operations to Net Income. A low ratio can be a red flag.
    * **Margin Velocity:** Calculate the rate of change (first and second derivatives) of gross, operating, and net margins.

* **Qualitative & Behavioral Signals (The "Why"):**
    * **Transcript Sentiment Analysis:** Score the sentiment of the prepared remarks and, separately, the Q&A session. A divergence is often a signal.
    * **Topic & Keyword Extraction:** Identify and flag key themes discussed (e.g., `inflationary_pressure`, `ai_investment`, `share_buybacks`, `supply_chain_normalization`).
    * **Forward-Looking Statement Analysis:** Isolate and classify management's statements about the future as `strong-positive`, `cautious-positive`, `neutral`, `cautious-negative`, or `strong-negative`.
    * **Evasion & Deception Detection:** Use LLM analysis on the Q&A portion to flag non-answers, topic pivots, or overly complex language, which can indicate management is obscuring a weakness.
    * **Analyst-Question Analysis**: Analyze the questions being asked by analysts on the call. Are they focused on a specific problem area? This provides a view into what "the street" is worried about.

### C. Scoring & Output Layer

The agent synthesizes the above signals into a standardized, machine-readable output for each earnings event.

* **Earnings Quality Score (EQS):** A composite score (e.g., 1-100) combining surprise metrics, margin stability, and cash flow quality.
* **Narrative Momentum Score (NMS):** A composite score based on sentiment, guidance strength, and the absence of negative flags.
* **Red Flag Array:** A list of binary flags for specific issues detected (e.g., `[inventory_buildup, high_opex_growth, negative_guidance]`).

## 3. Storage

The data architecture must support both time-series analysis and complex qualitative queries.

* **PostgreSQL / TimescaleDB:**
    * `quarterly_fundamentals`: A table storing all structured financial data, indexed by ticker and date.
    * `earnings_event_signals`: A table to store the synthesized scores and flags (EQS, NMS, Red Flags) for each earnings event.
* **Vector Database (e.g., Chroma, Pinecone):**
    * Store embeddings of earnings call transcripts and MD&A sections. This allows for powerful semantic search capabilities. For example: *"Find all IT companies that discussed client budget cuts in their latest earnings call."*

## 4. API & Usage

The **EarningsAgent** must expose a clean, logical API for other system components to query.

* **Batch Mode (Post-Earnings):**
    1.  On a schedule, the agent scans for companies that have just reported.
    2.  Triggers the full Ingestion -> Analysis -> Scoring pipeline.
    3.  Persists the output to the databases.
    4.  Publishes a message (e.g., via a message queue like RabbitMQ or a Prefect flow state change) that a new earnings analysis is complete for a specific ticker.

* **Query Interface:**
    * `get_earnings_summary(ticker, quarter)`: Returns the fully scored and analyzed output for a given company and period.
    * `get_fundamental_history(ticker, metrics=['revenue_yoy', 'operating_margin'])`: Retrieves historical time-series for specific quantitative signals.
    * `search_earnings_narrative(query_string)`: Executes a semantic search against the vector database to find relevant commentary across the entire stock universe.