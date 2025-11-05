# /app/earnings_agent/quality_engine/stage3/llm_client.py

import os
import logging
import json
import time
from typing import List, Dict

from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.oauth2 import service_account

# --- Configuration ---
GCP_PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "pdf-extractor-467911")
GCP_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
SUGGESTION_MODEL = "gemini-2.5-pro" # Use a powerful model for this complex task

LLM_MAX_RETRIES = 3
LLM_INITIAL_BACKOFF = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# ==============================================================================
# NEW: UNIFIED MAPPING SUGGESTION TASK
# ==============================================================================

class StandardMappingSuggestion(BaseModel):
    """A suggested mapping from a raw label to a standard playbook ID."""
    raw_label: str = Field(description="The original, unmapped label from the financial statement.")
    target_playbook_id: str = Field(description="The standard playbook ID that the raw label is a synonym for.")
    rationale: str = Field(description="A brief justification for why this is a confident one-to-one match.")

class KpiSuggestion(BaseModel):
    """A raw label identified as a company-specific KPI, with a normalized name."""
    raw_label: str = Field(description="The original, unmapped label from the financial statement.")
    normalized_kpi_name: str = Field(description="The clean, snake_case version of the label to be used as a KPI identifier.")

class MappingSuggestionResponse(BaseModel):
    """The complete, structured JSON response for the mapping suggestion task."""
    suggested_standard_mappings: List[StandardMappingSuggestion] = Field(description="A list of raw labels that have been confidently matched to a standard playbook ID.")
    suggested_kpis: List[KpiSuggestion] = Field(description="A list of remaining raw labels that should be treated as company-specific KPIs, along with their normalized names.")

SUGGESTION_SYSTEM_INSTRUCTION = """
You are an expert financial data analyst for Indian Listed Companies. Your task is to intelligently map non-standard, raw financial labels to a predefined playbook of standard financial concepts. You must be precise and only suggest mappings where you have extremely high confidence.
"""

SUGGESTION_PROMPT_TEMPLATE = """
**Objective:**
Analyze a list of unmapped financial labels and a list of available standard 'null' metrics from a single financial statement. Your goal is to:
1.  Identify high-confidence, one-to-one semantic matches between an unmapped label and a null metric.
2.  Classify any remaining unmapped labels as company-specific KPIs and generate a clean `snake_case` name for them.

**Inputs:**
1.  **Unmapped Labels List:**
    ```json
    {unmapped_metrics_json}
    ```
2.  **Available 'Null' Standard Metrics List:**
    ```json
    {null_standard_labels_json}
    ```

**CRITICAL RULES:**
1.  **One-to-One Only:** You MUST only map one unmapped label to one null metric. Do not map multiple labels to a single metric or vice-versa.
2.  **High Confidence Only:** If there is any ambiguity or the meaning is not a near-perfect synonym, classify the unmapped label as a KPI. It is better to have a KPI than an incorrect standard mapping.
3.  **Exhaustive Classification:** Every single label from the "Unmapped Labels List" must be present in exactly one of the output lists in your response (`suggested_standard_mappings` or `suggested_kpis`).
4.  **KPI Naming:** For KPIs, create a concise but descriptive `snake_case` name. (e.g., "Employees stock options outstanding" becomes "employee_stock_options_outstanding").
5. **AVOID PARENT-CHILD MISMATCHES:** This is crucial. A specific component should NOT be mapped to its broader parent category if a more specific mapping is available. For example:
    - **WRONG:** `raw_label: "(i) Employees cost"` -> `mapping: "operating_expenses"`. (Employee cost is PART OF operating expenses, not equal to it).
    - **CORRECT:** `raw_label: "(i) Employees cost"` -> `mapping: "employee_cost"` (if available in the playbook).
    - **CORRECT:** `raw_label: "(i) Employees cost"` -> `mapping: null` (if `employee_cost` is NOT in the playbook).

Your response **MUST** be a single, valid JSON object that conforms to the required schema.
"""

# --- Shared Gemini Client Logic ---

def _get_gemini_client() -> genai.Client:
    """Initializes and returns a Gemini client using service account credentials."""
    try:
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/app/gcp-credentials.json")
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"Google Cloud credentials file not found at {credentials_path}")

        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        return genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=GCP_LOCATION, credentials=credentials)
    except Exception as e:
        logging.error(f"Fatal error initializing Gemini client: {e}", exc_info=True)
        raise

def _call_llm_with_retry(client: genai.Client, system_instruction: str, prompt: str, response_schema: BaseModel, log_context: str) -> str:
    """Generic function to call the Gemini API with retry logic and structured output."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.0,
        response_schema=response_schema
    )
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=SUGGESTION_MODEL,
                contents=[system_instruction, prompt],
                config=config
            )
            if not response.text:
                raise ValueError("LLM returned an empty response.")
            return response.text
        except Exception as e:
            logging.warning(f"Gemini API call for '{log_context}' failed on attempt {attempt + 1}: {e}")
            if attempt + 1 == LLM_MAX_RETRIES:
                logging.error(f"LLM call failed after all retries for context: {log_context}")
                raise RuntimeError(f"LLM call failed after {LLM_MAX_RETRIES} retries.") from e
            time.sleep(LLM_INITIAL_BACKOFF * (2 ** attempt))
    raise RuntimeError("LLM call failed unexpectedly.")

# --- Public Function for the Orchestrator ---

def suggest_mappings_for_statement(unmapped_metrics: List[Dict], null_standard_labels: List[str]) -> MappingSuggestionResponse:
    """
    Asks the LLM to suggest mappings for a statement's unmapped metrics.

    Args:
        unmapped_metrics: List of unmapped metric dictionaries from working_content.
        null_standard_labels: List of playbook_ids that are null in working_content.

    Returns:
        A MappingSuggestionResponse Pydantic object.
    """
    client = _get_gemini_client()
    
    # Prepare inputs for the prompt template
    unmapped_labels_for_prompt = [metric['raw_label'] for metric in unmapped_metrics]
    unmapped_json = json.dumps(unmapped_labels_for_prompt, indent=2)
    null_labels_json = json.dumps(null_standard_labels, indent=2)
    
    prompt = SUGGESTION_PROMPT_TEMPLATE.format(
        unmapped_metrics_json=unmapped_json,
        null_standard_labels_json=null_labels_json
    )
    
    log_context = f"statement with {len(unmapped_metrics)} unmapped metrics"
    response_text = _call_llm_with_retry(client, SUGGESTION_SYSTEM_INSTRUCTION, prompt, MappingSuggestionResponse, log_context)
    return MappingSuggestionResponse.model_validate_json(response_text)