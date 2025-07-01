# debug_feed.py
# A temporary script to fetch and print the raw JSON feed for a specific ticker.

import requests
import json
import logging
import time
from pathlib import Path

# --- Configuration ---
TARGET_TICKER = "SBIN"

# Use a slightly wider date range to ensure we capture the announcement broadcast date.
SEARCH_START_DATE = "01-12-2023"
SEARCH_END_DATE = "31-05-2024"

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
    """Creates a new, authenticated session with NSE."""
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

if __name__ == '__main__':
    logging.info(f"--- Starting Debug Test for {TARGET_TICKER} ---")

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

    # --- THIS IS THE NEW DEBUGGING LOGIC ---
    found_records = False
    print("\n--- Found Records for SBIN ---")
    for announcement in master_list:
        # We will only filter by the ticker symbol to see all related records
        if announcement.get('symbol') == TARGET_TICKER:
            found_records = True
            # Print the entire JSON object, nicely formatted
            print(json.dumps(announcement, indent=2))
            print("-" * 40)
    
    if not found_records:
        print("!!! No records found for the symbol SBIN in the given date range. !!!")
    
    print("--- Debug Test Finished ---")