# scripts/seed_nse_mappings.py
#
# Description:
# A one-time script to pre-seed the label_mapping_cache table with the known,
# trusted mappings from the original NSE decoder map. This avoids the cost and
# latency of using the LLM for labels whose correct mapping is already known.

import logging
import sys
from pathlib import Path

# --- Environment and Path Setup ---
# This allows the script to import modules from the main `earnings_agent` directory.
try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    # Import the necessary database function and the decoder map
    from earnings_agent.storage.database import upsert_label_mapping
    from earnings_agent.ingestion.nse_scraper.nse_decoder_map import NSE_DECODER_MAP

except ImportError as e:
    print(f"Error: Failed to import project modules. Details: {e}")
    sys.exit(1)

# --- Standard Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def seed_mappings():
    """
    Iterates through the NSE_DECODER_MAP and upserts each mapping
    into the database with an 'APPROVED' status.
    """
    logging.info(f"--- Starting NSE Label Mapping Seeding Script ---")
    
    if not NSE_DECODER_MAP:
        logging.warning("NSE_DECODER_MAP is empty. Nothing to seed.")
        return

    logging.info(f"Found {len(NSE_DECODER_MAP)} mappings to seed into the database.")
    
    success_count = 0
    for raw_label, normalized_label in NSE_DECODER_MAP.items():
        try:
            mapping_data = {
                "raw_label": raw_label,
                "normalized_label": normalized_label,
                "status": "APPROVED",
                "processed": True, # Mark as processed to prevent backfill job
                "reviewed_by": "SYSTEM_SEEDER"
            }
            # Use the existing database function to upsert the mapping
            upsert_label_mapping(mapping_data)
            success_count += 1
            logging.debug(f"Successfully seeded: '{raw_label}' -> '{normalized_label}'")
        except Exception as e:
            logging.error(f"Failed to seed mapping for '{raw_label}': {e}", exc_info=True)

    logging.info(f"--- Seeding Finished: Successfully upserted {success_count}/{len(NSE_DECODER_MAP)} mappings. ---")


if __name__ == "__main__":
    seed_mappings()