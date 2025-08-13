import sys
import logging
from pathlib import Path

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[3]
sys.path.append(str(project_root))

from earnings_agent.storage.database import (
    get_runs_by_stage_1_status,
    update_quality_run, # Use the new generic update function
)
from earnings_agent.quality_engine.playbook_utils import load_playbook_leaf_nodes
from earnings_agent.quality_engine.stage1.stage_1a_completeness import run_completeness_check

# --- Configuration ---
# NEW: Stage-specific version constant
CURRENT_STAGE_1_VERSION = "1.0"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

def run_stage_1a_checks():
    """
    Runs the completeness check (1a) on all documents that are ready for it.
    """
    logging.info("--- Running Stage 1a: Completeness Checks ---")

    playbook_leaf_nodes = load_playbook_leaf_nodes()
    if not playbook_leaf_nodes:
        logging.error("Could not load playbook leaf nodes. Aborting Stage 1a.")
        return

    runs_to_process = get_runs_by_stage_1_status(statuses=['PENDING', 'COMPLETENESS_ERROR'])
    if not runs_to_process:
        logging.info("No documents are currently pending the completeness check.")
        return

    logging.info(f"Found {len(runs_to_process)} documents to check for completeness.")

    for run in runs_to_process:
        logging.info(f"Processing doc_id: {run.doc_id}, run_id: {run.run_id}")
        
        parsed_content = run.parsed_document.content
        if not parsed_content or "llm_call_2_extraction" not in parsed_content:
            logging.warning(f"  -> Skipping doc_id {run.doc_id}: No extraction content found.")
            update_quality_run(
                run_id=run.run_id,
                updates={
                    "stage_1_status": "COMPLETENESS_ERROR",
                    "failure_reason": "Critical: llm_call_2_extraction block is missing.",
                    "details": {"error": "Missing extraction data"}
                }
            )
            continue
        
        all_statements_passed = True
        for statement_key, statement_data in parsed_content["llm_call_2_extraction"].items():
            logging.info(f"  -> Checking statement: {statement_key}")
            
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
                        "details": result["details"]
                    }
                )
                all_statements_passed = False
                break 

        if all_statements_passed:
            logging.info(f"  -> SUCCESS for doc_id {run.doc_id}. All statements are complete.")
            update_quality_run(
                run_id=run.run_id,
                updates={"stage_1_status": "COMPLETENESS_CHECK_PASSED"}
            )

def run_stage_1_orchestrator():
    """
    Main orchestrator for all of Stage 1. It calls sub-stage functions in order.
    """
    # Run Stage 1a
    run_stage_1a_checks()

    # In the future, you will add calls to other sub-stage functions here.
    # For example:
    # run_stage_1b_checks() 
    # run_stage_1c_checks() 
    # run_stage_1d_checks() 
    # run_stage_1e_final_verification() # This last step would set status to PASSED and stamp the version.

if __name__ == "__main__":
    logging.info("This script is designed to be called by the master orchestrator, but running Stage 1 directly.")
    run_stage_1_orchestrator()