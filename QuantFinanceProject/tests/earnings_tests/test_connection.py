# earnings_agent/ingestion/nse_scraper/test_db_connection.py
# A simple, targeted script to test ONLY the database connection and the
# get_jobs_by_status function from within the Docker container.

import logging
from earnings_agent.storage.database import get_jobs_by_status
from earnings_agent.storage.models import IngestionJob

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

def run_db_test():
    """
    Connects to the database, fetches pending jobs, and prints their details.
    """
    logging.info("--- Starting Database Connection Test ---")
    
    try:
        # 1. Call the exact same function as the main script.
        logging.info("Attempting to fetch jobs with status 'PENDING'...")
        pending_jobs = get_jobs_by_status(['PENDING'])
        
        # 2. Report what was found.
        if not pending_jobs:
            logging.warning("No jobs with status 'PENDING' found in the database.")
            logging.info("--- Test Finished ---")
            return

        logging.info(f"SUCCESS: Found {len(pending_jobs)} PENDING jobs.")
        
        # 3. Inspect the first job object to verify its type and content.
        first_job = pending_jobs[0]
        logging.info(f"Type of the returned job object: {type(first_job)}")
        
        print("\n" + "="*80)
        print("Details of the first PENDING job found:")
        print(f"  - Job ID: {first_job.job_id}")
        print(f"  - Ticker: {first_job.ticker}")
        print(f"  - FY: {first_job.fiscal_year}")
        print(f"  - Q: {first_job.quarter}")
        print(f"  - Status: {first_job.status}")
        print(f"  - Consolidation: {first_job.consolidation_status}")
        print(f"  - Script Version: {first_job.ingestion_script_version}")
        print("="*80 + "\n")

    except Exception as e:
        logging.error("DATABASE TEST FAILED: An exception occurred while trying to connect or fetch from the database.", exc_info=True)
    
    logging.info("--- Test Finished ---")

if __name__ == "__main__":
    run_db_test()
