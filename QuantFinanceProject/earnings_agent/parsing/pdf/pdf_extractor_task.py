# /app/earnings_agent/parsing/pdf/pdf_extractor_task.py

import json
import logging
import os
import sys
import time
import concurrent.futures
import yaml
from pathlib import Path
from typing import Dict, Any, List

# --- Core library imports ---
from google import genai
from google.genai import types
from google.oauth2 import service_account
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy import select, update


# --- Project Imports ---
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.storage.database import get_session
from earnings_agent.storage.models import ParsedDocument, RawDataAsset, JobAssetLink, IngestionJob, CompanyMaster, Classification

# --- UPDATED: Import the new Pydantic models and revised prompt/config ---
# This now points to your new config file.
from earnings_agent.parsing.pdf.pdf_extractor_config import (
    PRODUCTION_MODEL,
    PRODUCTION_CONFIG,
    SYSTEM_INSTRUCTION,
    EXTRACTION_PROMPT_TEMPLATE,
    ExtractionResponse,
    NormalizedFigure
)

# --- Configuration ---
PARSER_VERSION = "v3.0-accuracy-upgrade" # Bumping version to reflect the major logic change
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- GCP Configuration ---
GCP_PROJECT_ID = "pdf-extractor-467911"
GCP_LOCATION = "us-central1"

# --- Paths & Error Handling ---
PLAYBOOKS_DIR = project_root / "earnings_agent" / "playbooks"
BANKING_PLAYBOOK_PATH = PLAYBOOKS_DIR / "sebi" / "metrics" / "sebi_banking.yml"
LLM_MAX_RETRIES = 3
LLM_INITIAL_BACKOFF = 5
MAX_WORKERS = 1

# ==============================================================================
# --- NEW: ALL LLM-RELATED HELPER FUNCTIONS ARE FROM YOUR NEW SCRIPT ---
# ==============================================================================

def _get_gemini_client():
    """Initializes and returns a production-ready Gemini client."""
    try:
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/app/gcp-credentials.json")
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"Credentials file not found at {credentials_path}")

        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        return genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=GCP_LOCATION, credentials=credentials)
    except Exception as e:
        logging.error(f"Fatal error initializing Gemini client: {e}", exc_info=True)
        raise

def generate_few_shot_examples(period: str) -> str:
    """Creates a string of rich, few-shot examples to guide the LLM."""
    
    example_1_desc = f"""
**Example 1: Column Selection, Negative Values, and Footnotes**
If the PDF contains a table like this and the target period is '{period}':

(All figures in â‚¹ Crores except as stated)
| Particulars                               | Quarter Ended {period}   | Quarter Ended 31-Dec-2023 |
|-------------------------------------------|--------------------------|---------------------------|
| 1. Interest Earned                        | 1,23,456.78              | 1,11,222.33               |
| 2. Provisions and Contingencies           | (5,432.10)Â³              | (4,000.00)                |
| 3. Share of Profit of Associates          | -                        | 55.00                     |

You would find the column for '{period}' and update the template as follows:
"""
    example_1_code = """
"normalized_figures": [
  //...
  {
    "playbook_id": "interest_earned",
    "raw_label": "Interest Earned",
    "value": 123456.78, // <-- Correctly parsed from the right column
    "confidence": "high",
    "representation": "currency",
    "currency_context": "INR",
    "unit_scale": "crore"
  },
  {
    "playbook_id": "provisions_other_than_tax_and_contingencies",
    "raw_label": "Provisions and Contingencies",
    "value": -5432.10, // <-- Correctly interpreted parenthetical as negative, ignored footnote
    "confidence": "high",
    "representation": "currency",
    "currency_context": "INR",
    "unit_scale": "crore"
  },
  {
    "playbook_id": "share_of_profit_loss_of_associates",
    "raw_label": "Missing in Filing", // <-- Correctly identifies '-' as a missing value
    "value": null,
    "confidence": "high",
    "representation": null,
    "currency_context": null,
    "unit_scale": null
  },
  //...
]
"""
    return f"{example_1_desc}```json\n{example_1_code}```\n"

def _call_extraction_llm(
    client: genai.Client, pdf_bytes: bytes, statement_type: str,
    playbook_structure: Dict[str, Any], filing_period: str
) -> str:
    """Calls the Gemini API using the "Fill-in-the-Blank" method."""
    logging.info(f"      -> Preparing 'Fill-in-the-Blank' call for {statement_type}...")

    # 1. Create the JSON template to be filled
    template_figures = []
    for pid in playbook_structure['ordered_ids']:
        template_figures.append(
            NormalizedFigure(playbook_id=pid, raw_label="Missing in Filing", value=None, confidence="high",
                             representation=None, currency_context=None, unit_scale=None, ratio_context=None))
    response_template = ExtractionResponse(normalized_figures=template_figures, unmapped_from_pdf=[])
    template_json_str = response_template.model_dump_json(indent=2)

    # 2. Build the final prompt
    prompt = EXTRACTION_PROMPT_TEMPLATE
    few_shot_examples = generate_few_shot_examples(filing_period)
    final_prompt = prompt.format(
        period=filing_period,
        hierarchical_playbook_json=json.dumps(playbook_structure['hierarchy'], indent=2),
        json_template_placeholder=template_json_str,
        few_shot_examples_placeholder=few_shot_examples
    )

    # 3. Configure the API call with the Pydantic schema
    config = {**PRODUCTION_CONFIG}
    config['response_schema'] = ExtractionResponse
    config['system_instruction'] = SYSTEM_INSTRUCTION
    # 4. Make the call with retry logic
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=PRODUCTION_MODEL,
                contents=[
                    final_prompt,
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                ],
                config=types.GenerateContentConfig(**config),
            )
            # The genai library automatically parses the response into the Pydantic object
            return response.text
        except Exception as e:
            logging.warning(f"LLM call failed on attempt {attempt + 1}/{LLM_MAX_RETRIES}: {e}")
            if attempt + 1 == LLM_MAX_RETRIES:
                raise
            time.sleep(LLM_INITIAL_BACKOFF * (2 ** attempt))
    raise RuntimeError("LLM call failed after all retry attempts.")

def get_playbook_structure(playbook_path: Path, statement_type: str) -> Dict[str, Any]:
    """Loads playbook structure, filtering for only extractable leaf nodes."""
    def _get_leaf_ids_recursive(nodes):
        ids = []
        for node in nodes:
            if not node.get('children'):
                if node.get('extractable', True): # The filtering logic
                    ids.append(node['id'])
            else:
                ids.extend(_get_leaf_ids_recursive(node.get('children', [])))
        return ids

    with open(playbook_path, "r", encoding="utf-8") as f:
        playbook = list(yaml.safe_load_all(f))

    doc_key_map = {'pnl': 'pnl', 'balance_sheet': 'balance_sheet', 'cash_flow': 'cash_flow_indirect'}
    target_key = next((v for k, v in doc_key_map.items() if k in statement_type), None)
    if not target_key: raise ValueError(f"Unknown statement type: {statement_type}")

    for doc in playbook:
        if doc and doc.get("statement", "").startswith(target_key):
            nodes = doc.get("nodes", [])
            return {'hierarchy': nodes, 'ordered_ids': _get_leaf_ids_recursive(nodes)}

    raise ValueError(f"No playbook found for statement type: {statement_type}")

def get_filing_metadata_for_extraction(session: SQLAlchemySession, asset_id: int) -> Dict[str, Any]:
    """Helper to get ticker and period for a given asset_id."""
    stmt = select(IngestionJob.ticker, IngestionJob.fiscal_year, IngestionJob.quarter)\
        .join(JobAssetLink, JobAssetLink.job_id == IngestionJob.job_id)\
        .filter(JobAssetLink.asset_id == asset_id).limit(1)
    result = session.execute(stmt).first()
    if not result: raise ValueError(f"Could not find job metadata for asset_id {asset_id}")

    # Logic to create a human-readable period string like "31-Mar-2025"
    q_map = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (3, 31)}
    fy, q = result.fiscal_year, result.quarter
    month, day = q_map[q]
    year = fy if q <= 3 else fy + 1
    period_str = f"{day:02d}-{time.strftime('%b', time.gmtime(month*2629746))}-{year}"
    return {"ticker": result.ticker, "period": period_str}


# ==============================================================================
# --- THE ORIGINAL WORKFLOW LOGIC, NOW USING THE NEW HELPERS ---
# ==============================================================================

def process_single_document_extraction(doc_id: int, session: SQLAlchemySession):
    """Processes extraction for a single document with the new accuracy-focused logic."""
    final_extraction_data, failed_statements = {}, []
    try:
        # --- This part is from your original script ---
        parsed_doc = session.get(ParsedDocument, doc_id)
        if not parsed_doc or not parsed_doc.content:
            raise ValueError("Document not found or has no content.")
        isolated_paths = parsed_doc.content.get("isolated_statement_paths", {})
        if not isolated_paths:
            raise ValueError("No isolated statement paths found.")
        logging.info(f"Processing extraction for doc_id {doc_id} with {len(isolated_paths)} statements.")

        # --- NEW: Get filing metadata required for the prompt and initialize client ---
        metadata = get_filing_metadata_for_extraction(session, parsed_doc.asset_id)
        filing_period = metadata['period']
        client = _get_gemini_client()

        for statement_type, relative_path in isolated_paths.items():
            logging.info(f"  -> Extracting statement: {statement_type}")
            full_path = project_root / relative_path

            try:
                # --- NEW: This block now calls the new, more powerful LLM functions ---
                with open(full_path, "rb") as f: pdf_bytes = f.read()
                playbook_structure = get_playbook_structure(BANKING_PLAYBOOK_PATH, statement_type)
                response_text = _call_extraction_llm(client, pdf_bytes, statement_type, playbook_structure, filing_period)
                llm_data = json.loads(response_text)

                if not any(fig.get('value') is not None for fig in llm_data.get('normalized_figures', [])):
                    raise ValueError("LLM returned a valid structure but with no extracted financial data.")

                final_extraction_data[statement_type] = llm_data
                logging.info(f"    âœ… Successfully extracted {statement_type} with data.")
            except Exception as e:
                logging.error(f"    âŒ Failed to extract {statement_type}: {e}", exc_info=True)
                final_extraction_data[statement_type] = {"error": str(e)}
                failed_statements.append(f"{statement_type}: {str(e)}")

        # --- This database update logic is from your original script, preserved perfectly ---
        if not failed_statements:
            final_status, error_details = 'EXTRACTION_SUCCESS', None
            logging.info(f"âœ… All {len(isolated_paths)} statements extracted successfully for doc_id {doc_id}.")
        else:
            final_status = 'EXTRACTION_ERROR'
            error_details = f"Failed {len(failed_statements)}/{len(isolated_paths)} statements: {'; '.join(failed_statements)}"
            logging.error(f"âŒ Extraction failed for doc_id {doc_id}: {error_details}")

        new_content = parsed_doc.content.copy()
        new_content['llm_call_2_extraction'] = final_extraction_data

        update_stmt = update(ParsedDocument).where(ParsedDocument.doc_id == doc_id).values(
            content=new_content,
            parse_status=final_status,
            error_details=error_details,
            parser_version=PARSER_VERSION
        )
        session.execute(update_stmt)
        session.commit()

    except Exception as e:
        logging.error(f"âŒ Major error processing doc_id {doc_id}: {e}", exc_info=True)
        session.rollback()
        # Attempt a final update to mark the job as failed
        with get_session() as error_session:
            error_session.execute(update(ParsedDocument).where(ParsedDocument.doc_id == doc_id).values(
                parse_status='EXTRACTION_ERROR', error_details=f"Major processing error: {str(e)}", parser_version=PARSER_VERSION
            ))
            error_session.commit()

# --- PRESERVED: The functions below are from your original script, ensuring the workflow remains unchanged ---

def get_banking_doc_ids(session: SQLAlchemySession, all_doc_ids: List[int]) -> List[int]:
    """Filters doc_ids to only include banking companies."""
    if not all_doc_ids: return []
    banking_docs_query = select(ParsedDocument.doc_id).where(ParsedDocument.doc_id.in_(all_doc_ids))\
        .join(RawDataAsset, ParsedDocument.asset_id == RawDataAsset.asset_id)\
        .join(JobAssetLink, RawDataAsset.asset_id == JobAssetLink.asset_id)\
        .join(IngestionJob, JobAssetLink.job_id == IngestionJob.job_id)\
        .join(CompanyMaster, IngestionJob.ticker == CompanyMaster.ticker)\
        .join(Classification, CompanyMaster.classification_id == Classification.id)\
        .where(Classification.industry_name == 'Banks')
    banking_doc_ids = session.execute(banking_docs_query).scalars().all()
    logging.info(f"Filtered out {len(all_doc_ids) - len(banking_doc_ids)} non-banking companies. Processing {len(banking_doc_ids)} banking documents.")
    return banking_doc_ids

def _execute_extraction_for_worker(doc_id: int):
    """Worker function for multiprocessing."""
    try:
        with get_session() as db_session:
            process_single_document_extraction(doc_id, db_session)
    except Exception as e:
        logging.error(f"Worker process for doc_id {doc_id} crashed: {e}", exc_info=True)

def run_extractor_batch():
    """Runs extraction batch with banking industry filtering."""
    logging.info(f"--- Starting PDF Extractor Batch Run v{PARSER_VERSION} ---")

    with get_session() as session:
        # Find documents that have been isolated but not yet successfully extracted by this version
        stmt = select(ParsedDocument.doc_id).where(
            ParsedDocument.parse_status.in_(['ISOLATION_SUCCESS', 'EXTRACTION_ERROR']),
            ParsedDocument.parser_version != PARSER_VERSION
        )
        all_doc_ids = session.execute(stmt).scalars().all()
        banking_doc_ids = get_banking_doc_ids(session, all_doc_ids)

    if not banking_doc_ids:
        logging.info("No banking documents pending extraction for this version.")
        return

    logging.info(f"Found {len(banking_doc_ids)} banking documents for extraction with {MAX_WORKERS} workers.")
    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        list(executor.map(_execute_extraction_for_worker, banking_doc_ids))

    logging.info("--- Extractor batch run completed. ---")

if __name__ == '__main__':
    run_extractor_batch()