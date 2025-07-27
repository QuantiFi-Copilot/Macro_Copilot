# scripts/run_backfill.py

import logging
import sys
from pathlib import Path

# --- Environment and Path Setup ---
try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    # Import all necessary functions and models
    from earnings_agent.storage.database import (
        get_session,
        get_unprocessed_approved_labels,
        mark_labels_as_processed
    )
    # MODIFIED: We now import the new, surgical update function
    from earnings_agent.normalization.normalizer_engine import update_staged_data_with_approved_label

except ImportError as e:
    print(f"Error: Failed to import project modules. Ensure this script is placed correctly. Details: {e}")
    sys.exit(1)

# --- Standard Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

def run_true_backfill_job():
    """
    Finds all approved but unprocessed label mappings and surgically updates
    the existing staged_normalized_data records.
    """
    logging.info("--- Starting TRUE Backfill Job for Approved Labels ---")

    # 1. Get the "to-do list" from the database
    labels_to_process = get_unprocessed_approved_labels()

    if not labels_to_process:
        logging.info("No new approved labels to process. Exiting.")
        return

    logging.info(f"Found {len(labels_to_process)} approved labels to backfill.")
    
    session = get_session()
    successfully_processed_labels = []
    
    try:
        # 2. Process each approved label one by one
        for label_mapping in labels_to_process:
            try:
                # 3. Call the new, surgical update function instead of the old one
                # This function updates the JSONB field directly and is highly efficient.
                update_staged_data_with_approved_label(label_mapping, session)
                
                # If the update succeeds, add it to our list to be marked as processed
                successfully_processed_labels.append(label_mapping.raw_label)

            except Exception as e:
                logging.error(f"❌ Failed to process label '{label_mapping.raw_label}'. It will be retried on the next run. Error: {e}", exc_info=True)
                session.rollback() # Rollback changes for this specific failed label
                continue # Move to the next label
        
        # 4. Mark all successfully processed labels as "done" in a single batch
        # This function commits its own transaction.
        if successfully_processed_labels:
            logging.info(f"Marking {len(successfully_processed_labels)} labels as processed in the database.")
            # We close the main session before this call to avoid transaction conflicts
            session.close() 
            mark_labels_as_processed(successfully_processed_labels)

    except Exception as e:
        logging.critical(f"A critical error occurred during the main backfill loop: {e}", exc_info=True)
        session.rollback()
    finally:
        if session.is_active:
            session.close()
        logging.info("--- Backfill Job Finished ---")


if __name__ == "__main__":
    run_true_backfill_job()