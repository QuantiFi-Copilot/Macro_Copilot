# /app/earnings_agent/finalization_engine/stage1.py

import sys
import logging
from pathlib import Path
from datetime import date

# --- Path Setup ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_ROOT))

from earnings_agent.storage.database import get_session, get_runs_ready_for_golden_record, create_fundamental_record
from earnings_agent.storage.models import QualityEngineRun

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

def _get_fiscal_date(year: int, quarter: int) -> date:
    """Converts fiscal year and quarter into a calendar quarter-end date."""
    if quarter == 1:
        return date(year, 6, 30)
    elif quarter == 2:
        return date(year, 9, 30)
    elif quarter == 3:
        return date(year, 12, 31)
    elif quarter == 4:
        # Q4 of a fiscal year ends in the next calendar year (e.g., Q4 FY2023 ends March 2024)
        return date(year + 1, 3, 31)
    else:
        raise ValueError(f"Invalid quarter: {quarter}")

def run_stage_1_create_master_records():
    """
    Finds completed QE runs, inspects their content to identify which
    consolidation types are present, and creates a separate master record
    in `fundamental_records` for each one.
    """
    logging.info("-> Finding completed runs ready for finalization...")
    
    runs_to_process = get_runs_ready_for_golden_record()
    
    if not runs_to_process:
        logging.info("-> No new records to create in the golden record.")
        return
        
    logging.info(f"-> Found {len(runs_to_process)} runs to process.")
    
    session = get_session()
    try:
        for run in runs_to_process:
            logging.info(f"  -> Processing run_id: {run.run_id} for doc_id: {run.doc_id}")
            
            job = run.parsed_document.asset.job_links[0].job
            
            # --- KEY CHANGE: Inspect content to find what to create ---
            found_statuses = set()
            extraction_keys = run.working_content.get("llm_call_2_extraction", {}).keys()
            if any("standalone" in key for key in extraction_keys):
                found_statuses.add("Standalone")
            if any("consolidated" in key for key in extraction_keys):
                found_statuses.add("Consolidated")

            if not found_statuses:
                logging.warning(f"    -> SKIPPING run_id {run.run_id}: No recognizable standalone or consolidated data found.")
                continue

            # Loop through the found statuses and create a record for each
            for status in found_statuses:
                record_data = {
                    "ticker": job.ticker,
                    "consolidation_status": status, # Pass the correct status
                    "fiscal_date": _get_fiscal_date(job.fiscal_year, job.quarter),
                    "period": f"Q{job.quarter}FY{job.fiscal_year}",
                    "filing_date": None,
                    "source_playbook": "sebi_banking",
                    "source_run_id": run.run_id
                }

                record_id = create_fundamental_record(session, record_data)
                logging.info(f"    -> Successfully created fundamental_record with id: {record_id} for {job.ticker} {record_data['period']} ({status})")

        session.commit()
        logging.info("-> Successfully created all master records for this batch.")

    except Exception as e:
        session.rollback()
        logging.error(f"-> A critical error occurred during master record creation: {e}", exc_info=True)
    finally:
        session.close()