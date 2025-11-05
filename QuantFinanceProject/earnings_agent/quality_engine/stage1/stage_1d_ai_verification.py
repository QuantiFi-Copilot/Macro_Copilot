# /app/earnings_agent/quality_engine/stage1/stage_1d_ai_verification.py

import json
import time
import logging
import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Core library imports
from google import genai
from google.genai import types
from google.oauth2 import service_account
from pydantic import BaseModel, Field

# Project imports
project_root = Path(__file__).resolve().parents[4]
import sys
sys.path.append(str(project_root))

from earnings_agent.parsing.pdf.pdf_extractor_config import (
    PRODUCTION_MODEL,
    PRODUCTION_CONFIG,
    SYSTEM_INSTRUCTION
)

# GCP Configuration (same as extractor)
GCP_PROJECT_ID = "pdf-extractor-467911"
GCP_LOCATION = "us-central1"
LLM_MAX_RETRIES = 3
LLM_INITIAL_BACKOFF = 5

# --- Pydantic Models for Verification ---
class VerificationFigure(BaseModel):
    playbook_id: str = Field(description="The exact playbook_id provided in the request.")
    value: Optional[float] = Field(description="The pure numerical value. Must be null if not found.")

class VerificationResponse(BaseModel):
    verified_figures: List[VerificationFigure]

# --- Verification Prompt Template ---
VERIFICATION_PROMPT_TEMPLATE = """
You are an expert financial data extraction specialist. Your task is to extract ONLY the specific financial metrics listed below from the attached PDF financial statement for the period: {period}.

**METRICS TO VERIFY:**
{metrics_list}

**CRITICAL PARSING RULES:**
1. **COLUMN SELECTION:** The document may have multiple columns for different periods. You **MUST** extract data **ONLY** from the column corresponding to the period: **{period}**. Ignore all other columns.

2. **NUMBER FORMAT PARSING:** You must strictly follow these rules:
   - **Indian Notation:** `1,23,456.78` must be parsed as `123456.78`. Remove all commas.
   - **Negative Values:** Numbers in parentheses, like `(5,432.10)`, **ALWAYS** indicate a negative value and must be parsed as `-5432.10`.
   - **Special Values:**
     - Text like `-`, `NIL`, `Nil`, or a blank entry must be treated as `null`.
     - A literal `0` or `0.00` must be parsed as the number `0`, not `null`.
   - **Percentages:** A value like `2.45%` must be parsed as the number `2.45`.

3. **UNIT DETECTION:** Scan the page for headers defining the unit scale (e.g., '(All figures in Rs. Crores)'). Apply this consistently.

4. **EXACT MATCHING:** Find the exact raw label text shown above for each metric. Extract only the numerical value from that line item.

**OUTPUT REQUIREMENT:**
Return the verified figures in the exact JSON structure specified by the response schema. Include only the playbook_id and value fields.
"""

def _get_gemini_client():
    """Initializes and returns a production-ready Gemini client (same as extractor)."""
    try:
        credentials_path = "/app/gcp-credentials.json"
        if not Path(credentials_path).exists():
            raise FileNotFoundError(f"Credentials file not found at {credentials_path}")

        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        return genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=GCP_LOCATION, credentials=credentials)
    except Exception as e:
        logging.error(f"Fatal error initializing Gemini client: {e}", exc_info=True)
        raise

def extract_low_confidence_metrics_by_statement(working_content: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Scans all statements in working_content and returns low confidence metrics grouped by statement.
    
    Returns:
        {
            "standalone_pnl": [
                {"playbook_id": "interest_earned", "raw_label": "Interest Earned", "value": 123.45},
                ...
            ],
            "consolidated_balance_sheet": [...],
            ...
        }
    """
    low_confidence_by_statement = {}
    
    if not working_content or "llm_call_2_extraction" not in working_content:
        return low_confidence_by_statement
    
    extraction_data = working_content["llm_call_2_extraction"]
    
    for statement_type, statement_data in extraction_data.items():
        if not isinstance(statement_data, dict) or "normalized_figures" not in statement_data:
            continue
            
        low_conf_metrics = []
        for figure in statement_data["normalized_figures"]:
            if figure.get("confidence") == "low":
                low_conf_metrics.append({
                    "playbook_id": figure["playbook_id"],
                    "raw_label": figure["raw_label"],
                    "value": figure["value"]
                })
        
        if low_conf_metrics:
            low_confidence_by_statement[statement_type] = low_conf_metrics
    
    return low_confidence_by_statement

def call_verification_llm(
    client: genai.Client, 
    pdf_bytes: bytes, 
    metrics_to_verify: List[Dict[str, Any]], 
    filing_period: str
) -> List[Dict[str, Any]]:
    """Calls Gemini for verification using the same robust configuration as extraction."""
    
    # Build the metrics list for the prompt
    metrics_list = "\n".join([
        f"- {metric['playbook_id']}: Extract value from line item '{metric['raw_label']}'"
        for metric in metrics_to_verify
    ])
    
    prompt = VERIFICATION_PROMPT_TEMPLATE.format(
        period=filing_period,
        metrics_list=metrics_list
    )
    
    # Use same config as extraction but with verification schema
    config = {**PRODUCTION_CONFIG}
    config['response_schema'] = VerificationResponse
    config['system_instruction'] = SYSTEM_INSTRUCTION
    
    # Make the API call with retry logic (same as extraction)
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=PRODUCTION_MODEL,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                ],
                config=types.GenerateContentConfig(**config),
            )
            
            # Parse response and return the verified figures
            response_data = json.loads(response.text)
            return response_data.get("verified_figures", [])
            
        except Exception as e:
            logging.warning(f"LLM verification call failed on attempt {attempt + 1}/{LLM_MAX_RETRIES}: {e}")
            if attempt + 1 == LLM_MAX_RETRIES:
                raise
            time.sleep(LLM_INITIAL_BACKOFF * (2 ** attempt))
    
    raise RuntimeError("LLM verification call failed after all retry attempts.")

def values_match(val1: Optional[float], val2: Optional[float]) -> bool:
    """
    Strict tolerance for LLM verification:
    - Values < 100: ±0.01 absolute difference
    - Values >= 100: ±0.1% relative difference
    """
    if val1 is None and val2 is None:
        return True
    if val1 is None or val2 is None:
        return False
    
    # For small values, use absolute tolerance
    if abs(val1) < 100:
        return abs(val1 - val2) <= 0.01
    
    # For larger values, use relative tolerance (0.1%)
    larger_val = max(abs(val1), abs(val2))
    return abs(val1 - val2) / larger_val <= 0.001

def run_ai_verification_check(
    low_conf_metrics: List[Dict[str, Any]], 
    pdf_bytes: bytes, 
    filing_period: str,
    client: genai.Client
) -> Dict[str, Any]:
    """
    Core business logic for AI verification of low confidence metrics.
    
    Args:
        low_conf_metrics: List of metrics to verify
        pdf_bytes: PDF content for verification
        filing_period: Period string for column selection
        client: Initialized Gemini client
    
    Returns:
        {
            "status": "SUCCESS" | "FAILURE",
            "verified_metrics": [...],  # if SUCCESS
            "details": {
                "disagreements": [...],  # if FAILURE
                "summary": "..."
            }
        }
    """
    try:
        # Call verification LLM
        verified_metrics = call_verification_llm(client, pdf_bytes, low_conf_metrics, filing_period)
        
        # Compare results
        disagreements = []
        for orig in low_conf_metrics:
            playbook_id = orig["playbook_id"]
            verification = next((v for v in verified_metrics if v["playbook_id"] == playbook_id), None)
            
            if not verification:
                disagreements.append(f"{playbook_id}: LLM2 failed to extract")
                continue
                
            if not values_match(orig["value"], verification["value"]):
                disagreements.append(f"{playbook_id}: LLM1={orig['value']}, LLM2={verification['value']}")
        
        if disagreements:
            return {
                "status": "FAILURE",
                "details": {
                    "disagreements": disagreements,
                    "summary": "; ".join(disagreements)
                }
            }
        else:
            return {
                "status": "SUCCESS",
                "verified_metrics": low_conf_metrics,
                "details": {
                    "summary": f"All {len(low_conf_metrics)} metrics verified successfully"
                }
            }
            
    except Exception as e:
        return {
            "status": "FAILURE",
            "details": {
                "error": str(e),
                "summary": f"LLM verification error: {str(e)}"
            }
        }

def promote_metrics_to_high_confidence(
    working_content: Dict[str, Any], 
    statement_type: str, 
    verified_metrics: List[Dict[str, Any]]
):
    """Updates the confidence level to 'high' for successfully verified metrics."""
    if "llm_call_2_extraction" not in working_content:
        return
    
    if statement_type not in working_content["llm_call_2_extraction"]:
        return
    
    normalized_figures = working_content["llm_call_2_extraction"][statement_type]["normalized_figures"]
    verified_playbook_ids = {metric["playbook_id"] for metric in verified_metrics}
    
    for figure in normalized_figures:
        if figure["playbook_id"] in verified_playbook_ids:
            figure["confidence"] = "high"