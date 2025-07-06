# earnings_agent/ingestion/nse_scraper/ingestion_task.py
# Version: 2.3 (Corrected with XBRL Logic)
# Description:
# This version replaces the job creation and fiscal_date calculation logic with the
# proven, robust logic from the xbrl_ingestion_task.py script. This guarantees
# internal consistency and resolves the persistent lookup failure.

import time
import requests
import logging
import json
import hashlib
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from requests.exceptions import RequestException
from typing import List, Dict, Any, Optional

# --- Core Application Imports ---
from earnings_agent.storage.database import (
    create_ingestion_jobs,
    get_jobs_by_status,
    log_ingestion_success,
    log_ingestion_failure
)
from earnings_agent.config.universe import COMPANIES

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
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
POLITE_PAUSE_PER_COMPANY_SECONDS = 2
POLITE_PAUSE_PER_REQUEST_SECONDS = 3
SOURCE_TYPE = "NSE_SCRAPER"
INGESTION_SCRIPT_VERSION = "nse-api-ingestor-v1.0" # Per your request

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

# --- Main Ingestion Logic ---
def ingest_all_nse_api(start_date_str: str, to_date_str: str):
    logging.info(f">>> Starting MONOLITHIC NSE API ingestion v{INGESTION_SCRIPT_VERSION} <<<")
    start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    end_date = datetime.strptime(to_date_str, "%d-%m-%Y").date()

    # --- MODIFIED BLOCK 1: Job Creation ---
    # This logic is now identical to the proven xbrl_ingestion_task.py script.
    jobs_to_create: List[Dict] = []
    consolidation_types = ["Consolidated", "Standalone"]
    for company in COMPANIES:
        ticker = company["ticker"]
        current_fy, current_q = get_indian_fiscal_period(start_date)
        while True:
            # This complex-looking line correctly calculates the quarter end date.
            q_end_month, q_end_year = ((6, current_fy), (9, current_fy), (12, current_fy), (3, current_fy + 1))[current_q-1]
            q_end_date = date(q_end_year if q_end_month < 12 else q_end_year + 1, q_end_month % 12 + 1, 1) - relativedelta(days=1)
            
            if q_end_date > end_date:
                break
            
            if q_end_date >= start_date:
                for conso_type in consolidation_types:
                    jobs_to_create.append({
                        "ticker": ticker, "fiscal_year": current_fy, "quarter": current_q,
                        "source_type": SOURCE_TYPE, "consolidation_status": conso_type,
                        "ingestion_script_version": INGESTION_SCRIPT_VERSION
                    })
            
            # This correctly advances to the next fiscal quarter.
            current_fy, current_q = (current_fy + 1, 1) if current_q == 4 else (current_fy, current_q + 1)
    
    if jobs_to_create:
        create_ingestion_jobs(jobs_data=jobs_to_create)
        logging.info(f"Created/verified {len(jobs_to_create)} jobs in the database.")
    # --- END MODIFIED BLOCK 1 ---

    jobs_to_process = get_jobs_by_status(['PENDING', 'MISSING_AT_SOURCE', 'FETCH_FAILED'])
    if not jobs_to_process:
        logging.info("No re-triable jobs to process. Exiting.")
        return

    try:
        http_session = seed_session()
        master_list_params = {"index": "equities", "from_date": start_date_str, "to_date": to_date_str, "period": "Quarterly"}
        response_data = http_session.get(LISTING_API_URL, params=master_list_params, timeout=45).json()
        all_announcements = response_data if isinstance(response_data, list) else response_data.get('data', [])
        logging.info(f"Fetched {len(all_announcements)} total announcements from NSE master list.")
    except Exception as e:
        logging.critical(f"Could not fetch master list. Aborting. Error: {e}", exc_info=True)
        for job in jobs_to_process: log_ingestion_failure(job.job_id, 'FETCH_FAILED', 'Could not fetch master announcement list.')
        return

    announcements_map = {}
    for ann in all_announcements:
        if isinstance(ann, dict) and "symbol" in ann and "toDate" in ann:
            try:
                key_date = datetime.strptime(ann["toDate"], "%d-%b-%Y").date()
                key_symbol = ann["symbol"].strip()
                status_val = ann.get('consolidated', '').strip()
                key_status = "Unknown"
                if 'Non-Consolidated' in status_val: key_status = 'Standalone'
                elif 'Consolidated' in status_val: key_status = 'Consolidated'
                if key_status != "Unknown": announcements_map[(key_symbol, key_date, key_status)] = ann
            except (ValueError, TypeError): continue
    logging.info(f"Built lookup map with {len(announcements_map)} entries.")

    for job in jobs_to_process:
        fy, q, ticker, conso_status = job.fiscal_year, job.quarter, job.ticker, job.consolidation_status
        logging.info(f"--- Processing Job ID {job.job_id} for {ticker} {conso_status} Q{q} FY{fy} ---")
        
        # --- MODIFIED BLOCK 2: Fiscal Date Calculation ---
        # This logic is now identical to the job creation logic, guaranteeing a match.
        q_end_month, q_end_year = ((6, fy), (9, fy), (12, fy), (3, fy + 1))[q-1]
        fiscal_date = date(q_end_year if q_end_month < 12 else q_end_year + 1, q_end_month % 12 + 1, 1) - relativedelta(days=1)
        # --- END MODIFIED BLOCK 2 ---
        
        found_announcement = announcements_map.get((ticker, fiscal_date, conso_status))

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
                log_ingestion_success(job_id=job.job_id, raw_data_hash=json_hash, source_type=SOURCE_TYPE, data_content=raw_json_content, source_last_modified=filing_date_dt)
                logging.info(f"SUCCESS: Logged raw JSON for Job ID {job.job_id}")
            else:
                log_ingestion_failure(job.job_id, 'FETCH_FAILED', 'Could not retrieve details JSON from API.')
        else:
            log_ingestion_failure(job.job_id, 'MISSING_AT_SOURCE', f'Filing for {conso_status} not found in master list for fiscal date {fiscal_date}.')
        
        time.sleep(POLITE_PAUSE_PER_COMPANY_SECONDS)

    logging.info(">>> MONOLITHIC NSE API ingestion process finished. <<<")

if __name__ == '__main__':
    SEARCH_START_DATE = "01-04-2024"
    SEARCH_END_DATE = "30-12-2024"
    ingest_all_nse_api(start_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)
