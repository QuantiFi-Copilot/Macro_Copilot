# /app/earnings_agent/finalization_engine/stage2.py

import sys
import logging
from pathlib import Path
from typing import Dict, Any, Tuple

# --- Path Setup ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_ROOT))

from sqlalchemy import update
from earnings_agent.storage.database import (
    get_session,
    get_master_records_pending_population,
    upsert_banking_fundamentals,
    upsert_custom_kpis,
)
from earnings_agent.storage.models import (
    FundamentalsBanking,
    CustomKpis,
    QualityEngineRun,
)

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

def _flatten_working_content(content: Dict[str, Any], consolidation_status: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Parses working_content and flattens it, but ONLY includes data from
    statements matching the requested consolidation_status.
    """
    banking_payload = {}
    kpi_payload = {}
    status_filter = consolidation_status.lower()

    extraction_data = content.get("llm_call_2_extraction", {})

    for statement_key, statement_data in extraction_data.items():
        # --- KEY CHANGE: Only process statements that match the status ---
        if status_filter in statement_key:
            for figure in statement_data.get("normalized_figures", []):
                playbook_id = figure.get("playbook_id")
                if playbook_id:
                    banking_payload[playbook_id] = figure.get("value")

            for kpi in statement_data.get("company_specific_kpis", []):
                normalized_label = kpi.get("normalized_label")
                if normalized_label:
                    kpi_payload[normalized_label] = kpi.get("value")

    return banking_payload, kpi_payload


def run_stage_2_populate_data_tables():
    """
    Finds master records and populates them with the correctly filtered
    (standalone or consolidated) data.
    """
    logging.info("-> Finding master records pending data population...")
    master_records = get_master_records_pending_population(child_model=FundamentalsBanking)

    if not master_records:
        logging.info("-> No new master records to populate.")
        return

    logging.info(f"-> Found {len(master_records)} records to process.")

    for record in master_records:
        session = get_session()
        try:
            # The record now has the consolidation_status on it
            logging.info(f"  -> Processing fundamental_record_id: {record.id} ({record.consolidation_status})")

            working_content = record.quality_engine_run.working_content
            if not working_content:
                logging.warning(f"    -> SKIPPING: No working_content found for run_id {record.source_run_id}.")
                continue

            # --- KEY CHANGE: Pass the status to the flattening function ---
            banking_data, kpi_data = _flatten_working_content(working_content, record.consolidation_status)

            if banking_data:
                banking_data["record_id"] = record.id
                upsert_banking_fundamentals(session, banking_data)
            
            if kpi_data:
                kpi_record_data = {"record_id": record.id, "kpi_data": kpi_data}
                upsert_custom_kpis(session, kpi_record_data)

            # NOTE: The is_loaded_to_golden_record flag is on the QualityEngineRun,
            # which is shared by both the Standalone and Consolidated FundamentalRecord.
            # It will be updated to TRUE by whichever record is processed last, which is correct.
            stmt = (
                update(QualityEngineRun)
                .where(QualityEngineRun.run_id == record.source_run_id)
                .values(is_loaded_to_golden_record=True)
            )
            session.execute(stmt)

            session.commit()
            logging.info(f"    -> Successfully loaded and finalized record_id: {record.id}")

        except Exception as e:
            session.rollback()
            logging.error(f"    -> FAILED to process record_id {record.id}: {e}", exc_info=True)
        finally:
            session.close()

    logging.info("-> Finished populating data tables for this batch.")