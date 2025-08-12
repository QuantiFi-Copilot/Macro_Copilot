import json
import logging
import os
import sys
import time
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml

# --- Project Imports ---
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from google import genai
from google.genai import types
from google.oauth2 import service_account
from sqlalchemy import select, update
from sqlalchemy.orm import Session as SQLAlchemySession

from earnings_agent.storage.database import get_session
from earnings_agent.storage.models import ParsedDocument, RawDataAsset, JobAssetLink, IngestionJob, CompanyMaster, Classification
# --- MODIFICATION: Import the new instruction dictionary ---
from earnings_agent.parsing.pdf.pdf_extractor_config import EXTRACTION_SYSTEM_INSTRUCTION, EXTRACTION_PROMPT_TEMPLATE, STATEMENT_INSTRUCTIONS, PRODUCTION_CONFIG

# --- Configuration ---
PARSER_VERSION = "parser-version-1.0"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Gemini Configuration ---
GCP_PROJECT_ID = "pdf-extractor-467911"
GCP_LOCATION = "us-central1"
EXTRACTION_MODEL = "gemini-2.5-pro"

# --- Playbook & Paths ---
PLAYBOOKS_DIR = project_root / "earnings_agent" / "playbooks"
BANKING_PLAYBOOK_PATH = PLAYBOOKS_DIR / "sebi" / "metrics" / "sebi_banking.yml"

# --- Retry & Error Handling ---
LLM_MAX_RETRIES = 3
LLM_INITIAL_BACKOFF = 5

def _get_gemini_client():
    """Initializes and returns a production-ready Gemini client."""
    try:
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not credentials_path or not os.path.exists(credentials_path):
            raise FileNotFoundError("GOOGLE_APPLICATION_CREDENTIALS path not found or not set.")

        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        return genai.Client(
            project=GCP_PROJECT_ID,
            location=GCP_LOCATION,
            credentials=credentials
        )
    except Exception as e:
        logging.error(f"Fatal error initializing Gemini client: {e}", exc_info=True)
        raise

def load_playbook(playbook_path: Path) -> List[Dict[str, Any]]:
    """Loads the full playbook YAML file."""
    with open(playbook_path, "r", encoding="utf-8") as f:
        return list(yaml.safe_load_all(f))

def _build_hierarchical_structure(nodes: List[Dict]) -> Dict:
    """Recursively builds a hierarchical map of all nodes."""
    structure = {}
    for node in nodes:
        node_id = node['id']
        structure[node_id] = {'id': node_id, 'children': [child['id'] for child in node.get('children', [])]}
        if 'children' in node and node['children']:
            structure.update(_build_hierarchical_structure(node['children']))
    return structure

def _get_ordered_leaf_ids(nodes: List[Dict]) -> List[str]:
    """Correctly gets a flat, ordered list of ONLY LEAF node IDs."""
    ids = []
    for node in nodes:
        if not node.get('children'):
            ids.append(node['id'])
        else:
            ids.extend(_get_ordered_leaf_ids(node['children']))
    return ids

def get_enhanced_playbook_structure(statement_type: str) -> Dict[str, Any]:
    """Gets playbook structure, targeting only leaf nodes for extraction."""
    playbook = load_playbook(BANKING_PLAYBOOK_PATH)
    if "pnl" in statement_type: key = "pnl"
    elif "balance_sheet" in statement_type: key = "balance_sheet"
    elif "cash_flow" in statement_type:
        structure = {'hierarchy': {}, 'ordered_ids': [], 'methods': {}}
        for doc in playbook:
            if doc.get("statement") in ["cash_flow_direct", "cash_flow_indirect"]:
                method = doc.get("statement").replace("cash_flow_", "")
                nodes = doc.get("nodes", [])
                structure['hierarchy'].update(_build_hierarchical_structure(nodes))
                structure['methods'][method] = _get_ordered_leaf_ids(nodes)
        all_cf_leaves = structure['methods'].get('direct', []) + structure['methods'].get('indirect', [])
        structure['ordered_ids'] = sorted(list(set(all_cf_leaves)))
        return structure
    else:
        raise ValueError(f"Unknown statement type: {statement_type}")

    for doc in playbook:
        if doc.get("statement") == key:
            nodes = doc.get("nodes", [])
            return {
                'hierarchy': _build_hierarchical_structure(nodes),
                'ordered_ids': _get_ordered_leaf_ids(nodes),
                'methods': {}
            }
    raise ValueError(f"No playbook found for statement key: {key}")


def _call_extraction_llm(pdf_bytes: bytes, playbook_structure: Dict, statement_type: str) -> str:
    """Calls Gemini API with enhanced and conditional configuration."""
    client = _get_gemini_client()
    for attempt in range(LLM_MAX_RETRIES):
        try:
            hierarchical_json = json.dumps({'structure': playbook_structure['hierarchy'], 'extraction_order': playbook_structure['ordered_ids']}, indent=2)

            # --- MODIFICATION START: Dynamically build the prompt ---
            # Determine the correct key for the instruction dictionary.
            statement_key = "cash_flow" if "cash_flow" in statement_type else statement_type
            # Fetch the specific instructions, defaulting to an empty string if none are found.
            specific_instructions = STATEMENT_INSTRUCTIONS.get(statement_key, "")

            # Format the main prompt, now including the specific instructions.
            user_prompt = EXTRACTION_PROMPT_TEMPLATE.format(
                hierarchical_playbook_json=hierarchical_json,
                statement_type=statement_type,
                statement_specific_instructions=specific_instructions
            )
            # --- MODIFICATION END ---

            response = client.models.generate_content(
                model=EXTRACTION_MODEL,
                contents=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    user_prompt # Note: Best practice is to put the media first
                ],
                # Use the new, enhanced configuration from the config file
                config=PRODUCTION_CONFIG
            )
            if not response.text:
                raise ValueError("LLM returned an empty response.")
            return response.text
        except Exception as e:
            logging.warning(f"LLM call failed on attempt {attempt + 1}/{LLM_MAX_RETRIES}: {e}")
            if attempt + 1 == LLM_MAX_RETRIES:
                raise
            time.sleep(LLM_INITIAL_BACKOFF * (2 ** attempt))
    raise RuntimeError("LLM call failed after all retry attempts.")

def _validate_and_reorder_response(llm_output: Dict, expected_order: List[str]) -> Dict:
    """Validates LLM response and ensures correct ordering and completeness."""
    figures = llm_output.get("normalized_figures", [])
    figures_map = {fig['playbook_id']: fig for fig in figures}
    ordered_figures = [figures_map.get(pid) for pid in expected_order if pid in figures_map]
    llm_output["normalized_figures"] = ordered_figures
    return llm_output

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

def process_single_document_extraction(doc_id: int, session: SQLAlchemySession):
    """Processes extraction for a single document with robust success/failure logic."""
    final_extraction_data, failed_statements = {}, []
    try:
        parsed_doc = session.get(ParsedDocument, doc_id)
        if not parsed_doc or not parsed_doc.content:
            raise ValueError("Document not found or has no content.")
        isolated_paths = parsed_doc.content.get("isolated_statement_paths", {})
        if not isolated_paths:
            raise ValueError("No isolated statement paths found.")
        logging.info(f"Processing extraction for doc_id {doc_id} with {len(isolated_paths)} statements.")
        for statement_type, relative_path in isolated_paths.items():
            logging.info(f"  -> Extracting statement: {statement_type}")
            full_path = project_root / relative_path
            try:
                with open(full_path, "rb") as f: pdf_bytes = f.read()
                playbook_structure = get_enhanced_playbook_structure(statement_type)
                response_text = _call_extraction_llm(pdf_bytes, playbook_structure, statement_type)
                llm_data = json.loads(response_text)
                validated_data = _validate_and_reorder_response(llm_data, playbook_structure['ordered_ids'])
                if not any(fig.get('value') is not None for fig in validated_data.get('normalized_figures', [])):
                    raise ValueError("LLM returned a valid structure but with no extracted financial data.")
                final_extraction_data[statement_type] = validated_data
                logging.info(f"    ✅ Successfully extracted {statement_type} with data.")
            except Exception as e:
                logging.error(f"    ❌ Failed to extract {statement_type}: {e}", exc_info=True)
                final_extraction_data[statement_type] = {"error": str(e)}
                failed_statements.append(f"{statement_type}: {str(e)}")

        if not failed_statements:
            final_status, error_details = 'EXTRACTION_SUCCESS', None
            logging.info(f"✅ All {len(isolated_paths)} statements extracted successfully for doc_id {doc_id}.")
        else:
            final_status = 'EXTRACTION_ERROR'
            error_details = f"Failed {len(failed_statements)}/{len(isolated_paths)} statements: {'; '.join(failed_statements)}"
            logging.error(f"❌ Extraction failed for doc_id {doc_id}: {error_details}")

        new_content = parsed_doc.content.copy()
        new_content['llm_call_2_extraction'] = final_extraction_data
        
        parsed_doc.content = new_content
        
        parsed_doc.parse_status = final_status
        parsed_doc.error_details = error_details
        parsed_doc.parser_version = PARSER_VERSION
        
        session.commit()
    except Exception as e:
        logging.error(f"❌ Major error processing doc_id {doc_id}: {e}", exc_info=True)
        session.rollback()
        update_stmt = update(ParsedDocument).where(ParsedDocument.doc_id == doc_id).values(
            parse_status='EXTRACTION_ERROR', error_details=f"Major processing error: {str(e)}", parser_version=PARSER_VERSION
        )
        with get_session() as error_session:
            error_session.execute(update_stmt)
            error_session.commit()

def _execute_extraction_for_worker(doc_id: int):
    """
    Worker function for multiprocessing. It now creates its own
    database session to be completely independent.
    """
    try:
        # Each worker process gets its own session
        with get_session() as db_session:
            process_single_document_extraction(doc_id, db_session)
    except Exception as e:
        logging.error(f"Worker process for doc_id {doc_id} crashed: {e}", exc_info=True)
    finally:
        pass

def run_extractor_batch():
    """Runs extraction batch with banking industry filtering."""
    MAX_WORKERS = 4
    logging.info(f"--- Starting PDF Extractor Batch Run v{PARSER_VERSION} ---")

    # The main process gets the list of work.
    with get_session() as session:
        docs_to_process_query = select(ParsedDocument.doc_id).where(
            ParsedDocument.parser_version == PARSER_VERSION,
            ParsedDocument.parse_status.in_(['ISOLATION_SUCCESS', 'EXTRACTION_ERROR'])
        )
        all_doc_ids = session.execute(docs_to_process_query).scalars().all()
        banking_doc_ids = get_banking_doc_ids(session, all_doc_ids)

    if not banking_doc_ids:
        logging.info("No banking documents pending extraction.")
        return

    logging.info(f"Found {len(banking_doc_ids)} banking documents for extraction with {MAX_WORKERS} workers.")
    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        list(executor.map(_execute_extraction_for_worker, banking_doc_ids))

    logging.info("--- Extractor batch run completed. ---")

if __name__ == '__main__':
    run_extractor_batch()