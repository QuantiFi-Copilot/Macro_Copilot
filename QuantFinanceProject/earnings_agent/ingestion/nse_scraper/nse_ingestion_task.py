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

    jobs_to_process = get_jobs_by_status(
        statuses=['PENDING', 'MISSING_AT_SOURCE', 'FETCH_FAILED'], 
        script_version=INGESTION_SCRIPT_VERSION
    )
    if not jobs_to_process:
        logging.info("No re-triable jobs to process. Exiting.")
        return

    try:
        http_session = seed_session()
        master_list_params = {
            "index": "equities",
            "from_date": start_date_str,
            "to_date": to_date_str,
            "period": "Quarterly",
        }
        response_data = fetch_json_with_retry(http_session, LISTING_API_URL, master_list_params)
        if not response_data:
            raise RuntimeError("Master list fetch failed after retries.")
        all_announcements = (
            response_data if isinstance(response_data, list) else response_data.get('data', [])
        )
        logging.info(f"Fetched {len(all_announcements)} total announcements from NSE master list.")
    except Exception as e:
        logging.critical(f"Could not fetch master list. Aborting. Error: {e}", exc_info=True)
        for job in jobs_to_process:
            log_ingestion_failure(job.job_id, 'FETCH_FAILED', 'Could not fetch master announcement list.')
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
    
        q_end_month, q_end_year = ((6, fy), (9, fy), (12, fy), (3, fy + 1))[q-1]
        fiscal_date = date(q_end_year if q_end_month < 12 else q_end_year + 1, q_end_month % 12 + 1, 1) - relativedelta(days=1)
    
        # Exact lookup on (symbol, period end date, consolidation status).
        # fiscal_date is the quarter end; NSE master list uses `toDate` = period end.
        found_announcement = announcements_map.get((ticker, fiscal_date, conso_status))

        if found_announcement:
            seq_id = found_announcement.get("seqNumber")
            # Use the dates exactly as NSE expects (retain hyphens)
            from_date_api = found_announcement.get("fromDate", "")
            to_date_api = found_announcement.get("toDate", "")

            # Map quarter label to Q1/Q2/Q3/Q4 as NSE expects inside params
            qtr_api = (
                found_announcement.get("relatingTo", "")
                .replace(" Quarter", "")
                .replace("First", "Q1")
                .replace("Second", "Q2")
                .replace("Third", "Q3")
                .replace("Fourth", "Q4")
            )

            # --- FLAG BUILD ★ NEW ★ ---
            audited_flag = "A" if (found_announcement.get("audited") == "Audited") else "U"

            cum_src = (found_announcement.get("cumulative", "") or "")
            # NSE uses N for Non-Cumulative, C otherwise
            cumulative_flag = "N" if "non-cumulative" in cum_src.lower() else "C"

            con_src = (found_announcement.get("consolidated", "") or "")
            # Consolidated -> C, Standalone -> N
            consolidated_flag = "C" if "consolidated" in con_src.lower() else "N"

            # These must be embedded in `params` too
            fr_old_new_flag = "N"   # New
            ind_flag = "N"          # index = equities

            indas_src = (found_announcement.get("indAs") or "")
            # Ind-AS: N = New, E = Existing/Old (never "O")
            ind_as_flag = "N" if "ind-as new" in indas_src.lower() else "E"

            # Concatenate in NSE's canonical order:
            # <from><to><Qn><A><CUM><FR><CON><IND><INDAS><TICKER>
            params_string = (
                f"{from_date_api}{to_date_api}"
                f"{qtr_api}{audited_flag}{cumulative_flag}{fr_old_new_flag}"
                f"{consolidated_flag}{ind_flag}{ind_as_flag}{ticker}"
            )

            details_params = {
                "index": "equities",
                "seq_id": seq_id,
                "params": params_string,
                "industry": "-",
                "frOldNewFlag": fr_old_new_flag,
                "ind": ind_flag,
                "format": "New",
            }
        
            # --- PHASE 2: Retry grid + broader success criterion --------------------
            def build_params(indas_flag: str, con_flag: str) -> str:
                return (
                    f"{from_date_api}{to_date_api}"
                    f"{qtr_api}{audited_flag}{cumulative_flag}{fr_old_new_flag}"
                    f"{con_flag}{ind_flag}{indas_flag}{ticker}"
                )

            # Prepare trial set: primary first, then minimal alternates
            trial_pairs = []
            primary = (ind_as_flag, consolidated_flag)
            trial_pairs.append(primary)
            for indas_try in ("N", "E"):
                for con_try in ("C", "N"):
                    if (indas_try, con_try) not in trial_pairs:
                        trial_pairs.append((indas_try, con_try))

            raw_json_content = None
            used_pair = None
            for indas_try, con_try in trial_pairs:
                params_try = build_params(indas_try, con_try)
                details_try = {
                    **details_params,
                    "params": params_try,
                }
                data_try = fetch_json_with_retry(http_session, DETAILS_API_URL, details_try)
                if not data_try:
                    continue
                # Broader success check: resultsData2 OR segmentData OR attachment_filename
                has_rd2 = isinstance(data_try.get("resultsData2"), dict) and bool(data_try["resultsData2"])
                has_seg = isinstance(data_try.get("segmentData"), list) and len(data_try["segmentData"]) > 0
                has_zip = bool(data_try.get("attachment_filename"))
                if has_rd2 or has_seg or has_zip:
                    raw_json_content = data_try
                    used_pair = (indas_try, con_try)
                    if used_pair != primary:
                        logging.warning(
                            f"Detail API required alt flags for {ticker} FY{fy} Q{q}: indAS={indas_try}, con={con_try}."
                        )
                    break

            if raw_json_content:
                json_hash = get_json_hash(raw_json_content)
                filing_date_str = (
                    raw_json_content.get('filingDate')
                    or raw_json_content.get('broadCastDate')
                    or raw_json_content.get('broadcastDate')
                )
                filing_date_dt = None
                if filing_date_str:
                    for _fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y"):
                        try:
                            filing_date_dt = datetime.strptime(filing_date_str, _fmt)
                            break
                        except ValueError:
                            continue

                # --- Fallback: use max segment broadcastDate if top-level absent/unparseable ---
                if filing_date_dt is None:
                    seg_dates = []
                    segs = raw_json_content.get("segmentData") or []
                    if isinstance(segs, list):
                        for seg in segs:
                            bd = None
                            try:
                                bd = (seg or {}).get("broadcastDate")
                            except AttributeError:
                                bd = None
                            if not bd:
                                continue
                            for _fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y"):
                                try:
                                    seg_dates.append(datetime.strptime(bd, _fmt))
                                    break
                                except ValueError:
                                    continue
                    if seg_dates:
                        filing_date_dt = max(seg_dates)
                        logging.debug(
                            f"Using segment broadcastDate fallback for {ticker} FY{fy} Q{q}: {filing_date_dt.isoformat()}"
                        )

                log_ingestion_success(
                    job_id=job.job_id,
                    raw_data_hash=json_hash,
                    source_type=SOURCE_TYPE,
                    data_content=raw_json_content,
                    source_last_modified=filing_date_dt,
                )
                logging.info(f"SUCCESS: Logged raw JSON for Job ID {job.job_id}")
            else:
                log_ingestion_failure(
                    job.job_id,
                    'FETCH_FAILED',
                    'Detail payload empty across retries; likely flag mismatch.',
                )
            # ------------------------------------------------------------------------
        else:
            log_ingestion_failure(job.job_id, 'MISSING_AT_SOURCE', f'Filing for {conso_status} not found in master list for fiscal date {fiscal_date}.')
    
        time.sleep(POLITE_PAUSE_PER_COMPANY_SECONDS)

    logging.info(">>> MONOLITHIC NSE API ingestion process finished. <<<")

    
if __name__ == '__main__':
    SEARCH_START_DATE = "01-01-2023"
    SEARCH_END_DATE = "30-06-2025"
    ingest_all_nse_api(start_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)
