# /app/earnings_agent/quality_engine/stage2/stage2.py

import sys
import logging
import datetime
from pathlib import Path
from datetime import timezone

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.storage.database import (
    get_runs_by_stage_2_status, # MODIFIED: This local function will be removed in favor of the database.py version
    update_quality_run,
    get_session
)
# --- NEW: Import additional models for the ticker lookup ---
from earnings_agent.storage.models import QualityEngineRun, ParsedDocument, JobAssetLink, IngestionJob
from earnings_agent.quality_engine.stage2.stage_2a_calculations import run_calculation_validation
from earnings_agent.quality_engine.stage2.stage_2b_reconciliation import check_reconciliation_possibilities
from sqlalchemy import select
from sqlalchemy.orm import joinedload

# --- Configuration ---
CURRENT_STAGE_2_VERSION = "1.0"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- REMOVED: This function now lives in database.py ---
# def get_runs_by_stage_2_status(...):

def _append_history(run_history: list, step: str, status: str, details: dict = None):
    """Helper to append a new record to the run history."""
    if run_history is None:
        run_history = []
    
    record = {
        "step": step,
        "status": status,
        "timestamp": datetime.datetime.now(timezone.utc).isoformat()
    }
    if details:
        record["details"] = details
        
    run_history.append(record)
    return run_history

def run_stage_2_calculations():
    """
    Main function for Stage 2: Accounting Reconciliation.
    Checks all mathematical relationships and attempts reconciliation with unmapped metrics.
    Also writes a minimal content summary of auto-reconciliations into working_content.
    """
    logging.info("--- Running Stage 2: Accounting Reconciliation ---")
    
    runs_to_process = get_runs_by_stage_2_status(['PENDING', 'CALCULATION_MISMATCH'])
    if not runs_to_process:
        logging.info("No documents are currently pending Stage 2 processing.")
        return
    
    logging.info(f"Found {len(runs_to_process)} documents for Stage 2 processing.")
    
    session = get_session()
    try:
        for run in runs_to_process:
            logging.info(f"Processing doc_id: {run.doc_id}, run_id: {run.run_id}")
            
            try:
                # Resolve ticker for context (unchanged logic)
                stmt = (
                    select(IngestionJob.ticker)
                    .join(JobAssetLink, JobAssetLink.job_id == IngestionJob.job_id)
                    .join(ParsedDocument, ParsedDocument.asset_id == JobAssetLink.asset_id)
                    .where(ParsedDocument.doc_id == run.doc_id)
                    .limit(1)
                )
                ticker = session.execute(stmt).scalar_one_or_none()
                if not ticker:
                    raise ValueError(f"Could not resolve ticker for doc_id {run.doc_id}")

                working_content = run.working_content
                if not working_content or "llm_call_2_extraction" not in working_content:
                    raise ValueError("Critical: llm_call_2_extraction block is missing.")
                
                logging.info(f"  -> Running calculation validation for doc_id {run.doc_id} (Ticker: {ticker})")
                calculation_results = run_calculation_validation(working_content, ticker)
                
                logging.info(f"  -> Checking reconciliation possibilities for doc_id {run.doc_id}")
                enhanced_results = check_reconciliation_possibilities(calculation_results, working_content)

                # === NEW: Build minimal content summary of actual auto-reconciliations ===
                try:
                    # Ensure containers exist (backwards compatible)
                    stage2_summary = working_content.get('stage2_summary') or {}
                    reconciliations_list = stage2_summary.get('reconciliations') or []

                    # Walk results to find auto-reconciled calculations and capture only those
                    for statement_key, stmt_res in enhanced_results.get('statement_results', {}).items():
                        for calc in stmt_res.get('calculations', []):
                            if calc.get('auto_reconciled') is True:
                                entry = {
                                    "statement_key": statement_key,
                                    "parent_playbook_id": calc.get('rule_id') or calc.get('parent_playbook_id'),
                                    # Optional if available in your calc payload:
                                    "extracted_parent_value": calc.get('parent_value') or calc.get('extracted_parent_value'),
                                    "recomputed_children_sum_before": calc.get('recomputed_children_sum_before'),
                                    "missing_amount": calc.get('missing_amount'),
                                    "components_used": calc.get('auto_reconcile_components_used'),
                                    "recomputed_children_sum_after": calc.get('recomputed_children_sum_after'),
                                    "delta_after": calc.get('delta_after')
                                }
                                reconciliations_list.append(entry)

                    # Write back into working_content
                    stage2_summary['reconciliations'] = reconciliations_list
                    working_content['stage2_summary'] = stage2_summary
                except Exception as _e:
                    # Non-fatal: summary generation should never break Stage 2
                    logging.warning(f"Stage 2 content summary generation warning (doc_id={run.doc_id}): {_e}")

                reconciliation_summary = enhanced_results.get('reconciliation_summary', {})
                requires_human_review_statements = reconciliation_summary.get('requires_human_review_statements', 0)
                
                if requires_human_review_statements == 0:
                    logging.info(f"  -> SUCCESS for doc_id {run.doc_id}. All calculations validated or auto-reconciled.")
                    success_details = {
                        "total_statements": enhanced_results.get('total_statements', 0),
                        "auto_passable_statements": reconciliation_summary.get('auto_passable_statements', 0),
                        "reconciliation_tiers_summary": {
                            key: res.get('reconciliation_summary', {}) for key, res in enhanced_results['statement_results'].items()
                        }
                    }
                    update_quality_run(
                        run_id=run.run_id,
                        updates={
                            "stage_2_status": "PASSED",
                            "stage_2_version": CURRENT_STAGE_2_VERSION,
                            "failure_reason": None,
                            # NEW: persist enriched working_content with reconciliations summary
                            "working_content": working_content,
                            "run_history": _append_history(run.run_history, "2_calculations", "SUCCESS", success_details)
                        }
                    )
                else:
                    logging.error(f"  -> FAILURE for doc_id {run.doc_id}. {requires_human_review_statements} statements need human review.")
                    failed_statements = [key for key, res in enhanced_results['statement_results'].items() if res.get('requires_human_review', False)]
                    consolidated_reason = f"Statements requiring human review: {', '.join(failed_statements)}"
                    
                    failure_details = { "message": "Details omitted for brevity" }  # Keep existing behavior

                    update_quality_run(
                        run_id=run.run_id,
                        updates={
                            "stage_2_status": "CALCULATION_MISMATCH",
                            "failure_reason": consolidated_reason,
                            # NEW: persist enriched working_content with partial reconciliations (if any)
                            "working_content": working_content,
                            "run_history": _append_history(run.run_history, "2_calculations", "FAILURE", failure_details)
                        }
                    )
            
            except Exception as e:
                logging.error(f"Error processing doc_id {run.doc_id}: {e}", exc_info=True)
                update_quality_run(
                    run_id=run.run_id,
                    updates={
                        "stage_2_status": "CALCULATION_MISMATCH",
                        "failure_reason": f"Processing error: {str(e)}",
                        # Ensure we persist whatever content we have so far (non-destructive)
                        "working_content": working_content if 'working_content' in locals() else run.working_content,
                        "run_history": _append_history(run.run_history, "2_calculations", "FAILURE", {"error": str(e)})
                    }
                )
    finally:
        session.close()  # Close the session after the loop is done

def run_stage_2_orchestrator():
    """Main entry point for Stage 2 orchestrator."""
    run_stage_2_calculations()

if __name__ == "__main__":
    logging.info("Running Stage 2 Orchestrator directly.")
    run_stage_2_orchestrator()