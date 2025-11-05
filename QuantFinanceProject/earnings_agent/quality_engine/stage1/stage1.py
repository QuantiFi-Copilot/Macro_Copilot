import sys
import logging
from pathlib import Path
import datetime
from datetime import timezone
from copy import deepcopy

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.storage.database import (
    get_runs_by_stage_1_status,
    update_quality_run,
    get_session
)
from earnings_agent.storage.models import IngestionJob, JobAssetLink
from earnings_agent.quality_engine.playbook_utils import load_playbook_leaf_nodes
from earnings_agent.quality_engine.expectations_utils import load_expectations
from earnings_agent.quality_engine.stage1.stage_1a_completeness import run_completeness_check
from earnings_agent.quality_engine.stage1.stage_1b_order import run_order_check_and_fix
from earnings_agent.quality_engine.stage1.stage_1c_metadata import run_metadata_check_and_fix
# --- NEW: Import stage 1d functions ---
from earnings_agent.quality_engine.stage1.stage_1d_ai_verification import (
    extract_low_confidence_metrics_by_statement,
    run_ai_verification_check,
    promote_metrics_to_high_confidence,
    _get_gemini_client
)
from sqlalchemy import select
import time

# --- Configuration ---
CURRENT_STAGE_1_VERSION = "1.1"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

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

def get_filing_metadata_for_verification(asset_id: int) -> dict:
    """Helper to get ticker and period for a given asset_id."""
    session = get_session()
    try:
        stmt = select(IngestionJob.ticker, IngestionJob.fiscal_year, IngestionJob.quarter)\
            .join(JobAssetLink, JobAssetLink.job_id == IngestionJob.job_id)\
            .filter(JobAssetLink.asset_id == asset_id).limit(1)
        result = session.execute(stmt).first()
        if not result:
            raise ValueError(f"Could not find job metadata for asset_id {asset_id}")

        # Logic to create a human-readable period string like "31-Mar-2025"
        q_map = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (3, 31)}
        fy, q = result.fiscal_year, result.quarter
        month, day = q_map[q]
        year = fy if q <= 3 else fy + 1
        period_str = f"{day:02d}-{time.strftime('%b', time.gmtime(month*2629746))}-{year}"
        return {"ticker": result.ticker, "period": period_str}
    finally:
        session.close()

def get_isolated_pdf_path(parsed_document, statement_type: str) -> Path:
    """Gets the isolated PDF path for a specific statement type."""
    if not parsed_document.content or "isolated_statement_paths" not in parsed_document.content:
        return None
    
    isolated_paths = parsed_document.content["isolated_statement_paths"]
    relative_path = isolated_paths.get(statement_type)
    
    if not relative_path:
        return None
    
    return project_root / relative_path

def run_stage_1a_checks():
    """Runs the completeness check (1a) on all documents that are ready for it."""
    logging.info("--- Running Stage 1a: Completeness Checks ---")
    playbook_leaf_nodes = load_playbook_leaf_nodes()
    if not playbook_leaf_nodes:
        logging.error("Could not load playbook leaf nodes. Aborting Stage 1a.")
        return

    runs_to_process = get_runs_by_stage_1_status(statuses=['PENDING', 'COMPLETENESS_ERROR'])
    if not runs_to_process:
        logging.info("No documents are currently pending the completeness check.")
        return

    logging.info(f"Found {len(runs_to_process)} documents for completeness check.")
    for run in runs_to_process:
        logging.info(f"Processing doc_id: {run.doc_id}, run_id: {run.run_id}")
        
        working_content = run.working_content
        if not working_content or "llm_call_2_extraction" not in working_content:
            logging.warning(f"  -> Skipping doc_id {run.doc_id}: No extraction content found.")
            update_quality_run(
                run_id=run.run_id,
                updates={
                    "stage_1_status": "COMPLETENESS_ERROR",
                    "failure_reason": "Critical: llm_call_2_extraction block is missing.",
                    "run_history": _append_history(run.run_history, "1a_completeness", "FAILURE", {"error": "Missing extraction data"})
                }
            )
            continue
        
        all_statements_passed = True
        for statement_key, statement_data in working_content["llm_call_2_extraction"].items():
            if 'pnl' in statement_key: playbook_key = 'pnl'
            elif 'balance_sheet' in statement_key: playbook_key = 'balance_sheet'
            elif 'cash_flow' in statement_key: playbook_key = 'cash_flow'
            else: continue
            expected_ids = playbook_leaf_nodes.get(playbook_key, [])
            result = run_completeness_check(statement_data, expected_ids)

            if result["status"] == "FAILURE":
                logging.error(f"  -> FAILURE for doc_id {run.doc_id} on statement '{statement_key}'.")
                update_quality_run(
                    run_id=run.run_id,
                    updates={
                        "stage_1_status": "COMPLETENESS_ERROR",
                        "failure_reason": f"Completeness check failed on: {statement_key}",
                        "run_history": _append_history(run.run_history, "1a_completeness", "FAILURE", result["details"])
                    }
                )
                all_statements_passed = False
                break 

        if all_statements_passed:
            logging.info(f"  -> SUCCESS for doc_id {run.doc_id}. All statements are complete.")
            update_quality_run(
                run_id=run.run_id,
                updates={
                    "stage_1_status": "COMPLETENESS_CHECK_PASSED",
                    "run_history": _append_history(run.run_history, "1a_completeness", "SUCCESS")
                }
            )

def run_stage_1b_checks():
    """Runs the order check (1b) on all documents that have passed 1a or previously failed 1b."""
    logging.info("--- Running Stage 1b: Order Validation and Fixing ---")
    playbook_leaf_nodes = load_playbook_leaf_nodes()
    if not playbook_leaf_nodes:
        logging.error("Could not load playbook leaf nodes. Aborting Stage 1b.")
        return
    
    runs_to_process = get_runs_by_stage_1_status(statuses=['COMPLETENESS_CHECK_PASSED', 'ORDER_ERROR'])

    if not runs_to_process:
        logging.info("No documents are currently pending the order check.")
        return

    logging.info(f"Found {len(runs_to_process)} documents to check for order.")
    for run in runs_to_process:
        logging.info(f"Processing doc_id: {run.doc_id}, run_id: {run.run_id}")
        
        working_content = run.working_content
        all_statements_passed = True
        content_was_updated = False

        for statement_key, statement_data in working_content["llm_call_2_extraction"].items():
            if 'pnl' in statement_key: playbook_key = 'pnl'
            elif 'balance_sheet' in statement_key: playbook_key = 'balance_sheet'
            elif 'cash_flow' in statement_key: playbook_key = 'cash_flow'
            else: continue
            expected_ids = playbook_leaf_nodes.get(playbook_key, [])
            result = run_order_check_and_fix(statement_data, expected_ids)

            if result["status"] == "FAILURE":
                logging.error(f"  -> FAILURE for doc_id {run.doc_id} on statement '{statement_key}'. Order could not be fixed.")
                update_quality_run(
                    run_id=run.run_id,
                    updates={
                        "stage_1_status": "ORDER_ERROR",
                        "failure_reason": f"Order check failed on: {statement_key}",
                        "run_history": _append_history(run.run_history, "1b_order", "FAILURE", result["details"])
                    }
                )
                all_statements_passed = False
                break
            
            if result["status"] == "FIXED":
                logging.info(f"  -> FIXED order for statement: {statement_key}")
                working_content["llm_call_2_extraction"][statement_key]['normalized_figures'] = result['data']
                content_was_updated = True

        if all_statements_passed:
            logging.info(f"  -> SUCCESS for doc_id {run.doc_id}. Order check passed/fixed.")
            
            updates = {
                "stage_1_status": "ORDER_CHECK_PASSED",
                "run_history": _append_history(run.run_history, "1b_order", "SUCCESS")
            }
            if content_was_updated:
                updates["working_content"] = working_content
                updates["run_history"][-1]["status"] = "FIXED"

            update_quality_run(run_id=run.run_id, updates=updates)

def run_stage_1c_checks():
    """
    Runs the metadata check (1c).
    Processes all statements in a document and aggregates failures
    instead of stopping at the first one.
    """
    logging.info("--- Running Stage 1c: Metadata Validation and Fixing ---")
    try:
        expectations = load_expectations()
    except Exception as e:
        logging.error(f"Could not load expectations file: {e}. Aborting Stage 1c.")
        return

    runs_to_process = get_runs_by_stage_1_status(statuses=['ORDER_CHECK_PASSED', 'METADATA_ERROR'])
    if not runs_to_process:
        logging.info("No documents are currently pending the metadata check.")
        return

    logging.info(f"Found {len(runs_to_process)} documents for metadata check.")
    for run in runs_to_process:
        logging.info(f"Processing doc_id: {run.doc_id}, run_id: {run.run_id}")
        
        working_content = run.working_content
        content_was_updated = False
        statement_failures = []

        for statement_key, statement_data in working_content["llm_call_2_extraction"].items():
            result = run_metadata_check_and_fix(statement_data, expectations)
            
            if result["status"] == "FAILURE":
                failure_reason = result["details"]["reason"]
                logging.error(f"  -> FAILURE for doc_id {run.doc_id} on statement '{statement_key}': {failure_reason}")
                statement_failures.append(f"Statement '{statement_key}': {failure_reason}")
            
            if result["status"] == "FIXED":
                logging.info(f"  -> FIXED metadata for statement: {statement_key}")
                print(f"AUTO-FIX SUMMARY for doc_id [{run.doc_id}], statement [{statement_key}]:")
                for fix_detail in result["details"]["summary"]:
                    print(f"  -> {fix_detail}")
                
                working_content["llm_call_2_extraction"][statement_key]['normalized_figures'] = result['data']
                content_was_updated = True
        
        if statement_failures:
            consolidated_reason = "; ".join(statement_failures)
            update_quality_run(
                run_id=run.run_id,
                updates={
                    "stage_1_status": "METADATA_ERROR",
                    "failure_reason": consolidated_reason,
                    "run_history": _append_history(run.run_history, "1c_metadata", "FAILURE", {"errors": statement_failures})
                }
            )
        else:
            final_status_log = "SUCCESS"
            updates = {
                "stage_1_status": "METADATA_CHECK_PASSED",
                "failure_reason": None
            }
            if content_was_updated:
                updates["working_content"] = working_content
                final_status_log = "FIXED"

            logging.info(f"  -> {final_status_log} for doc_id {run.doc_id}. Metadata check passed/fixed.")
            updates["run_history"] = _append_history(run.run_history, "1c_metadata", final_status_log)
            update_quality_run(run_id=run.run_id, updates=updates)

def run_stage_1d_checks():
    """Runs the AI verification check (1d) on documents that have passed metadata validation."""
    logging.info("--- Running Stage 1d: AI Cross-Verification ---")
    
    runs_to_process = get_runs_by_stage_1_status(['METADATA_CHECK_PASSED'])
    if not runs_to_process:
        logging.info("No documents are currently pending AI verification.")
        return

    logging.info(f"Found {len(runs_to_process)} documents for AI verification.")
    
    # Initialize Gemini client once
    try:
        client = _get_gemini_client()
    except Exception as e:
        logging.error(f"Could not initialize Gemini client: {e}. Aborting Stage 1d.")
        return
    
    for run in runs_to_process:
        logging.info(f"Processing doc_id: {run.doc_id}, run_id: {run.run_id}")
        
        try:
            # Extract low confidence metrics by statement
            low_conf_by_statement = extract_low_confidence_metrics_by_statement(run.working_content)
            
            if not low_conf_by_statement:
                # No low confidence metrics - skip LLM call
                logging.info(f"  -> All metrics already high confidence. Skipping verification.")
                print(f"ℹ️  doc_id {run.doc_id}: All metrics already high confidence. Skipping verification.")
                update_quality_run(run.run_id, {
                    "stage_1_status": "PASSED",
                    "stage_1_version": CURRENT_STAGE_1_VERSION,
                    "run_history": _append_history(run.run_history, "1d_ai_verification", "SUCCESS", 
                                                 {"message": "All metrics already high confidence. No verification needed."})
                })
                continue
            
            # Get filing metadata for period information
            metadata = get_filing_metadata_for_verification(run.parsed_document.asset_id)
            filing_period = metadata['period']
            
            # Process each statement with low confidence metrics
            all_statements_verified = True
            all_disagreements = []
            updated_content = deepcopy(run.working_content)
            total_verified_count = 0
            
            for statement_type, low_conf_metrics in low_conf_by_statement.items():
                logging.info(f"  -> Verifying {len(low_conf_metrics)} metrics in {statement_type}")
                
                # Get the isolated PDF for this statement
                pdf_path = get_isolated_pdf_path(run.parsed_document, statement_type)
                if not pdf_path or not pdf_path.exists():
                    logging.error(f"  -> Could not find isolated PDF for {statement_type}")
                    all_statements_verified = False
                    all_disagreements.append(f"{statement_type}: PDF not found")
                    continue
                
                # Read the PDF and call verification
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                
                # Call the core business logic
                result = run_ai_verification_check(low_conf_metrics, pdf_bytes, filing_period, client)
                
                if result["status"] == "FAILURE":
                    logging.error(f"  -> FAILURE for statement {statement_type}: {result['details']['summary']}")
                    print(f"\n🚨 AI DISAGREEMENT - doc_id {run.doc_id}, statement {statement_type}:")
                    for disagreement in result["details"]["disagreements"]:
                        print(f"  ❌ {disagreement}")
                    
                    all_statements_verified = False
                    all_disagreements.append(f"{statement_type}: {result['details']['summary']}")
                else:
                    # Success - update confidence to high for verified metrics
                    promote_metrics_to_high_confidence(updated_content, statement_type, result["verified_metrics"])
                    total_verified_count += len(result["verified_metrics"])
                    logging.info(f"  -> Successfully verified {len(result['verified_metrics'])} metrics in {statement_type}")
            
            # Update run status based on results
            if all_statements_verified:
                logging.info(f"  -> SUCCESS for doc_id {run.doc_id}. All {total_verified_count} low confidence metrics verified.")
                print(f"✅ doc_id {run.doc_id}: All {total_verified_count} low confidence metrics verified successfully.")
                update_quality_run(run.run_id, {
                    "stage_1_status": "PASSED", 
                    "stage_1_version": CURRENT_STAGE_1_VERSION,
                    "working_content": updated_content,
                    "run_history": _append_history(run.run_history, "1d_ai_verification", "SUCCESS",
                                                 {"message": f"All {total_verified_count} low confidence metrics verified and promoted to high confidence."})
                })
            else:
                consolidated_reason = "; ".join(all_disagreements)
                logging.error(f"  -> FAILURE for doc_id {run.doc_id}. LLM disagreements found.")
                update_quality_run(run.run_id, {
                    "stage_1_status": "AI_DISAGREEMENT_ERROR",
                    "failure_reason": f"LLM disagreements: {consolidated_reason}",
                    "run_history": _append_history(run.run_history, "1d_ai_verification", "FAILURE",
                                                 {"disagreements": consolidated_reason})
                })
                
        except Exception as e:
            logging.error(f"Error processing doc_id {run.doc_id}: {e}", exc_info=True)
            update_quality_run(run.run_id, {
                "stage_1_status": "AI_DISAGREEMENT_ERROR",
                "failure_reason": f"Processing error: {str(e)}",
                "run_history": _append_history(run.run_history, "1d_ai_verification", "FAILURE",
                                             {"error": str(e)})
            })

def run_stage_1_orchestrator():
    """Main orchestrator for all of Stage 1."""
    run_stage_1a_checks()
    run_stage_1b_checks()
    run_stage_1c_checks()
    run_stage_1d_checks()  # NEW

if __name__ == "__main__":
    logging.info("Running Stage 1 Orchestrator directly.")
    run_stage_1_orchestrator()