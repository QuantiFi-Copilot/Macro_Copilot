# /app/earnings_agent/finalization_engine/finalization_engine.py

import sys
import logging
from pathlib import Path

# --- Path Setup ---
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

# Import the stage-specific processors
from earnings_agent.finalization_engine.stage1 import run_stage_1_create_master_records
from earnings_agent.finalization_engine.stage2 import run_stage_2_populate_data_tables # <-- ADDED

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

def run_finalization_orchestrator():
    """
    Master orchestrator for the Finalization Engine.
    This process finds completed Quality Engine runs and loads them into the
    final "golden record" database tables.
    """
    logging.info("==================================================")
    logging.info("🚀 Starting Finalization Engine Orchestrator")
    logging.info("==================================================")

    # --- Stage 1: Create Master Records ---
    # This stage finds all eligible runs and creates their central
    # 'fundamental_records' entry, which acts as a hub for all other
    # golden record data.
    logging.info("--- Handing off to Stage 1: Master Record Creation ---")
    run_stage_1_create_master_records()
    logging.info("--- Stage 1 finished ---")
    
    # --- Stage 2: Populate Data Tables --- # <-- ADDED SECTION
    # This stage finds the newly created master records and populates the
    # detailed banking and KPI tables from the source JSON.
    logging.info("--- Handing off to Stage 2: Populate Data Tables ---")
    run_stage_2_populate_data_tables()
    logging.info("--- Stage 2 finished ---")
    
    logging.info("==================================================")
    logging.info("✅ Finalization Engine Orchestrator Finished")
    logging.info("==================================================")

if __name__ == "__main__":
    run_finalization_orchestrator()