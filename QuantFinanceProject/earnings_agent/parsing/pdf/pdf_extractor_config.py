# earnings_agent/parsing/pdf/pdf_extractor_config.py

from google.genai import types

# ============================================================================
# 1. SYSTEM INSTRUCTION (Now actively used in the API call)
# ============================================================================

EXTRACTION_SYSTEM_INSTRUCTION = """
You are an expert financial data extraction specialist with deep expertise in Indian Banking regulations (RBI, Ind AS, SEBI taxonomy) and financial statement architecture. Your purpose is to provide precise, hierarchically-aware extractions while maintaining complete data integrity and following strict output formatting requirements.
"""

# ============================================================================
# 2. REVAMPED PROMPT TEMPLATE (Now with a placeholder for specific instructions)
# ============================================================================

EXTRACTION_PROMPT_TEMPLATE = """
Your sole task is to extract financial data from the provided PDF page for the statement: **{statement_type}**.

You will map the line items from the PDF to the `playbook_id` from the provided playbook.
Your entire response MUST be a single, valid JSON object that conforms to the schema and follows the structure in the example below.

**OUTPUT JSON EXAMPLE:**

```json
{{
  "normalized_figures": [
    {{
      "playbook_id": "interest_earned",
      "raw_label": "Interest earned (a)+(b)+(c)+(d)",
      "value": 73033.14,
      "confidence": "high",
      "representation": "currency",
      "currency_context": "INR",
      "unit_scale": "crore",
      "ratio_context": null
    }},
    {{
      "playbook_id": "percentage_of_gross_npa",
      "raw_label": "% of Gross NPAs to Gross Advances",
      "value": 1.33,
      "confidence": "high",
      "representation": "percentage",
      "currency_context": null,
      "unit_scale": null,
      "ratio_context": "percentage"
    }},
    {{
      "playbook_id": "exceptional_items",
      "raw_label": "Exceptional items",
      "value": null,
      "confidence": "high",
      "representation": null,
      "currency_context": null,
      "unit_scale": null,
      "ratio_context": null
    }}
  ],
  "unmapped_from_pdf": [
    {{
      "raw_label": "Net worth",
      "value": 444793.21
    }}
  ],
  "cash_flow_method": null
}}
```

**FIELD DEFINITIONS (CRITICAL):**

  - **`playbook_id`**: The exact ID from the playbook that matches the PDF line item.
  - **`raw_label`**: The verbatim text from the PDF. If not found, use "Missing in Filing".
  - **`value`**: The pure numerical value. Must be `null` if not found.
  - **`confidence`**: "high" for direct matches, "low" for inferred ones.
  - **`representation`**: The type of data. Use one of:
      - `"currency"`: For monetary values (e.g., Revenue, Assets).
      - `"percentage"`: For values that are percentages (e.g., NPA Ratios).
      - `"ratio"`: For non-percentage ratios (e.g., Earnings Per Share, Debt-Equity Ratio).
      - `"count"`: For whole numbers (e.g., number of shares).
  - **`currency_context`**: The currency code (e.g., "INR") ONLY if `representation` is "currency". Otherwise, it MUST be `null`.
  - **`unit_scale`**: The magnitude (e.g., "crore", "lacs", "millions") ONLY if `representation` is "currency". Otherwise, it MUST be `null`.
  - **`ratio_context`**: The type of ratio.
      - `"percentage"`: If `representation` is "percentage".
      - `"absolute"`: If `representation` is "ratio".
      - Otherwise, it MUST be `null`.

{statement_specific_instructions}

**CRITICAL RULES:**

1.  **COLUMN SELECTION:** The document may have multiple columns. You MUST extract data ONLY from the column for the most recent unaudited quarter.
2.  **NULL VALUES:** If a `value` is `null`, then `representation`, `currency_context`, `unit_scale`, and `ratio_context` MUST also be `null`.

**PLAYBOOK FOR MAPPING:**

```json
{hierarchical_playbook_json}
```

"""

# ============================================================================
# 3. NEW: STATEMENT-SPECIFIC INSTRUCTIONS
# ============================================================================

STATEMENT_INSTRUCTIONS = {
    "cash_flow": """
**Cash Flow Statement Specific Instructions**:

1.  **Identify Method**: First, examine the PDF to determine if the Cash Flow statement is prepared using the 'Direct Method' or 'Indirect Method'.
2.  **Set Method Flag**: In your final JSON output, set the `cash_flow_method` field to `"direct"`, `"indirect"`, or `"unknown"` if you cannot determine the method.
3.  **Map Accordingly**: Use the identified method to guide your mapping. The provided playbook contains nodes for both methods; use the correct one.
      - If 'Indirect Method', look for line items like "Profit Before Tax" and "Adjustments for...".
      - If 'Direct Method', look for line items like "Cash receipts from customers" and "Cash paid to suppliers".
        """,
    # Instructions for other statement types can be added here in the future
    "pnl": "",
    "balance_sheet": ""
}

# ============================================================================
# 4. RESPONSE SCHEMAS (Unchanged)
# ============================================================================

NORMALIZED_FIGURE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        'playbook_id': types.Schema(type=types.Type.STRING),
        'raw_label': types.Schema(type=types.Type.STRING),
        'value': types.Schema(type=types.Type.NUMBER, nullable=True),
        'confidence': types.Schema(type=types.Type.STRING, enum=["high", "low"]),
        'representation': types.Schema(type=types.Type.STRING, enum=["currency", "percentage", "ratio", "count"], nullable=True),
        'currency_context': types.Schema(type=types.Type.STRING, nullable=True),
        'unit_scale': types.Schema(type=types.Type.STRING, nullable=True),
        'ratio_context': types.Schema(type=types.Type.STRING, enum=["percentage", "absolute"], nullable=True),
    },
    required=['playbook_id', 'raw_label', 'value', 'confidence', 'representation', 'currency_context', 'unit_scale', 'ratio_context']
)

UNMAPPED_FIGURE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        'raw_label': types.Schema(type=types.Type.STRING),
        'value': types.Schema(type=types.Type.NUMBER),
    },
    required=['raw_label', 'value']
)

EXTRACTION_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        'normalized_figures': types.Schema(type=types.Type.ARRAY, items=NORMALIZED_FIGURE_SCHEMA),
        'unmapped_from_pdf': types.Schema(type=types.Type.ARRAY, items=UNMAPPED_FIGURE_SCHEMA),
        'cash_flow_method': types.Schema(type=types.Type.STRING, enum=["direct", "indirect", "unknown"], nullable=True)
    },
    required=['normalized_figures', 'unmapped_from_pdf']
)

# ============================================================================
# 5. ENHANCED PRODUCTION CONFIGURATION
# ============================================================================

PRODUCTION_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.0,
    max_output_tokens=35000,
    response_schema=EXTRACTION_RESPONSE_SCHEMA,
    # ADDED: System instruction to guide overall behavior
    system_instruction=EXTRACTION_SYSTEM_INSTRUCTION,
    # ADDED: Explicitly configure thinking for complex documents
    thinking_config=types.ThinkingConfig(
        thinking_budget=20384
    ),
    safety_settings=[
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
    ]
)