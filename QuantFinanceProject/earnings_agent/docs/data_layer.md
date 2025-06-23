# EarningsAgent: Data Layer Architecture

## 1. Guiding Philosophy

The `EarningsAgent`'s data layer is the heart of the entire quantitative system. Its primary directive is to achieve **data sovereignty**: to build a proprietary, accurate, and comprehensive repository of **quantitative fundamental data** for all Indian listed companies.

We explicitly reject a reliance on third-party APIs as the primary source of truth due to their known limitations in coverage, accuracy, and standardization for the Indian market. Instead, we embrace the core challenge of processing raw, unstructured source documents (corporate filings from exchanges and company websites) to extract hard financial numbers.

## 2. Core Architectural Principles

Our architecture is built on three core principles designed to handle a heterogeneous and unreliable data environment:

1.  **The Cascading Pipeline:** We will use a single, intelligent, multi-stage pipeline to process every company. This pipeline attempts the cheapest and most reliable data extraction method first—**starting with XBRL filings**—and only cascades to more complex, expensive methods like PDF parsing when necessary.
2.  **The Core + Satellite Schema:** We will not use a single, rigid database table. Our schema will consist of a lean "Core" table for universally standard metrics and a flexible "Satellite" table (using PostgreSQL's `JSONB` type) to store the rich, industry-specific, and non-standard **quantitative KPIs**.
3.  **The Source of Truth Hierarchy:** We will programmatically encode the reliability of each data source. The official corporate filing (XBRL or PDF) is the ultimate source of truth. Our data ingestion logic will use this hierarchy to automatically improve the quality of our database over time.

## 3. Identified Challenges & Solutions

This section details every anticipated challenge in building this data layer and the specific architectural solution designed to solve it.

### Challenge 1: Unreliable & Incomplete API Coverage
* **Problem:** No single third-party API provides accurate, timely, and complete fundamental data for the entire universe of Indian-listed stocks.
* **Solution:** The **Cascading Pipeline** architecture. APIs will be treated as a low-priority, "convenience" source of data. Our primary focus is on parsing official source documents.

### Challenge 2: Heterogeneous Data Formats & Limited XBRL Availability
* **Problem:** Financial data is published in a variety of formats: XBRL, text-based PDFs, and image-based (scanned) PDFs. A single approach is not feasible.
* **Solution:** The **Cascading Pipeline** is designed for this reality. It will always attempt to find and parse the XBRL filing first. If XBRL is not available, the system automatically falls back to the PDF parsing stages.

### Challenge 3: Inconsistent Labeling & Semantics
* **Problem:** The same financial concept has different names across companies (e.g., "Revenue from Operations," "Total Income").
* **Solution:** This is primarily solved by **XBRL**, which uses a standardized `Ind-AS` taxonomy. For non-XBRL sources (PDFs), we will implement a **Semantic Mapping Layer** to map various labels to our internal, standardized concepts.

### Challenge 4: Non-Standard, Industry-Specific KPIs
* **Problem:** A bank's report contains quantitative metrics (NPA, NIM) that a manufacturing company's report does not. A single, rigid database schema cannot capture this diversity.
* **Solution:** The **Core + Satellite Schema**. Universal metrics go into the `quarterly_fundamentals` (Core) table. All other industry-specific quantitative KPIs go into the `custom_kpis` (Satellite) table's `JSONB` field.

### Challenge 5: Inconsistent Financial Units
* **Problem:** Figures in reports are presented in `Lakhs`, `Crores`, `Millions`, etc.
* **Solution:** **XBRL** data contains explicit unit context. For PDF documents, we will build a **Context-Aware Unit Normalizer** module within the parsing layer to convert every figure to its absolute INR value.

### Challenge 6: Data Accuracy & The Source of Truth
* **Problem:** Data from third-party APIs can be inaccurate. The official PDF/XBRL filing is the ultimate source of truth.
* **Solution:** Our **Source of Truth Hierarchy** will be strictly enforced by our database upsert logic.

#### Source of Truth Hierarchy
The ingestion pipeline will use the following priority order.

| Priority | Source Tag (`source`) | Description |
|:---:|:---|:---|
| 1 | `MANUAL_VERIFIED` | Data that has been manually entered or verified by a human operator. |
| 2 | `XBRL_NSE` / `XBRL_BSE` | Machine-readable data parsed directly from official XBRL filings. |
| 3 | `PDF_OCR_LLM` | Data extracted from a scanned PDF using our OCR and LLM pipeline. |
| 4 | `PDF_TEXT_EXTRACT`| Data extracted from a text-based PDF using parsers. |
| 5 | `API_PRIMARY` | Data from a primary, trusted third-party API. |

### Challenge 7: Parser & Scraper Breakage
* **Problem:** Websites change their layout and PDF formats evolve, which will break our parsers.
* **Solution:** **Robust Monitoring & Alerting** via Prefect flows.

### Challenge 8: Cost Management of LLMs
* **Problem:** Using advanced LLMs for PDF table extraction can become expensive.
* **Solution:** The **Cascading Pipeline** is our primary cost-control mechanism, using LLMs only as a final resort.

### Challenge 9: Handling Corporate Actions
* **Problem:** Corporate actions can lead to companies restating historical financials.
* **Solution:** We will add a `version` integer column to our `quarterly_fundamentals` table to handle restatements.

## 4. Proposed Database Schema
*(The schema remains unchanged as it is already designed for this purpose)*
```sql
CREATE SCHEMA IF NOT EXISTS earnings_data;

CREATE TABLE IF NOT EXISTS earnings_data.raw_documents (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    doc_type VARCHAR(50) NOT NULL, -- 'QUARTERLY_RESULTS_PDF', 'XBRL_INSTANCE'
    source_url TEXT,
    local_path TEXT,
    raw_text_content TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS earnings_data.quarterly_fundamentals (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    period VARCHAR(10) NOT NULL,
    filing_date DATE,
    standard_revenue BIGINT,
    standard_net_income BIGINT,
    total_assets BIGINT,
    total_liabilities BIGINT,
    operating_cash_flow BIGINT,
    source VARCHAR(50) NOT NULL,
    version INT DEFAULT 1 NOT NULL,
    raw_document_id INTEGER REFERENCES earnings_data.raw_documents(id),
    UNIQUE(ticker, fiscal_date, version)
);

CREATE TABLE IF NOT EXISTS earnings_data.custom_kpis (
    id SERIAL PRIMARY KEY,
    fundamental_id INTEGER NOT NULL REFERENCES earnings_data.quarterly_fundamentals(id) ON DELETE CASCADE,
    kpi_data JSONB NOT NULL,
    UNIQUE(fundamental_id)
);

The Revised "Source of Truth Hierarchy"
Our data architecture can now be expanded to a multi-tiered pipeline that ingests and reconciles data from all these sources:
Tier 1 (The Fast Layer): Data from Screen Scrapers or a Third-Party API.
This gives us immediate numbers on earnings day and deep history.
We store this with a source tag like SCRAPED_PROVISIONAL.
Tier 2 (The Structured Layer): XBRL Data (when it arrives 1-2 months later).
This is an official source. Its data will be used to overwrite and validate the provisional Tier 1 data.
source tag: XBRL_NSE.
Tier 3 (The Ground Truth Layer): PDF Extraction, guided by our validation engine and ML model.
This is the ultimate source of truth, used to correct any discrepancies in Tiers 1 and 2.
source tag: PDF_OCR_LLM.