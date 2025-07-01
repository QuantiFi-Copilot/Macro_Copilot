# test_consolidated_final.py
# A standalone script with the corrected logic to find and download both filing types.

import requests
import json
import logging
import time
from pathlib import Path
from datetime import datetime

# --- Configuration ---
TARGET_TICKER = "RELIANCE"
# This is the "Period Ended" date. The API calls it 'toDate'.
TARGET_DATE_STR = "31-Dec-2023" 

# This is the date range of the announcement *broadcast*. For the period ending 31-Dec-2023,
# the broadcast date was 19-Jan-2024. This range must include the broadcast date.
SEARCH_START_DATE = "01-01-2024"
SEARCH_END_DATE = "31-01-2024"

# --- NSE Constants ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
JSON_ENDPOINT = BASE_URL + "/api/corporates-financial-results"
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def seed_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session...")
        resp = sess.get(UI_URL, timeout=30)
        resp.raise_for_status()
        time.sleep(1)
        return sess
    except Exception as e:
        logging.error(f"Failed to seed session: {e}")
        raise

def download_file_with_retry(session: requests.Session, url: str, output_path: Path) -> bool:
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            logging.info(f"  -> Successfully downloaded: {output_path.name}")
            time.sleep(2)
            return True
        except requests.exceptions.RequestException as e:
            logging.warning(f"  -> Attempt {attempt + 1} failed for {output_path.name}: {e}")
            time.sleep(3)
    logging.error(f"  -> All download attempts failed for {output_path.name}.")
    return False

if __name__ == '__main__':
    logging.info(f"--- Starting Final Test for {TARGET_TICKER} on {TARGET_DATE_STR} ---")

    try:
        session = seed_session()
        params = {"index": "equities", "from_date": SEARCH_START_DATE, "to_date": SEARCH_END_DATE, "period": "Quarterly"}
        response = session.get(JSON_ENDPOINT, params=params, timeout=20)
        response.raise_for_status()
        
        response_data = response.json()
        master_list = response_data.get('data', []) if isinstance(response_data, dict) else response_data
        
        logging.info(f"Fetched {len(master_list)} total announcements from NSE.")
    except Exception as e:
        logging.critical(f"Could not fetch master list. Exiting. Error: {e}")
        exit()

    filings_to_download = []
    for announcement in master_list:
        if announcement.get('symbol') == TARGET_TICKER and announcement.get('toDate') == TARGET_DATE_STR:
            xbrl_link = announcement.get('xbrl')
            if not xbrl_link or xbrl_link.strip().endswith('/-'):
                continue

            # --- FINAL, CORRECTED LOGIC ---
            # We now check the "consolidated" field for its exact value.
            consolidation_status = announcement.get('consolidated', '')
            
            if consolidation_status == 'Consolidated':
                logging.info(f"Found CONSOLIDATED filing for {TARGET_TICKER}.")
                filings_to_download.append({
                    "type": "Consolidated",
                    "url": BASE_URL + xbrl_link if not xbrl_link.startswith('http') else xbrl_link
                })
            elif consolidation_status == 'Non-Consolidated':
                logging.info(f"Found STANDALONE (Non-Consolidated) filing for {TARGET_TICKER}.")
                filings_to_download.append({
                    "type": "Standalone",
                    "url": BASE_URL + xbrl_link if not xbrl_link.startswith('http') else xbrl_link
                })
            # --------------------------------

    if not filings_to_download:
        logging.warning(f"Could not find any Consolidated or Standalone filings for {TARGET_TICKER} on {TARGET_DATE_STR}.")
    else:
        logging.info(f"Found {len(filings_to_download)} filings to download. Starting downloads...")
        output_dir = Path("./test_downloads")
        output_dir.mkdir(exist_ok=True)
        
        for filing in filings_to_download:
            file_name = f"{TARGET_TICKER}_{filing['type']}.xml"
            output_path = output_dir / file_name
            logging.info(f"Attempting to download {filing['type']} version...")
            download_file_with_retry(session, filing['url'], output_path)

    logging.info("--- Test Finished ---")