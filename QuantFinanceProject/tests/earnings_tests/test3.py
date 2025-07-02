# test_integrity_check.py
# A standalone script to prove we can verify a local file against a remote source
# using a lightweight HTTP HEAD request.

import requests
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

# --- Configuration ---
# IMPORTANT: Update these two variables to match your test case.

# 1. The original URL from which the file was downloaded.
#    (This is the URL for the Consolidated filing for RELIANCE ending 31-Dec-2023)
REMOTE_FILE_URL = "https://nsearchives.nseindia.com/corporate/xbrl/INDAS_101209_1030568_19012024071754.xml"

# 2. The path to the file you have already downloaded on your local machine.
LOCAL_FILE_PATH = Path("./test_downloads/RELIANCE_Consolidated.xml")


# --- NSE Constants (Self-contained) ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_remote_file_metadata(session: requests.Session, url: str) -> dict | None:
    """
    Performs a lightweight HEAD request to get remote file metadata.
    """
    logging.info(f"Performing HEAD request to: {url}")
    try:
        # requests.head() gets only the headers, not the file content.
        response = session.head(url, timeout=10)
        response.raise_for_status() # Will raise an error for 4xx or 5xx status codes
        
        # Extract the metadata from the response headers
        remote_size = int(response.headers.get('Content-Length', 0))
        last_modified_str = response.headers.get('Last-Modified')
        
        remote_modified_dt = None
        if last_modified_str:
            # Parse the standard HTTP-date string format and make it timezone-aware (UTC)
            remote_modified_dt = datetime.strptime(
                last_modified_str, 
                '%a, %d %b %Y %H:%M:%S %Z'
            ).replace(tzinfo=timezone.utc)
            
        return {'size': remote_size, 'modified': remote_modified_dt}

    except Exception as e:
        logging.error(f"Could not fetch remote metadata. Reason: {e}")
        return None

if __name__ == '__main__':
    logging.info("--- Starting Data Integrity Check Test ---")

    # Step 1: Check if the local file exists
    if not LOCAL_FILE_PATH.exists():
        logging.critical(f"Local file not found at: {LOCAL_FILE_PATH}. Please download the file first to run this test.")
        exit()

    # Step 2: Get metadata from the local file
    local_stat = LOCAL_FILE_PATH.stat()
    local_size = local_stat.st_size
    local_modified_dt = datetime.fromtimestamp(local_stat.st_mtime, tz=timezone.utc)
    logging.info(f"Local File Size    : {local_size} bytes")
    logging.info(f"Local File Modified  : {local_modified_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Step 3: Get metadata from the remote server
    # We don't need to seed a session for nsearchives.nseindia.com, a direct request is fine.
    http_session = requests.Session()
    http_session.headers.update(HEADERS)
    remote_metadata = get_remote_file_metadata(http_session, REMOTE_FILE_URL)

    # Step 4: Compare and conclude
    if remote_metadata:
        logging.info(f"Remote File Size   : {remote_metadata['size']} bytes")
        if remote_metadata['modified']:
            logging.info(f"Remote Last-Modified: {remote_metadata['modified'].strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            logging.warning("Remote server did not provide a 'Last-Modified' header.")

        print("-" * 50)
        # Perform the comparison
        size_matches = (local_size == remote_metadata['size'])
        # Only compare dates if the remote server provided one
        time_matches = (remote_metadata['modified'] is None or local_modified_dt >= remote_metadata['modified'])

        logging.info(f"Size Match: {size_matches}")
        logging.info(f"Timestamp Check (Local >= Remote): {time_matches}")

        if size_matches and time_matches:
            print("\nCONCLUSION: SUCCESS! Local file appears to be up-to-date.")
        else:
            print("\nCONCLUSION: WARNING! Remote file has changed. Re-download would be required.")
    else:
        print("-" * 50)
        print("\nCONCLUSION: FAILED to retrieve metadata from the remote server.")

    logging.info("--- Test Finished ---")