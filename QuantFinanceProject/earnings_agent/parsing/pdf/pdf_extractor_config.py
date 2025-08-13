# /app/earnings_agent/parsing/pdf/pdf_extractor_config.py
import logging
import enum
from typing import Optional, List
from pydantic import BaseModel, Field
from google.genai.types import Content
# --- NEW: Enum Definitions for Stronger Typing ---
# By defining these enums, we constrain the LLM's output to only these
# specific, valid strings, dramatically increasing reliability.

class ConfidenceLevel(str, enum.Enum):
    HIGH = "high"
    LOW = "low"

class RepresentationType(str, enum.Enum):
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    RATIO = "ratio"

class CurrencyType(str, enum.Enum):
    INR = "INR"

class UnitScaleType(str, enum.Enum):
    CRORES = "crores"
    LAKHS = "lakhs"
    BILLIONS = "billions"
    MILLIONS = "millions"
    THOUSANDS = "thousands"
    HUNDREDS = "hundreds"

class RatioContextType(str, enum.Enum):
    PERCENTAGE = "percentage"
    ABSOLUTE = "absolute"

# --- Pydantic Schemas for Structured Output (UPDATED with Enums) ---
class NormalizedFigure(BaseModel):
    playbook_id: str = Field(description="The unique, snake_case identifier from the playbook.")
    raw_label: str = Field(description="The verbatim text from the PDF. If not found, use 'Missing in Filing'.")
    value: Optional[float] = Field(description="The pure numerical value. Must be null if not found.")
    confidence: ConfidenceLevel = Field(description="Your confidence in the accuracy of the extraction.")
    representation: Optional[RepresentationType] = Field(description="The data type. Must be null if value is null.")
    currency_context: Optional[CurrencyType] = Field(description="The currency if applicable. Must be null if not currency.")
    unit_scale: Optional[UnitScaleType] = Field(description="The unit scale. Must be null if not applicable.")
    ratio_context: Optional[RatioContextType] = Field(description="The ratio type. Must be null if not a ratio or percentage.")

class UnmappedFigure(BaseModel):
    raw_label: str = Field(description="The verbatim text from the PDF for a line item NOT found in the playbook.")
    value: float = Field(description="The pure numerical value for the unmapped line item.")

class ExtractionResponse(BaseModel):
    normalized_figures: List[NormalizedFigure]
    unmapped_from_pdf: List[UnmappedFigure]

# --- System instruction (high-priority guardrails for the model) ---
SYSTEM_INSTRUCTION = """
You are an expert financial data extraction specialist with deep expertise in Indian Financial regulations (RBI, Ind AS, SEBI) and financial statement architecture. Your purpose is to provide precise, clean and extremely accurate extractions while maintaining complete data integrity and following strict output formatting requirements.
"""


EXTRACTION_PROMPT_TEMPLATE = """
Your sole task is to take the provided JSON TEMPLATE and populate its values based on the data in the attached PDF financial statement for the period: {period}.

You will be provided with a playbook that maps standard financial concepts to a `playbook_id`. You will also be provided with a JSON TEMPLATE containing a pre-defined list of all required financial metrics.

**YOUR TASK:**
Carefully read the PDF and the playbook. For each metric in the JSON TEMPLATE's `normalized_figures` array, find the corresponding line item in the PDF and UPDATE the template's fields (`raw_label`, `value`, metadata, etc.).

---
**FIELD DEFINITIONS (How to update the template):**
- `playbook_id`: **DO NOT CHANGE THIS.** It is the ground truth from the playbook.
- `raw_label`: Update this from `"Missing in Filing"` to the verbatim text label found in the PDF for that metric.
- `value`: Update this from `null` to the pure numerical value you extract. If a value is genuinely not present for a metric in the PDF, leave the value as `null`.
- `confidence`: Set your confidence for each extraction.
- `representation`, `currency_context`, `unit_scale`, `ratio_context`: Populate these metadata fields based on the extracted value.
- `unmapped_from_pdf`: If you find any significant financial line items in the PDF that are **NOT** in the playbook, add them to this array.

---
**CRITICAL RULES:**
1.  **UNIT & CURRENCY DETECTION:** Before extracting values, you **MUST** scan the entire page for headers or text that define the default currency and unit scale (e.g., '(All figures in Rs. Crores)'). Apply this default to all relevant metrics unless a specific line item indicates otherwise.
2.  **COLUMN SELECTION:** The document may have multiple columns for different periods. You **MUST** extract data **ONLY** from the column corresponding to the specified period: **{period}**. Ignore all other columns.
3.  **NULL VALUES:** If a `value` is `null`, then `representation`, `currency_context`, `unit_scale`, and `ratio_context` **MUST** also be `null`.
4.  **MANDATORY COMPLETENESS:** You **MUST** populate the values for **EVERY** object in the provided `normalized_figures` array within the template. **DO NOT ADD OR REMOVE ANY OBJECTS FROM THIS ARRAY.** Your final response must contain the complete, populated list.

**DATA PARSING & NORMALIZATION RULES:**
1.  **NUMBER FORMAT PARSING:** You must strictly follow these rules when parsing numbers:
    - **Indian Notation:** `1,23,456.78` must be parsed as `123456.78`. Remove all commas.
    - **Negative Values:** Numbers in parentheses, like `(5,432.10)`, **ALWAYS** indicate a negative value and must be parsed as `-5432.10`.
    - **Special Values:**
        - Text like `-`, `NIL`, `Nil`, or a blank entry must be treated as `null`.
        - A literal `0` or `0.00` must be parsed as the number `0`, not `null`.
    - **Percentages:** A value like `2.45%` must be parsed as the number `2.45`.
2. **UNIT SCALE MAPPING:** You must map the unit found in the document to one of the allowed `UnitScaleType` enum values.
    - Map variations like 'lacs', 'Lakh', or 'Lac' to **'lakhs'**.
    - Map variations like 'Crs.', 'Crore', or 'Cr' to **'crores'**.
    - Map 'Million' or 'Mn' to **'millions'**.
    - And so on for other units.


**FEW-SHOT EXAMPLES:**
{few_shot_examples_placeholder}

---
**PLAYBOOK FOR MAPPING:**
The playbook is a hierarchical JSON defining the financial concepts to extract, mapping human-readable labels to a standardized `playbook_id`. Use this to understand the meaning of each metric.
```json
{hierarchical_playbook_json}
```

---

**JSON TEMPLATE TO COMPLETE:**

```json
{json_template_placeholder}
```

"""

# --- Production Gemini Configuration (UPDATED) ---
PRODUCTION_MODEL = "gemini-2.5-pro"
PRODUCTION_CONFIG = {
    "temperature": 0.0,
    "top_p": 1.0,
    "top_k": 1, # ADDED: For more deterministic, less creative outputs
    "max_output_tokens": 20000,
    "response_mime_type": "application/json",
}