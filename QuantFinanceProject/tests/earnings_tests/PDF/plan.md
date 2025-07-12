# Tentative Plan: PDF Parsing Pipeline for Earnings Agent

This document outlines a phased approach to building a robust, company-agnostic PDF parsing pipeline. Each phase builds upon the last, moving from foundational improvements to a highly automated, AI-powered system.

---

### Phase 1: Crawl - Foundational Parsing & Cleaning

* **Objective:** Make the existing V1 script robust for common, text-based financial reports. Get the fundamentals of data cleaning right.
* **Key Focus:**
    * Intelligent header detection (handling multi-row and period-based headers).
    * Consolidating multi-line item descriptions in the "Particulars" column.
    * Robust data type conversion and normalization of numeric values.
* **Primary Tools:**
    * **Core Extractor:** `camelot-py`, `PyMuPDF` (as in your V1 script).
    * **Cleaning & Manipulation:** `pandas`.
    * **Pattern Matching:** `re` (Python's built-in regular expressions library).
* **Outcome:** A reliable Python module (`TableCleaner`) that takes the raw output from the V1 script and transforms it into a clean, well-structured pandas DataFrame with proper headers and data types.

---

### Phase 2: Walk - Advanced Layout Recognition

* **Objective:** Handle a much wider variety of PDF layouts from different companies with less custom code, including those that break the Phase 1 script.
* **Key Focus:**
    * Automated table detection without needing to manually define regions.
    * Handling basic scanned documents using Optical Character Recognition (OCR).
    * Getting a cleaner extraction upfront to simplify the cleaning pipeline.
* **Primary Tools:**
    * **Recommended First Choice:** `unstructured.io` - A powerful, open-source library designed for this exact purpose.
    * **Alternative/Fallback:** `pytesseract` with `pdf2image` - For direct, simple OCR tasks if needed.
* **Outcome:** An enhanced parsing script that uses `unstructured` to process PDFs. This will be benchmarked against the Phase 1 script to determine which provides a better starting point for the `TableCleaner`.

---

### Phase 3: Run - Semantic Understanding & Integration

* **Objective:** Standardize the extracted financial data and integrate it into the existing database and workflow. This phase is about adding *meaning* to the numbers.
* **Key Focus:**
    * Mapping extracted line items to a standard financial concept (e.g., "Net Sales" and "Revenue" both map to `total_revenue`).
    * Validating the data for correctness and consistency.
    * Orchestrating the entire pipeline from PDF to database.
* **Primary Tools:**
    * **Semantic Mapping:** Your existing `earnings_agent/common/semantic_map.py`.
    * **Data Validation:** `Pandera` or `Great Expectations`.
    * **Database Integration:** Your existing `earnings_agent/storage/database.py` and `schema.sql`.
    * **Orchestration:** `Prefect`.
* **Outcome:** A fully operational Prefect flow that automates the entire process: parsing a PDF (using the best tool from Phase 1/2), cleaning the data, mapping it to your standard schema, validating it, and inserting it into the `earnings_data` table.

---

### Phase 4: Fly - Scaling with Advanced AI

* **Objective:** Achieve the highest possible automation rate by handling the most difficult edge cases (e.g., complex scanned documents, non-standard tables, malformed PDFs).
* **Key Focus:**
    * Leveraging state-of-the-art AI for documents that fail the previous phases.
    * Minimizing cost by using these powerful tools surgically as a fallback.
* **Primary Tools (Evaluate based on cost vs. control):**
    * **Managed Services (Lower Effort, Pay-per-use):**
        * Google Cloud Document AI
        * Amazon Textract
    * **Open Source Models (Higher Effort, Full Control):**
        * Directly use models like `LayoutLMv3` or `DiT` from Hugging Face.
        * Implement a full pipeline with toolkits like `opendatalab/PDF-Extract-Kit`.
    * **Surgical LLM Use (For Final Fallback):**
        * OpenAI `GPT-4o` or Claude 3 Opus (for sending images of tables that all other methods fail on).
* **Outcome:** An enhanced Prefect flow where if the main pipeline fails on a PDF, it is automatically routed to a Phase 4 tool for a final attempt before being flagged for manual human review. This creates a resilient, multi-tiered, and highly automated system.