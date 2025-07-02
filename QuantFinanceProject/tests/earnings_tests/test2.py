# test_integrity_check_final.py
# FINAL VERSION: Uses a streaming GET request to reliably fetch headers.

import requests
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

# --- Configuration ---
REMOTE_FILE_URL = "https://nsearchives.nseindia.com/corporate/xbrl/INDAS_101209_1030568_19012024071754.xml"
LOCAL_FILE_PATH = Path("./test_downloads/RELIANCE_Consolidated.xml")

# --- NSE Constants ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
HEADERS = {
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def seed_session() -> requests.Session:
    """Creates a new, authenticated session with NSE by getting cookies."""
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session to get auth cookies...")
        resp = sess.get(UI_URL, timeout=30)
        resp.raise_for_status()
        time.sleep(1)
        logging.info("Session seeded successfully.")
        return sess
    except Exception as e:
        logging.error(f"Failed to seed session: {e}")
        raise

def get_remote_file_metadata(session: requests.Session, url: str) -> dict | None:
    """
    --- MODIFIED: Uses a streaming GET request to reliably fetch headers. ---
    """
    logging.info(f"Performing streaming GET to: {url}")
    try:
        # Use a 'with' statement to ensure the connection is always closed.
        with session.get(url, stream=True, timeout=10) as response:
            response.raise_for_status()
            
            # Extract the metadata from the response headers
            remote_size = int(response.headers.get('Content-Length', 0))
            last_modified_str = response.headers.get('Last-Modified')
            
            remote_modified_dt = None
            if last_modified_str:
                remote_modified_dt = datetime.strptime(
                    last_modified_str, '%a, %d %b %Y %H:%M:%S %Z'
                ).replace(tzinfo=timezone.utc)
                
            return {'size': remote_size, 'modified': remote_modified_dt}
            # The 'with' block automatically closes the connection here,
            # preventing the file content from being downloaded.

    except Exception as e:
        logging.error(f"Could not fetch remote metadata. Reason: {e}")
        return None

if __name__ == '__main__':
    logging.info("--- Starting Data Integrity Check Test (v3 - Streaming GET) ---")

    if not LOCAL_FILE_PATH.exists():
        logging.critical(f"Local file not found at: {LOCAL_FILE_PATH}.")
        exit()

    local_stat = LOCAL_FILE_PATH.stat()
    local_size = local_stat.st_size
    local_modified_dt = datetime.fromtimestamp(local_stat.st_mtime, tz=timezone.utc)
    
    logging.info(f"Local File Size      : {local_size} bytes")
    logging.info(f"Local File Modified  : {local_modified_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    try:
        http_session = seed_session()
        remote_metadata = get_remote_file_metadata(http_session, REMOTE_FILE_URL)
    except Exception:
        remote_metadata = None

    if remote_metadata:
        logging.info(f"Remote File Size     : {remote_metadata['size']} bytes")
        if remote_metadata['modified']:
            logging.info(f"Remote Last-Modified : {remote_metadata['modified'].strftime('%Y-%m-%d %H:%M:%S %Z')}")
        
        print("-" * 50)
        size_matches = (local_size == remote_metadata['size'])
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