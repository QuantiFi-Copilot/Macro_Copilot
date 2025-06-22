# Plan: Institutional-Grade XBRL Data Layer

This document outlines the step-by-step process for parsing a raw XBRL file and transforming it into a validated, structured, and intelligent output.

### INPUT: Raw XBRL File (.xml)
*(A single file downloaded by our `fetch_xbrl.py` script)*

↓

### **Step 1: Pre-Processing & Initial Logging**
- **Action:** Log the raw file's metadata (path, ticker, date) to the `raw_documents` table in our database.
- **Purpose:** Create an immediate audit trail. Every file is tracked before processing begins.
- **Module:** `storage/database.py`

↓

### **Step 2: File Classification (The "Triage" Stage)**
- **Action:** A new `XBRLClassifier` module reads the raw XML to extract high-level metadata.
    1.  **Identify Sector/Format:** Check the `<link:schemaRef>` tag (e.g., `banking_entry_point` vs. `Ind-AS_entry_point`). This is the most reliable way to classify the filing type.
    2.  **Calculate Initial Quality Metrics:**
        -   Count total number of numerical facts.
        -   Count percentage of facts that are zero-filled.
- **Purpose:** To understand what kind of document we are dealing with *before* parsing its details. This will guide the downstream logic.
- **Module:** `parsing/classify_xbrl.py`

↓

### **Step 3: Core Parsing & Semantic Mapping**
- **Action:** The main `XBRLParser` module reads the file.
    1.  **Extract All Facts:** Pull every numerical fact associated with the primary reporting period (e.g., context `OneD`).
    2.  **Apply Semantic Map:** Use our centralized `semantic_map.py` to translate diverse source tags (`InterestEarned`, `RevenueFromOperations`) into our standardized internal fields (`standard_revenue`).
    3.  **Separate Data:** Bucket the results into `core_data` (for the `quarterly_fundamentals` table) and `custom_kpis` (for everything else).
- **Purpose:** To convert the raw, tagged data into a clean, standardized structure.
- **Module:** `parsing/parse_xbrl.py`

↓

### **Step 4: Automated Validation & Quality Flagging**
- **Action:** A new `ValidationEngine` module takes the parsed data and runs a series of programmatic checks.
    1.  **Filing-Level Check:**
        -   **Balance Sheet Identity:** Programmatically verify if `Assets ≈ Liabilities + Equity`. If this fails, the entire filing is marked as `VALIDATION_FAILED`.
    2.  **Metric-Level Checks:**
        -   **Suspicious Zero Check:** Based on the sector classification from Step 2, apply rules. If `sector == 'Bank'` and `PercentageOfGrossNpa == 0`, flag this specific metric as `SUSPICIOUS_ZERO`.
        -   **Outlier Check:** Compare key metrics against that company's own 8-quarter history. If `standard_revenue` deviates by >5 standard deviations, flag it as `HISTORICAL_OUTLIER`.
- **Purpose:** To move from blind data ingestion to intelligent data validation. We systematically trust nothing and verify everything.
- **Module:** `earnings_agent/validation/rules.py`

↓

### **Step 5: Structure the Final Output**
- **Action:** Consolidate all the information gathered into a single, rich JSON object.
- **Purpose:** To create a highly meaningful output that gives the next layer of our system all the context it needs to make an intelligent decision.

↓

### OUTPUT: The "Rich Fact" JSON Object
*(A structured object ready to be passed to the next stage of the pipeline)*
```json
{
  "filing_metadata": {
    "ticker": "HDFCBANK",
    "period_end_date": "2024-09-30",
    "source_file_id": 12345
  },
  "classification": {
    "filing_type": "Bank",
    "initial_quality_score": 0.85
  },
  "validation_summary": {
    "status": "PASSED_WITH_WARNINGS",
    "checks": [
        "BALANCE_SHEET_OK",
        "NPA_RATIO_SUSPICIOUS"
    ]
  },
  "metrics": {
    "standard_revenue": {
        "value": 830017200000,
        "flag": "CONFIRMED_HIGH"
    },
    "PercentageOfGrossNpa":{
        "value": 0,
        "flag": "SUSPICIOUS_ZERO"
    },
    "Assets": {
        "value": 41517879300000,
        "flag": "CONFIRMED_HIGH"
    }
  }
}

- For the classification:
Do rule based classificaton of features first, then unsupervised learning to find patterns, the use supervised learning 