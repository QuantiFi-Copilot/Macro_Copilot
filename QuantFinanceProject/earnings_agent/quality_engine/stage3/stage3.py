# /app/earnings_agent/quality_engine/stage3/stage3.py

import sys
import logging
import json
import datetime
import copy
from pathlib import Path
from typing import Dict, Any, List, Optional

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.storage.database import get_session, update_quality_run, get_mapping_from_cache, save_mapping_to_cache
from earnings_agent.storage.models import QualityEngineRun, ParsedDocument, JobAssetLink, IngestionJob
from earnings_agent.quality_engine.stage3.llm_client import suggest_mappings_for_statement
# Import the verifier from the UI utils
from earnings_agent.quality_engine.stage3.review_UI.stage_3_review_utils import verify_single_rule

from sqlalchemy import select
from sqlalchemy.orm import Session

# --- Configuration ---
CURRENT_STAGE_3_VERSION = "2.1" # Version bump for robust logic

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Helper Functions ---
def _append_history(run_history: list, step: str, status: str, details: dict = None) -> list:
    """Helper to append a new record to the run history."""
    if run_history is None: run_history = []
    record = {"step": step, "status": status, "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if details: record["details"] = details
    run_history.append(record)
    return run_history

def get_run_context(session: Session, doc_id: int) -> Dict:
    """Retrieves the ticker and other context for a given document ID."""
    stmt = (
        select(IngestionJob.ticker)
        .join(JobAssetLink, JobAssetLink.job_id == IngestionJob.job_id)
        .join(ParsedDocument, ParsedDocument.asset_id == JobAssetLink.asset_id)
        .where(ParsedDocument.doc_id == doc_id)
        .limit(1)
    )
    result = session.execute(stmt).scalar_one_or_none()
    return {"ticker": result} if result else {}

# --- NEW Core Logic Functions ---

def apply_and_verify_mapping(working_content: Dict, mapping: Any, statement_key: str) -> Optional[Dict]:
    """
    Applies an approved mapping to a copy of the working content and verifies the math.
    Returns the transformed content if verification passes, otherwise returns None.
    """
    temp_content = copy.deepcopy(working_content)
    
    # Robustly find the metric to move from either list
    unmapped_list = temp_content["llm_call_2_extraction"][statement_key].get("unmapped_from_pdf", [])
    kpi_list_source = temp_content["llm_call_2_extraction"][statement_key].get("company_specific_kpis", [])
    
    metric_to_move = None
    source_list = None
    
    # Prioritize finding the metric in the unmapped_from_pdf list first
    for metric in unmapped_list:
        if metric['raw_label'] == mapping.raw_label:
            metric_to_move = metric
            source_list = unmapped_list
            break
            
    # If not found, check if it was already in the kpi list (e.g., re-processing)
    if not metric_to_move:
        for metric in kpi_list_source:
             if metric['raw_label'] == mapping.raw_label:
                metric_to_move = metric
                source_list = kpi_list_source
                break

    if not metric_to_move:
        logging.warning(f"Could not find metric '{mapping.raw_label}' to apply from cache. It might have been processed already.")
        return working_content

    # --- BUG FIX STARTS HERE ---
    if mapping.mapping_type == 'standard':
        figures = temp_content["llm_call_2_extraction"][statement_key].get("normalized_figures", [])
        target_figure = next((f for f in figures if f['playbook_id'] == mapping.normalized_label), None)
        
        if target_figure:
            target_figure.update({
                'raw_label': metric_to_move['raw_label'],
                'value': metric_to_move['value'],
                'confidence': 'human_approved'
            })
            source_list.remove(metric_to_move)

            # VERIFY THE MATH
            if not verify_single_rule(temp_content, mapping.normalized_label):
                logging.error(f"Verification FAILED for mapping '{mapping.raw_label}' -> '{mapping.normalized_label}'.")
                return None # Signal failure
        else:
            logging.error(f"Target playbook ID '{mapping.normalized_label}' not found for standard mapping.")
            return None

    elif mapping.mapping_type == 'company_specific':
        kpi_list_dest = temp_content["llm_call_2_extraction"][statement_key].setdefault("company_specific_kpis", [])
        
        # Ensure the metric has the correct normalized_label before moving
        metric_to_move['normalized_label'] = mapping.normalized_label
        
        # Avoid duplicating if it's already in the destination list
        if not any(kpi['raw_label'] == metric_to_move['raw_label'] for kpi in kpi_list_dest):
             kpi_list_dest.append(metric_to_move)
        
        # Remove from source list if it exists there
        if metric_to_move in source_list:
             source_list.remove(metric_to_move)
    # --- BUG FIX ENDS HERE ---

    return temp_content


def process_document(run: QualityEngineRun):
    """
    Processes a single document according to the new simplified workflow.
    """
    session = get_session()
    try:
        context = get_run_context(session, run.doc_id)
        ticker = context.get('ticker')
        if not ticker:
            raise ValueError(f"Could not resolve ticker for doc_id {run.doc_id}")

        current_content = run.working_content
        needs_human_review = False

        for stmt_key, data in run.working_content.get("llm_call_2_extraction", {}).items():
            unmapped_from_pdf = data.get("unmapped_from_pdf", [])
            unmapped_from_kpis = data.get("company_specific_kpis", [])
            all_unmapped_metrics = list(unmapped_from_pdf) + list(unmapped_from_kpis)
            
            if not all_unmapped_metrics:
                continue

            metrics_to_send_to_llm = []
            
            for metric in all_unmapped_metrics:
                cached_mapping = get_mapping_from_cache(metric['raw_label'], ticker, stmt_key)
                if cached_mapping and cached_mapping.status == 'APPROVED':
                    logging.info(f"Applying approved mapping from cache for '{metric['raw_label']}'")
                    transformed_content = apply_and_verify_mapping(current_content, cached_mapping, stmt_key)
                    if transformed_content:
                        current_content = transformed_content
                    else:
                        raise ValueError(f"Cached mapping for '{metric['raw_label']}' failed verification.")
                elif not cached_mapping:
                    if not any(m['raw_label'] == metric['raw_label'] for m in metrics_to_send_to_llm):
                        metrics_to_send_to_llm.append(metric)

            if metrics_to_send_to_llm:
                needs_human_review = True
                logging.info(f"Found {len(metrics_to_send_to_llm)} metrics requiring new suggestions for statement '{stmt_key}'.")
                
                null_standard_labels = [
                    f['playbook_id'] for f in data.get("normalized_figures", []) if f.get('value') is None
                ]
                
                llm_response = suggest_mappings_for_statement(metrics_to_send_to_llm, null_standard_labels)
                
                for suggestion in llm_response.suggested_standard_mappings:
                    save_mapping_to_cache({
                        "raw_label": suggestion.raw_label, "ticker": ticker, "statement_key": stmt_key,
                        "mapping_type": "standard", "normalized_label": suggestion.target_playbook_id
                    })

                for kpi in llm_response.suggested_kpis:
                    save_mapping_to_cache({
                        "raw_label": kpi.raw_label, "ticker": ticker, "statement_key": stmt_key,
                        "mapping_type": "company_specific", "normalized_label": kpi.normalized_kpi_name
                    })

        final_status = "AWAITING_APPROVAL" if needs_human_review else "PASSED"
        
        update_quality_run(
            run_id=run.run_id,
            updates={
                "working_content": current_content,
                "stage_3_status": final_status,
                "stage_3_version": CURRENT_STAGE_3_VERSION,
                "failure_reason": None,
                "run_history": _append_history(run.run_history, "3_mapping_application", "SUCCESS")
            }
        )
        logging.info(f"SUCCESS for doc_id {run.doc_id}. Final status: {final_status}")

    except Exception as e:
        logging.error(f"Error processing doc_id {run.doc_id}: {e}", exc_info=True)
        update_quality_run(
            run_id=run.run_id,
            updates={
                "stage_3_status": "NORMALIZATION_ERROR",
                "failure_reason": f"Processing error: {str(e)}",
                "run_history": _append_history(run.run_history, "3_mapping_application", "FAILURE", {"error": str(e)})
            }
        )
    finally:
        session.close()

# --- Main Orchestrator ---

def run_stage_3_orchestrator():
    logging.info("--- Running Stage 3: Label Mapping Application (v2.1) ---")
    
    session = get_session()
    runs_to_process = session.execute(
        select(QualityEngineRun).where(
            QualityEngineRun.stage_2_status == 'PASSED',
            QualityEngineRun.stage_3_status.in_(['PENDING', 'NORMALIZATION_ERROR'])
        )
    ).scalars().all()
    session.close()

    if not runs_to_process:
        logging.info("No documents are currently pending Stage 3 processing.")
        return
        
    logging.info(f"Found {len(runs_to_process)} documents for Stage 3 processing.")
    
    for run in runs_to_process:
        process_document(run)

if __name__ == "__main__":
    run_stage_3_orchestrator()