# EarningsAgent: Data Layer Architecture

## 1. Guiding Philosophy

The `EarningsAgent`'s data layer is the heart of the entire quantitative system. Its primary directive is to achieve **data sovereignty**: to build a proprietary, accurate, and comprehensive repository of fundamental financial data for all Indian listed companies.

We explicitly reject a reliance on third-party APIs as the primary source of truth due to their known limitations in coverage, accuracy, and standardization for the Indian market. Instead, we embrace the core challenge of processing raw, unstructured source documents (corporate filings from exchanges and company websites).

This approach is harder but creates a durable competitive advantage. Our system's value will be derived from its unique ability to interpret the messiness of the real world, not from its ability to call a clean API. This document outlines the architecture required to succeed in this mission.

## 2. Core Architectural Principles

Our architecture is built on three core principles designed to handle a heterogeneous and unreliable data environment:

1.  **The Cascading Pipeline:** We will use a single, intelligent, multi-stage pipeline to process every company. This pipeline attempts the cheapest and most reliable data extraction method first—**starting with XBRL filings**—and only cascades to more complex, expensive methods like PDF parsing when necessary. This provides maximum efficiency and resilience.
2.  **The Core + Satellite Schema:** We will not use a single, rigid database table. Our schema will consist of a lean "Core" table for universally standard metrics and a flexible "Satellite" table (using PostgreSQL's `JSONB` type) to store the rich, industry-specific, and non-standard KPIs. This provides both structure and adaptability.
3.  **The Source of Truth Hierarchy:** We will programmatically encode the reliability of each data source. The official corporate filing (XBRL or PDF) is the ultimate source of truth. Our data ingestion logic will use this hierarchy to automatically improve the quality of our database over time by overwriting less reliable data with more reliable data.

## 3. Identified Challenges & Solutions

This section details every anticipated challenge in building this data layer and the specific architectural solution designed to solve it.

### Challenge 1: Unreliable & Incomplete API Coverage
* **Problem:** No single third-party API provides accurate, timely, and complete fundamental data for the entire universe of Indian-listed stocks.
* **Solution:** The **Cascading Pipeline** architecture. APIs will be treated as a low-priority, "convenience" source of data, not the source of truth. Our primary focus will be on building robust scrapers and parsers for official source documents.

### Challenge 2: Heterogeneous Data Formats & Limited XBRL Availability
* **Problem:** Financial data is published in a variety of formats: XBRL (since ~2017 for a subset of companies), text-based PDFs, image-based (scanned) PDFs, and HTML. A single approach is not feasible.
* **Solution:** The **Cascading Pipeline** is designed for this reality. It will always attempt to find and parse the XBRL filing first. If XBRL is not available for that company or historical period, the system automatically falls back to the PDF parsing stages without failure. This provides the speed and accuracy of XBRL when possible, and the comprehensive coverage of PDF parsing when not.

### Challenge 3: Inconsistent Labeling & Semantics
* **Problem:** The same financial concept is given different names across companies and industries (e.g., "Revenue from Operations", "Total Income", "Interest Earned" all conceptually mean "revenue").
* **Solution:** This is primarily solved by **XBRL**, which uses a standardized `Ind-AS` taxonomy to tag each financial fact. For non-XBRL sources (PDFs), we will implement a **Semantic Mapping Layer**—a configurable JSON file that maps various labels to our internal, standardized concepts.

### Challenge 4: Non-Standard, Industry-Specific KPIs
* **Problem:** A bank's report (NPA, NIM) is fundamentally different from a manufacturing company's report. A single, rigid database schema cannot capture this diversity.
* **Solution:** The **Core + Satellite Schema**. Universal metrics (`standard_revenue`, `standard_net_income`) go into the `quarterly_fundamentals` (Core) table. All other industry-specific ratios and KPIs go into the `custom_kpis` (Satellite) table's flexible `JSONB` field.

### Challenge 5: Inconsistent Financial Units
* **Problem:** Figures in reports are presented in `Lakhs`, `Crores`, `Millions`, etc., often without clear, machine-readable declarations.
* **Solution:** **XBRL** data contains explicit unit context, solving this problem cleanly. For PDF documents, we will build a **Context-Aware Unit Normalizer** module within the parsing layer that uses heuristics and LLMs to determine the correct multiplier and convert every figure to its absolute INR value.

### Challenge 6: Data Accuracy & The Source of Truth
* **Problem:** Data from third-party APIs can be inaccurate. The official PDF/XBRL filing is the ultimate source of truth.
* **Solution:** Our **Source of Truth Hierarchy** will be strictly enforced by our database upsert logic. Data from an official filing will always overwrite data from a less reliable source.

#### Source of Truth Hierarchy
The ingestion pipeline will use the following priority order. When processing data for a period that already exists in the database, the system will only overwrite the existing data if the new source has a **higher priority (a lower number)**.

| Priority | Source Tag (`source`) | Description |
|:---:|:---|:---|
| 1 | `MANUAL_VERIFIED` | Data that has been manually entered or verified by a human operator. Highest possible trust. |
| 2 | `XBRL_NSE` / `XBRL_BSE` | Machine-readable data parsed directly from official XBRL filings. The gold standard for automated data. |
| 3 | `PDF_OCR_LLM` | Data extracted from a scanned PDF using our most advanced OCR and LLM pipeline. |
| 4 | `PDF_TEXT_EXTRACT`| Data extracted from a text-based PDF using parsers. Less complex than OCR but still a direct source. |
| 5 | `API_PRIMARY` | Data from a primary, trusted third-party API. Used for convenience or back-filling, but always subordinate to official filings. |

### Challenge 7: Parser & Scraper Breakage
* **Problem:** Websites change their layout and PDF formats evolve, which will break our parsers and scrapers.
* **Solution:** **Robust Monitoring & Alerting**. Every Prefect flow will have clear success/failure states. Any failure will trigger an immediate alert to a human operator for review and maintenance.

### Challenge 8: Cost Management of LLMs
* **Problem:** Using advanced LLMs for PDF extraction can become prohibitively expensive if used indiscriminately.
* **Solution:** The **Cascading Pipeline** is our primary cost-control mechanism. LLMs are a final resort, used only when XBRL and direct PDF text extraction fail.

### Challenge 9: Handling Corporate Actions
* **Problem:** Corporate actions like mergers or de-mergers can lead to companies restating historical financials.
* **Solution:** We will add a `version` integer column to our `quarterly_fundamentals` table. When a corporate action is detected, we can trigger a backfill process to re-parse recent reports, incrementing the `version` number to signify the new, restated data.

## 4. Proposed Database Schema

```sql
-- The "Core" table for universally comparable financial data.
CREATE TABLE IF NOT EXISTS quarterly_fundamentals (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    period VARCHAR(10) NOT NULL,
    filing_date DATE,
    standard_revenue BIGINT,
    standard_net_income BIGINT,
    -- Add a few more universal concepts like total_assets, total_liabilities, operating_cash_flow
    source VARCHAR(50) NOT NULL, -- e.g., 'XBRL_NSE', 'PDF_OCR_LLM', 'API_FMP', 'MANUAL_VERIFIED'
    version INT DEFAULT 1 NOT NULL, -- For handling restatements
    raw_document_id INTEGER, -- Foreign key to a raw documents table
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, fiscal_date, version)
);

-- The "Satellite" table for all non-standard and industry-specific data.
CREATE TABLE IF NOT EXISTS custom_kpis (
    id SERIAL PRIMARY KEY,
    fundamental_id INTEGER NOT NULL REFERENCES quarterly_fundamentals(id) ON DELETE CASCADE,
    kpi_data JSONB NOT NULL, -- Stores all other metrics, e.g., {"gross_npa": 2.1, "source_label_for_revenue": "Interest Earned"}
    UNIQUE(fundamental_id)
);

-- A table to store the raw source documents for audit and reprocessing.
CREATE TABLE IF NOT EXISTS raw_documents (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    fiscal_date DATE NOT NULL,
    doc_type VARCHAR(50) NOT NULL, -- 'QUARTERLY_RESULTS_PDF', 'EARNINGS_TRANSCRIPT', 'XBRL_INSTANCE'
    source_url TEXT,
    local_path TEXT,
    raw_text_content TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
## 5. Implementation Roadmap (Prototype V1)

### Phase 1: Setup & Reconnaissance

- **Initialize Project Structure**  
  Create the `earnings_agent` directory and sub-directories (`ingestion`, `parsing`, `storage`, `docs`) as planned.

- **Initialize Database**  
  Run a script to create the three tables (`quarterly_fundamentals`, `custom_kpis`, `raw_documents`) in the PostgreSQL database.

- **Target Selection**  
  Conduct the *Representative Hard Case* analysis.  
  Select one medium-difficulty company from the Nifty Midcap 100 to serve as the initial development target.  
  Document this choice.

---

### Phase 2: The XBRL Pipeline (Targeted)

- **Build Scraper**  
  Develop a Python script to find and download the XBRL instance file (`.xml`) for the target company from BSE/NSE filings.

- **Implement XBRL Parser**  
  Create an `XBRLParser` module using a library like `python-xbrl`.  
  This module will map the standard Ind-AS tags to the Core and Satellite schema.

- **Implement Storage Logic**  
  Write the function to take the parsed XBRL data and UPSERT it into the database tables, flagging the source as `XBRL_NSE`.

---

### Phase 3: The PDF Fallback Pipeline (Targeted)

- **Build Scraper**  
  Develop the PDF scraper to download the official quarterly report PDF for historical periods where XBRL is unavailable.

- **Implement PDF Parser**  
  Create a `PDFParser` module that incorporates OCR (`tesseract`) for scanned documents.

- **Implement Normalizers**
  - Integrate the **Context-Aware Unit Normalizer** to convert all figures to absolute values.
  - Integrate the **Semantic Mapping Layer** to map text labels to standard fields.

- **Implement Storage Logic**  
  Write the function to UPSERT data from the PDF parser, using the correct source tags (`PDF_TEXT_EXTRACT` or `PDF_OCR_LLM`).

---

### Phase 4: Orchestration & Integration

- **Build Prefect Flow**  
  Create a single Prefect flow that implements the full **Cascading Pipeline** logic:
  - For a given ticker and date, first attempt the XBRL pipeline (Phase 2).
  - If it fails, automatically trigger the PDF pipeline (Phase 3).

- **End-to-End Testing**  
  Run the flow for the target company:
  - For a recent quarter (expecting XBRL success)
  - For a historical quarter (expecting PDF success)  
  to validate the entire architecture.

---

### Phase 5: Scaling

- **Select Second Target**  
  Choose a second company from a different industry to test the flexibility of the parsers.

- **Refactor & Generalize**  
  Adapt the scrapers and parsers to handle configurations for multiple companies,  
  moving towards a system that can handle a list of tickers, not just a hardcoded one.
