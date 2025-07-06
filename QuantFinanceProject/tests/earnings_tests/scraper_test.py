# test_nse_scraper_v3.py
# Description:
# A standalone test script to validate the NEW, SIMPLER lookup strategy.
# It completely isolates the core logic from the database.
#
# Strategy to Test:
# 1. Build the lookup map using a key of: (ticker, fiscal_year, quarter, status).
# 2. Derive the fiscal_year and quarter for the map directly from the API's 'toDate'.
# 3. Perform a direct lookup in the main loop using the job's (ticker, fy, q, status).
#
# This test will definitively prove if the core matching logic is correct.

import time
import requests
import logging
import json
import hashlib
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from requests.exceptions import RequestException
from typing import List, Dict, Any, Optional

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Configuration & Constants ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
LISTING_API_URL = BASE_URL + "/api/corporates-financial-results"
DETAILS_API_URL = BASE_URL + "/api/corporates-financial-results-data"
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
SESSION_TIMEOUT_SECONDS = 30
API_MAX_RETRIES = 3
API_INITIAL_DELAY_SECONDS = 5
POLITE_PAUSE_PER_REQUEST_SECONDS = 2

# --- Mock Universe for Testing ---
COMPANIES_UNIVERSE = [
    {"ticker": "RELIANCE"},
    {"ticker": "HDFCBANK"},
    {"ticker": "TCS"},
    {"ticker": "INFY"},
]

# --- Helper Functions (Unchanged) ---
def get_indian_fiscal_period(report_end_date: date) -> tuple[int, int]:
    month = report_end_date.month
    year = report_end_date.year
    fiscal_year = year if month >= 4 else year - 1
    if month in (4, 5, 6): return fiscal_year, 1
    elif month in (7, 8, 9): return fiscal_year, 2
    elif month in (10, 11, 12): return fiscal_year, 3
    else: return fiscal_year, 4

def get_json_hash(data: dict) -> str:
    encoded_data = json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded_data).hexdigest()

def seed_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session by visiting the UI page...")
        resp = sess.get(UI_URL, timeout=SESSION_TIMEOUT_SECONDS)
        resp.raise_for_status()
        time.sleep(POLITE_PAUSE_PER_REQUEST_SECONDS)
        logging.info("Session seeded successfully.")
        return sess
    except RequestException as e:
        logging.error(f"Fatal error: Failed to seed session: {e}")
        raise

def fetch_json_with_retry(session: requests.Session, url: str, params: dict) -> dict | None:
    for attempt in range(API_MAX_RETRIES):
        try:
            time.sleep(POLITE_PAUSE_PER_REQUEST_SECONDS)
            resp = session.get(url, params=params, timeout=30)
            logging.debug(f"Requesting URL: {resp.url}")
            resp.raise_for_status()
            return resp.json()
        except (RequestException, json.JSONDecodeError) as e:
            logging.warning(f"Attempt {attempt + 1}/{API_MAX_RETRIES} failed for URL {url}: {e}")
            if attempt + 1 == API_MAX_RETRIES:
                logging.error(f"All {API_MAX_RETRIES} attempts failed for URL {url}.")
                return None
            delay = API_INITIAL_DELAY_SECONDS * (2 ** attempt)
            time.sleep(delay)
    return None

# --- Mocked Database Functions ---
def mock_log_ingestion_success(job: Dict, raw_data_hash: str, data_content: Dict, source_last_modified: Optional[datetime]):
    print("\n" + "="*80)
    print("✅ CONSOLE LOG: SUCCESS")
    print(f"  - Job Details: {job['ticker']} {job['consolidation_status']} Q{job['quarter']} FY{job['fiscal_year']}")
    print(f"  - Data Hash: {raw_data_hash}")
    print(f"  - Filing Date: {source_last_modified.strftime('%Y-%m-%d %H:%M:%S') if source_last_modified else 'N/A'}")
    snippet = json.dumps(list(data_content.items())[:2], indent=2)
    print(f"  - Data Snippet: {snippet}...")
    print("="*80 + "\n")

def mock_log_ingestion_failure(job: Dict, status: str, reason: str):
    print("\n" + "─"*80)
    print(f"❌ CONSOLE LOG: FAILURE ({status})")
    print(f"  - Job Details: {job['ticker']} {job['consolidation_status']} Q{job['quarter']} FY{job['fiscal_year']}")
    print(f"  - Reason: {reason}")
    print("─"*80 + "\n")

# --- Main Test Orchestration Logic ---
def run_standalone_test(start_date_str: str, to_date_str: str):
    logging.info(f">>> Starting STANDALONE TEST with NEW LOOKUP STRATEGY <<<")
    start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    end_date = datetime.strptime(to_date_str, "%d-%m-%Y").date()

    # 1. Create jobs in-memory
    jobs_to_process: List[Dict] = []
    job_id_counter = 1
    for company in COMPANIES_UNIVERSE:
        current_fy, current_q = get_indian_fiscal_period(start_date)
        while True:
            q_end_month, q_end_year = ((6, current_fy), (9, current_fy), (12, current_fy), (3, current_fy + 1))[current_q-1]
            q_end_date = date(q_end_year if q_end_month < 12 else q_end_year + 1, q_end_month % 12 + 1, 1) - relativedelta(days=1)
            if q_end_date > end_date: break
            if q_end_date >= start_date:
                for conso_type in ["Consolidated", "Standalone"]:
                    jobs_to_process.append({
                        "job_id": job_id_counter, "ticker": company["ticker"], "fiscal_year": current_fy, "quarter": current_q, "consolidation_status": conso_type
                    })
                    job_id_counter += 1
            current_fy, current_q = (current_fy + 1, 1) if current_q == 4 else (current_fy, current_q + 1)
    logging.info(f"Created {len(jobs_to_process)} in-memory jobs to process.")

    # 2. Fetch the master list
    try:
        http_session = seed_session()
        master_list_params = {"index": "equities", "from_date": start_date_str, "to_date": to_date_str, "period": "Quarterly"}
        response_data = http_session.get(LISTING_API_URL, params=master_list_params, timeout=45).json()
        all_announcements = response_data if isinstance(response_data, list) else response_data.get('data', [])
        logging.info(f"Fetched {len(all_announcements)} total announcements from NSE master list.")
    except Exception as e:
        logging.critical(f"Could not fetch master list. Aborting. Error: {e}", exc_info=True)
        return

    # 3. Build the lookup map with the NEW (ticker, fy, q, status) key
    announcements_map = {}
    for ann in all_announcements:
        if isinstance(ann, dict) and "symbol" in ann and "toDate" in ann:
            try:
                api_date = datetime.strptime(ann["toDate"], "%d-%b-%Y").date()
                ann_fy, ann_q = get_indian_fiscal_period(api_date)
                key_symbol = ann["symbol"].strip()
                status_val = ann.get('consolidated', '').strip()
                key_status = "Unknown"
                if 'Non-Consolidated' in status_val: key_status = 'Standalone'
                elif 'Consolidated' in status_val: key_status = 'Consolidated'
                if key_status != "Unknown":
                    lookup_key = (key_symbol, ann_fy, ann_q, key_status)
                    announcements_map[lookup_key] = ann
            except (ValueError, TypeError): continue
    logging.info(f"Built lookup map with {len(announcements_map)} entries.")

    # 4. Loop through jobs and perform the direct lookup
    for job in jobs_to_process:
        fy, q, ticker, conso_status = job['fiscal_year'], job['quarter'], job['ticker'], job['consolidation_status']
        logging.info(f"--- Processing Job ID {job['job_id']} for {ticker} {conso_status} Q{q} FY{fy} ---")
        
        lookup_key = (ticker, fy, q, conso_status)
        found_announcement = announcements_map.get(lookup_key)

        if found_announcement:
            seq_id = found_announcement.get("seqNumber")
            from_date_api = found_announcement.get("fromDate", "").replace("-","")
            to_date_api = found_announcement.get("toDate", "").replace("-","")
            qtr_api = found_announcement.get("relatingTo", "").replace(" Quarter","").replace("First","Q1").replace("Second","Q2").replace("Third","Q3").replace("Fourth","Q4")
            audited_flag = "A" if found_announcement.get("audited") == "Audited" else "U"
            cumulative_flag = "C" if "Non-cumulative" not in found_announcement.get("cumulative", "") else "N"
            consolidated_flag = "C" if "Non-Consolidated" not in found_announcement.get("consolidated", "") else "N"
            ind_as_flag = "N" if "Ind-AS New" in found_announcement.get("indAs", "") else "O"
            params_string = f"{from_date_api}{to_date_api}{qtr_api}{audited_flag}{cumulative_flag}{consolidated_flag}{ind_as_flag}{ticker}"
            details_params = { "index": "equities", "seq_id": seq_id, "params": params_string, "industry": "-", "frOldNewFlag": "N", "ind": "N", "format": "New" }
            
            raw_json_content = fetch_json_with_retry(http_session, DETAILS_API_URL, details_params)

            if raw_json_content:
                json_hash = get_json_hash(raw_json_content)
                filing_date_str = raw_json_content.get('filingDate') or raw_json_content.get('broadCastDate')
                filing_date_dt = datetime.strptime(filing_date_str, "%d-%b-%Y %H:%M") if filing_date_str else None
                mock_log_ingestion_success(job, json_hash, raw_json_content, filing_date_dt)
            else:
                mock_log_ingestion_failure(job, 'FETCH_FAILED', 'Could not retrieve details JSON from API.')
        else:
            mock_log_ingestion_failure(job, 'MISSING_AT_SOURCE', f'Filing for {conso_status} Q{q} FY{fy} not found in master list.')
        
        time.sleep(1)

    logging.info(">>> STANDALONE TEST FINISHED <<<")

if __name__ == "__main__":
    # A short, recent date range is best for testing
    SEARCH_START_DATE = "01-01-2024"
    SEARCH_END_DATE = "30-12-2024"
    run_standalone_test(start_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)
