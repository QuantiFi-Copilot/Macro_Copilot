# tests/earnings_tests/debug_ingestion.py
# A standalone test script to debug the core ingestion logic without any database interaction.

import time
import requests
import logging
import json
from pathlib import Path
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

# --- Configuration for this Test ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# The specific universe of companies you requested for the test
TEST_UNIVERSE = [
    {"ticker": "AXISBANK"}, {"ticker": "BHARTIARTL"}, {"ticker": "HAL"},
    {"ticker": "ICICIBANK"}, {"ticker": "ITC"}, {"ticker": "LICI"},
    {"ticker": "LT"}, {"ticker": "M&M"}, {"ticker": "RELIANCE"},
    {"ticker": "SBIN"}, {"ticker": "SUNPHARMA"}, {"ticker": "ULTRACEMCO"}
]

# The script will save files to a 'data' folder next to itself
DATA_ROOT = Path(__file__).parent / "data"

# --- NSE Constants (Self-contained) ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
JSON_ENDPOINT = BASE_URL + "/api/corporates-financial-results"
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01", "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

# --- Helper Functions (Self-contained) ---

def seed_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session...")
        resp = sess.get(UI_URL, timeout=30)
        resp.raise_for_status(); time.sleep(1)
        return sess
    except Exception as e:
        logging.error(f"Failed to seed session: {e}"); raise

def download_file(session: requests.Session, url: str, output_path: Path) -> bool:
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
        logging.info(f"  SUCCESS: Downloaded {output_path.name}")
        time.sleep(2) # Polite pause
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"  ERROR: Download failed for {url}. Reason: {e}")
        return False

# --- Main Test Logic ---

def run_ingestion_test(from_date_str: str, to_date_str: str):
    logging.info(">>> Starting Standalone Ingestion Test <<<")
    
    start_date = datetime.strptime(from_date_str, "%d-%m-%Y").date()
    end_date = datetime.strptime(to_date_str, "%d-%m-%Y").date()

    # Step 1: Generate expectations for both Consolidated and Standalone
    expected_filings = []
    for company in TEST_UNIVERSE:
        for conso_type in ["Consolidated", "Standalone"]:
            expected_filings.append({"ticker": company["ticker"], "consolidation_status": conso_type})

    # Step 2: Fetch the master list of all announcements from NSE
    try:
        http_session = seed_session()
        params = {"index": "equities", "from_date": from_date_str, "to_date": to_date_str, "period": "Quarterly"}
        r = http_session.get(JSON_ENDPOINT, params=params, timeout=20)
        r.raise_for_status()
        response_data = r.json()
        all_announcements = response_data.get('data', []) if isinstance(response_data, dict) else response_data
        logging.info(f"Fetched {len(all_announcements)} total announcements from NSE.")
    except Exception as e:
        logging.critical(f"Could not fetch master list. Aborting. Error: {e}")
        return

    # Step 3: Build a precise map of available announcements using the proven logic
    announcements_map = {}
    for ann in all_announcements:
        if isinstance(ann, dict) and "symbol" in ann and "toDate" in ann:
            try:
                key_date = datetime.strptime(ann["toDate"], "%d-%b-%Y").date()
                key_symbol = ann["symbol"]
                status_val = ann.get('consolidated', '')
                
                key_status = "Unknown"
                if status_val == 'Consolidated': key_status = 'Consolidated'
                elif status_val == 'Non-Consolidated': key_status = 'Standalone'
                
                if key_status != "Unknown":
                    # For this test, we only care about the URL
                    if ann.get("xbrl") and not ann.get("xbrl").strip().endswith("/-"):
                         announcements_map[(key_symbol, key_date, key_status)] = ann.get("xbrl")
            except (ValueError, TypeError):
                continue

    # Step 4: Loop through all companies and quarters and try to find a match
    # This logic is simpler than the main script; it checks all historical quarters in the date range.
    for company in TEST_UNIVERSE:
        ticker = company["ticker"]
        logging.info(f"--- Checking for {ticker} ---")
        for conso_type in ["Consolidated", "Standalone"]:
            found_match_for_this_type = False
            # Iterate through all dates in the map to find matches for this ticker/type
            for (map_ticker, map_date, map_conso_status), xbrl_path in announcements_map.items():
                if map_ticker == ticker and map_conso_status == conso_type:
                    found_match_for_this_type = True
                    fy, q = (map_date.year, (map_date.month-1)//3 + 1) # Simple date to quarter mapping for filename
                    
                    logging.info(f"  FOUND: Match for {ticker} {conso_type} ending {map_date}")
                    
                    # Create unique filename and download path
                    company_dir = DATA_ROOT / ticker
                    company_dir.mkdir(parents=True, exist_ok=True)
                    file_name = f"{ticker}_{map_date}_{conso_type}.xml"
                    out_path = company_dir / file_name
                    
                    if not out_path.exists():
                        full_url = BASE_URL + xbrl_path if not xbrl_path.startswith('http') else xbrl_path
                        download_file(http_session, full_url, out_path)
                    else:
                        logging.info(f"  SKIPPED: File already exists - {file_name}")

            if not found_match_for_this_type:
                logging.warning(f"  MISSING: No {conso_type} filings found for {ticker} in the entire date range.")
    
    logging.info(">>> Standalone Ingestion Test Finished. <<<")


if __name__ == '__main__':
    # Use a very wide date range to find all possible filings for the test universe
    SEARCH_START_DATE = "01-12-2021"
    SEARCH_END_DATE = "30-04-2024"
    run_ingestion_test(from_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)