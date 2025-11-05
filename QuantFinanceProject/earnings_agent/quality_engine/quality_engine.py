# /app/earnings_agent/quality_engine/quality_engine.py

import sys
import logging
from pathlib import Path

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[2]
sys.path.append(str(project_root))

from sqlalchemy import update
from earnings_agent.storage.database import (
    create_quality_engine_runs_for_new_documents,
    get_session,
)
from earnings_agent.storage.models import QualityEngineRun

# Import the stage-specific orchestrators and their versions
from earnings_agent.quality_engine.stage1.stage1 import run_stage_1_orchestrator, CURRENT_STAGE_1_VERSION
from earnings_agent.quality_engine.stage2.stage2 import run_stage_2_orchestrator, CURRENT_STAGE_2_VERSION
from earnings_agent.quality_engine.stage3.stage3 import run_stage_3_orchestrator, CURRENT_STAGE_3_VERSION
from earnings_agent.quality_engine.stage4.stage4 import run_stage_4_orchestrator, CURRENT_STAGE_4_VERSION

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

def trigger_waterfall_resets():
    """
    Finds runs that passed a stage with an old version and resets them
    and all subsequent stages to 'PENDING'. This enables the waterfall logic.
    """
    logging.info("Checking for version mismatches to trigger waterfall resets...")
    session = get_session()
    try:
        # Stage 1 version reset
        stmt_reset_s1 = (
            update(QualityEngineRun)
            .where(
                QualityEngineRun.stage_1_status == 'PASSED',
                QualityEngineRun.stage_1_version != CURRENT_STAGE_1_VERSION
            )
            .values(
                stage_1_status='PENDING', stage_1_version=None,
                stage_2_status='PENDING', stage_2_version=None,
                stage_3_status='PENDING', stage_3_version=None,
                stage_4_status='PENDING', stage_4_version=None,
                # FIX: Removed references to stage_5_status and stage_5_version which do not exist in the schema.
                failure_reason="Resetting due to new Stage 1 version",
                run_history=None
            )
        )
        result = session.execute(stmt_reset_s1)
        if result.rowcount > 0:
            logging.info(f"Reset {result.rowcount} runs due to new Stage 1 version '{CURRENT_STAGE_1_VERSION}'.")

        # Stage 2 version reset
        stmt_reset_s2 = (
            update(QualityEngineRun)
            .where(
                QualityEngineRun.stage_2_status == 'PASSED',
                QualityEngineRun.stage_2_version != CURRENT_STAGE_2_VERSION
            )
            .values(
                stage_2_status='PENDING', stage_2_version=None,
                stage_3_status='PENDING', stage_3_version=None,
                stage_4_status='PENDING', stage_4_version=None,
                # FIX: Removed references to stage_5_status and stage_5_version.
                failure_reason="Resetting due to new Stage 2 version"
            )
        )
        result = session.execute(stmt_reset_s2)
        if result.rowcount > 0:
            logging.info(f"Reset {result.rowcount} runs due to new Stage 2 version '{CURRENT_STAGE_2_VERSION}'.")

        # Stage 3 version reset
        stmt_reset_s3 = (
            update(QualityEngineRun)
            .where(
                QualityEngineRun.stage_3_status == 'PASSED',
                QualityEngineRun.stage_3_version != CURRENT_STAGE_3_VERSION
            )
            .values(
                stage_3_status='PENDING', stage_3_version=None,
                stage_4_status='PENDING', stage_4_version=None,
                # FIX: Removed references to stage_5_status and stage_5_version.
                failure_reason="Resetting due to new Stage 3 version",
            )
        )
        result = session.execute(stmt_reset_s3)
        if result.rowcount > 0:
            logging.info(f"Reset {result.rowcount} runs due to new Stage 3 version '{CURRENT_STAGE_3_VERSION}'.")
            
        # Stage 4 version reset
        stmt_reset_s4 = (
            update(QualityEngineRun)
            .where(
                QualityEngineRun.stage_4_status == 'PASSED',
                QualityEngineRun.stage_4_version != CURRENT_STAGE_4_VERSION
            )
            .values(
                stage_4_status='PENDING', stage_4_version=None,
                # FIX: Removed references to stage_5_status and stage_5_version.
                failure_reason="Resetting due to new Stage 4 version",
            )
        )
        result = session.execute(stmt_reset_s4)
        if result.rowcount > 0:
            logging.info(f"Reset {result.rowcount} runs due to new Stage 4 version '{CURRENT_STAGE_4_VERSION}'.")

        session.commit()
    except Exception as e:
        session.rollback()
        # FIX: The original file had a logging error here, showing the exception twice. Corrected for clarity.
        logging.error(f"Error during waterfall reset check: {e}")
    finally:
        session.close()

# FIX: The eligibility functions are no longer needed with the corrected logic in database.py and are removed.

def main():
    """Master orchestrator for the entire Quality Engine pipeline."""
    logging.info("==================================================")
    logging.info("🚀 Starting Master Quality Engine Orchestrator")
    logging.info("==================================================")

    # 1. Initialize the queue for any new documents
    logging.info("Seeding the queue with new documents...")
    create_quality_engine_runs_for_new_documents()
    logging.info("Queue seeding complete.")

    # 2. Trigger waterfall resets for any version changes
    trigger_waterfall_resets()

    # 3. Run Stage 1
    logging.info("--- Handing off to Stage 1 Orchestrator ---")
    run_stage_1_orchestrator()
    logging.info("--- Stage 1 Orchestrator finished ---")

    # 4. Run Stage 2
    logging.info("--- Handing off to Stage 2 Orchestrator ---")
    run_stage_2_orchestrator()
    logging.info("--- Stage 2 Orchestrator finished ---")

    # 5. Run Stage 3
    logging.info("--- Handing off to Stage 3 Orchestrator ---")
    run_stage_3_orchestrator()
    logging.info("--- Stage 3 Orchestrator finished ---")
    
    # 6. Run Stage 4
    logging.info("--- Handing off to Stage 4 Orchestrator ---")
    run_stage_4_orchestrator()
    logging.info("--- Stage 4 Orchestrator finished ---")

    logging.info("==================================================")
    logging.info("✅ Master Quality Engine Orchestrator Finished")
    logging.info("==================================================")

if __name__ == "__main__":
    main()