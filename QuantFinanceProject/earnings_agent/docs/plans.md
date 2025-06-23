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

Things to do: 

- For the classification:
Do rule based classificaton of features first, then unsupervised learning to find patterns, the use supervised learning 

Summary of Our Data Classification and Validation Process
Here is the concise, step-by-step summary of the process we defined.
1. Standardization via Semantic Mapping:
The first step is to create a "dictionary" (semantic_map.py) that translates diverse XBRL tags (e.g., RevenueFromOperations, InterestEarned) into our single, standard internal concepts (e.g., standard_revenue). This ensures we speak a consistent language internally.
2. Validation via Rule-Based Engine:
Once data is standardized, we run it through our ValidationEngine. This engine applies a series of deterministic, common-sense checks to flag suspicious data.
Filing-Level Check: Verifies universal accounting identities (e.g., Assets ≈ Liabilities + Equity).
Metric-Level Check: Uses a sector-specific playbook to flag impossible values (e.g., a bank having 0% Gross NPA).
Historical Outlier Check: Flags any metric that deviates significantly from the company's own historical performance.
3. Prioritization via Initial ML Model:
The flags generated by the rule-based engine are used as "proxy labels" to train an initial, lightweight ML model.
The purpose of this model is not to be perfectly accurate, but to analyze the combination of flags and calculate a "probability of corruption" score for every filing.
4. Refinement via Iterative Learning:
We use the corruption scores to perform "smart sampling"—intelligently selecting a small number of the highest-risk files for expensive PDF extraction.
This creates a "Golden Set" of perfectly accurate labels.
We then retrain our ML model with this Golden Set, continuously improving its accuracy over time. This makes our entire system more efficient by focusing our most expensive resources only where they are needed most.

- The Revised "Source of Truth Hierarchy"
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



IMPORTANT: 
- Cross source reconcilation idea: once we build an XBRL ingestion layer + cheaper sources ingestion layer - can we compare it to data on like the NSE website or other extremely reliable sources through scraping. 
this will be incorporated with our validation engine then it flags discrepancies

So the workflow will be: 
1. Build solid cheap data source layers like XBRL, other sources etc 
2. Validation engine is built to flag inconsistencies, but it cannot flag minor errors like 5% difference in profit for instance 
3. so build a cross validation with sources that are considered much more reliable 
4. then once it goes through these, flag inconsistencies and then any file that has been flagged after this process will go through the more expensive PDF extraction procedure. 


Things to explore: 
- AI Driven scraping - somehow build an agent that automates web scraping 

Analysis of Your Proposed 8-Step Workflow
Your plan is 95% correct and perfectly aligned with best practices. I will go through each step and offer my commentary and one minor, crucial refinement.
Step 1: Expand Universe: Correct. Scaling from one stock to a thousand is the first real test of the entire pipeline.
Step 2: Build Rule-Based Validation Engine: Correct. This is our immediate next logical step. It's the "intelligent" layer that sits on top of the parser.
Step 3: Validation Engine Flags Stuff: Correct. The engine's job is to create a prioritized "to-do list" for us.
Step 4: Diagnose the Flag's Cause: Correct. This is the key diagnostic step. Is total_assets null because the XML is empty, or because our parser missed the tag?
Step 5: Auto-Update Semantic Map: This is a fantastic idea, but I would propose a slight, critical modification. Instead of "auto-update," we should implement "auto-suggest."
The Risk of Auto-Update: Imagine one malformed XML file causes the system to incorrectly identify a garbage tag (e.g., MiscellaneousItems) as total_assets. An auto-update could pollute our semantic_map.py and break the parsing for all other 999 companies.
The Power of Auto-Suggest: A safer, more robust workflow is for the system to flag the issue and suggest a solution. It would log: "ALERT: For ticker XYZ, 'total_assets' is null. A potential new tag was found in custom_kpis: 'TotalCompanyAssets'. Suggest adding this to the semantic map." This creates a "human-in-the-loop" process where we can approve these suggestions, preventing a single bad file from corrupting the entire system.
Step 6: Iterate & Evolve Validation Engine: Correct. The rule-based engine is v1. Once we have enough labeled data (i.e., filings we have confirmed as "good" or "bad"), we can train an ML model to take over and find more subtle patterns.
Step 7: Risk-Classify XBRL & Trigger PDF Parsing: Perfect. This is the "smart sampling" or "active learning" strategy we discussed. It uses a cheap process (XBRL parsing + ML scoring) to intelligently guide an expensive one (PDF parsing).
Step 8: Repeat for Other Sources: Correct. This demonstrates a deep understanding of the architecture. Once this entire XBRL pipeline is a well-oiled machine, we can plug in a new data source (like a screen scraper) at the beginning and reuse the entire validation and processing backend.