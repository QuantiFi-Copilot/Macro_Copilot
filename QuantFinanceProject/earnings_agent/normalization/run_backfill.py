# run_backfill.py - to fill the data in the staged_normalized_data table after the labels from the normalizer are approved.\

import logging
import sys
from pathlib import Path
from sqlalchemy import or_

# --- Environment and Path Setup ---
# This allows the script to import modules from the main `earnings_agent` directory.
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
    from earnings_agent.storage.models import ParsedDocument
    from earnings_agent.normalization.normalizer_engine import normalize_document

except ImportError as e:
    print(f"Error: Failed to import project modules. Ensure this script is placed correctly and that your project structure is intact. Details: {e}")
    sys.exit(1)

# --- Standard Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

def run_backfill_job():
    """
    Finds all approved but unprocessed label mappings and re-runs the
    normalization task for all documents affected by them.
    """
    logging.info("--- Starting Backfill Job for Approved Labels ---")

    # 1. Get the "to-do list" from the database
    labels_to_process = get_unprocessed_approved_labels()

    if not labels_to_process:
        logging.info("No new approved labels to process. Exiting.")
        return

    logging.info(f"Found {len(labels_to_process)} approved labels to backfill.")
    
    successfully_processed_labels = []
    
    for label_mapping in labels_to_process:
        raw_label = label_mapping.raw_label
        logging.info(f"--- Processing label: '{raw_label}' ---")
        
        try:
            # 2. Find all documents that contain this raw_label
            session = get_session()
            affected_docs = (
                session.query(ParsedDocument.doc_id)
                .filter(
                    # Support both layouts:
                    # 1) Top-level: content ? raw_label
                    # 2) Nested:    (content->'raw_facts') ? raw_label
                    or_(
                        ParsedDocument.content.has_key(raw_label),
                        ParsedDocument.content['raw_facts'].has_key(raw_label)
                    )
                )
                .all()
            )
            session.close()

            doc_ids_to_normalize = [doc.doc_id for doc in affected_docs]

            if not doc_ids_to_normalize:
                logging.warning(
                    f"Label '{raw_label}' was approved, but no parsed documents were found containing it. Leaving as UNPROCESSED so it can be retried."
                )
                continue

            logging.info(
                f"Found {len(doc_ids_to_normalize)} documents containing label '{raw_label}'. Re-normalizing them..."
            )

            # 3. Re-run normalization for each affected document
            # The normalizer will now use the approved mapping from the cache
            run_cache = {} # Use a fresh cache for this batch
            for doc_id in doc_ids_to_normalize:
                normalize_document(doc_id=doc_id, run_cache=run_cache)
            
            # If we reach here, all documents for this label were processed successfully
            successfully_processed_labels.append(raw_label)
            logging.info(f"Successfully processed all documents for label: '{raw_label}'")

        except Exception as e:
            logging.error(f"❌ Failed to process label '{raw_label}'. It will be retried on the next run. Error: {e}", exc_info=True)
            # We do NOT add it to the success list, so it won't be marked as processed
            continue

    # 4. Mark all successfully processed labels as "done" in a single batch
    if successfully_processed_labels:
        logging.info(f"Marking {len(successfully_processed_labels)} labels as processed in the database.")
        mark_labels_as_processed(successfully_processed_labels)

    logging.info("--- Backfill Job Finished ---")


if __name__ == "__main__":
    run_backfill_job()