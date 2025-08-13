import sys
import logging
from pathlib import Path

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[2]
sys.path.append(str(project_root))

from sqlalchemy import update
from earnings_agent.storage.database import (
    create_quality_engine_runs_for_new_documents,
    get_session, # Needed for the reset logic
)
from earnings_agent.storage.models import QualityEngineRun

# Import the stage-specific orchestrators and their versions
from earnings_agent.quality_engine.stage1.stage1 import run_stage_1_orchestrator, CURRENT_STAGE_1_VERSION
# from earnings_agent.quality_engine.stage2.stage2 import run_stage_2_orchestrator, CURRENT_STAGE_2_VERSION # For future stages

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
        # --- Check Stage 1 ---
        # Find runs where stage 1 passed but with an older version
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
                stage_5_status='PENDING', stage_5_version=None,
                failure_reason="Resetting due to new Stage 1 version",
                details=None
            )
        )
        result = session.execute(stmt_reset_s1)
        if result.rowcount > 0:
            logging.info(f"Reset {result.rowcount} runs due to new Stage 1 version '{CURRENT_STAGE_1_VERSION}'.")

        # --- Add checks for other stages here in the future ---
        # For example:
        # stmt_reset_s2 = update(QualityEngineRun).where(...).values(stage_2_status='PENDING', stage_3_status='PENDING', ...)
        # session.execute(stmt_reset_s2)

        session.commit()
    except Exception as e:
        session.rollback()
        logging.error(f"Error during waterfall reset check: {e}", exc_info=True)
    finally:
        session.close()

def main():
    """
    Master orchestrator for the entire Quality Engine pipeline.
    """
    logging.info("==================================================")
    logging.info("🚀 Starting Master Quality Engine Orchestrator")
    logging.info("==================================================")

    # 1. Initialize the queue for any new documents
    logging.info("Seeding the queue with new documents...")
    create_quality_engine_runs_for_new_documents()
    logging.info("Queue seeding complete.")

    # 2. NEW: Trigger waterfall resets for any version changes
    trigger_waterfall_resets()

    # 3. Run Stage 1 Orchestrator
    logging.info("--- Handing off to Stage 1 Orchestrator ---")
    run_stage_1_orchestrator()
    logging.info("--- Stage 1 Orchestrator finished ---")

    # Future stages will be called here

    logging.info("==================================================")
    logging.info("✅ Master Quality Engine Orchestrator Finished")
    logging.info("==================================================")

if __name__ == "__main__":
    main()