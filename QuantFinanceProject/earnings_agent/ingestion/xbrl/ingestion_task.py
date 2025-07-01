# earnings_agent/ingestion/xbrl/ingestion_task.py
import time
import requests
import logging
import hashlib
from pathlib import Path
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from requests.exceptions import RequestException

from earnings_agent.storage.database import (
    create_ingestion_jobs,
    get_jobs_by_status,
    log_ingestion_success,
    log_ingestion_failure
)
from earnings_agent.config.universe import COMPANIES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

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
DATA_ROOT = Path(__file__).resolve().parents[2] / "storage" / "data"
SESSION_TIMEOUT_SECONDS = 30
SOURCE_TYPE = "XBRL_FILE"
# Bumping version for this major logic change
INGESTION_SCRIPT_VERSION = "xbrl-ingestor-v3.0-monolithic-conso"
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_INITIAL_DELAY_SECONDS = 5

# (Helper functions are unchanged)
def get_indian_fiscal_period(report_end_date: date) -> tuple[int, int]:
    month = report_end_date.month
    year = report_end_date.year
    fiscal_year = year if month >= 4 else year - 1
    if month in (4, 5, 6): return fiscal_year, 1
    elif month in (7, 8, 9): return fiscal_year, 2
    elif month in (10, 11, 12): return fiscal_year, 3
    else: return fiscal_year, 4

def get_file_hash(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def seed_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session...")
        resp = sess.get(UI_URL, timeout=SESSION_TIMEOUT_SECONDS)
        resp.raise_for_status()
        time.sleep(2)
        return sess
    except Exception as e:
        logging.error(f"Failed to seed session: {e}")
        raise

def download_file_with_retry(session: requests.Session, url: str, output_path: Path) -> bool:
    for attempt in range(DOWNLOAD_MAX_RETRIES):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            logging.info(f"Successfully downloaded: {output_path.name}")
            time.sleep(2)
            return True
        except RequestException as e:
            logging.warning(f"Attempt {attempt + 1} failed for {output_path.name}: {e}")
            if attempt + 1 == DOWNLOAD_MAX_RETRIES:
                logging.error(f"All {DOWNLOAD_MAX_RETRIES} download attempts failed for {output_path.name}.")
                return False
            delay = DOWNLOAD_INITIAL_DELAY_SECONDS * (2 ** attempt)
            time.sleep(delay)
    return False

def ingest_all_xbrl(start_date_str: str, to_date_str: str):
    logging.info(f">>> Starting MONOLITHIC XBRL ingestion v{INGESTION_SCRIPT_VERSION} <<<")
    
    start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    end_date = datetime.strptime(to_date_str, "%d-%m-%Y").date()

    # --- CHANGE 1: MANIFEST GENERATION ---
    # Now creates jobs for both Consolidated and Standalone types.
    jobs_to_create = []
    consolidation_types = ["Consolidated", "Standalone"]
    for company in COMPANIES:
        for conso_type in consolidation_types:
            ticker, (current_fy, current_q) = company["ticker"], get_indian_fiscal_period(start_date)
            while True:
                q_end_month, q_end_year = ((6, current_fy), (9, current_fy), (12, current_fy), (3, current_fy + 1))[current_q-1]
                q_end_date = date(q_end_year if q_end_month < 12 else q_end_year + 1, q_end_month % 12 + 1, 1) - relativedelta(days=1)
                if q_end_date > end_date: break
                if q_end_date >= start_date:
                    jobs_to_create.append({
                        "ticker": ticker, "fiscal_year": current_fy, "quarter": current_q,
                        "source_type": SOURCE_TYPE, "consolidation_status": conso_type,
                        "ingestion_script_version": INGESTION_SCRIPT_VERSION
                    })
                current_fy, current_q = (current_fy + 1, 1) if current_q == 4 else (current_fy, current_q + 1)
    
    if jobs_to_create:
        create_ingestion_jobs(jobs_data=jobs_to_create)
        logging.info(f"Created/verified {len(jobs_to_create)} jobs in the database.")
    
    jobs_to_process = get_jobs_by_status(['PENDING', 'MISSING_AT_SOURCE', 'FETCH_FAILED'])
    if not jobs_to_process:
        logging.info("No pending jobs to process. Exiting.")
        return
        
    try:
        http_session = seed_session()
        params = {"index": "equities", "from_date": start_date_str, "to_date": to_date_str, "period": "Quarterly"}
        r = http_session.get(JSON_ENDPOINT, params=params, timeout=20)
        r.raise_for_status()
        response_data = r.json()
        all_announcements = response_data.get('data', []) if isinstance(response_data, dict) else response_data
        logging.info(f"Fetched {len(all_announcements)} total announcements from NSE.")
    except Exception as e:
        logging.critical(f"Could not fetch master list. Aborting. Error: {e}", exc_info=True)
        for job in jobs_to_process:
            log_ingestion_failure(job.job_id, 'FETCH_FAILED', 'Could not fetch master announcement list.')
        return

    # --- CHANGE 2: PRECISE ANNOUNCEMENT MAPPING ---
    # The map key now includes the consolidation status for precise lookups.
    announcements_map = {}
    for ann in all_announcements:
        if isinstance(ann, dict) and "symbol" in ann and "toDate" in ann:
            key_date = datetime.strptime(ann["toDate"], "%d-%b-%Y").date()
            key_symbol = ann["symbol"]
            status_val = ann.get('consolidated', '')
            
            key_status = "Unknown"
            if status_val == 'Consolidated':
                key_status = 'Consolidated'
            elif status_val == 'Non-Consolidated':
                key_status = 'Standalone'
            
            if key_status != "Unknown":
                announcements_map[(key_symbol, key_date, key_status)] = ann

    for job in jobs_to_process:
        fy, q, ticker, conso_status = job.fiscal_year, job.quarter, job.ticker, job.consolidation_status
        logging.info(f"--- Processing Job ID {job.job_id} for {ticker} {conso_status} Q{q} FY{fy} ---")
        
        q_end_month, q_end_year = ((6, fy), (9, fy), (12, fy), (3, fy + 1))[q-1]
        fiscal_date = date(q_end_year if q_end_month < 12 else q_end_year + 1, q_end_month % 12 + 1, 1) - relativedelta(days=1)
        
        # --- CHANGE 3: PRECISE LOOKUP ---
        # The key now includes the consolidation_status from the job.
        found_announcement = announcements_map.get((ticker, fiscal_date, conso_status))

        if found_announcement and found_announcement.get("xbrl") and not found_announcement.get("xbrl").strip().endswith("/-"):
            xml_url_path = found_announcement["xbrl"]
            full_xml_url = BASE_URL + xml_url_path if not xml_url_path.startswith('http') else xml_url_path
            
            company_dir = DATA_ROOT / "raw" / "xbrl" / ticker
            company_dir.mkdir(parents=True, exist_ok=True)
            
            # --- CHANGE 4: UNIQUE FILE NAMING ---
            file_name = f"{ticker}_FY{fy}_Q{q}_{conso_status}.xml"
            out_path = company_dir / file_name

            if out_path.exists():
                file_hash = get_file_hash(out_path)
                log_ingestion_success(job.job_id, file_hash, SOURCE_TYPE, str(out_path.resolve()))
            elif download_file_with_retry(http_session, full_xml_url, out_path):
                file_hash = get_file_hash(out_path)
                log_ingestion_success(job.job_id, file_hash, SOURCE_TYPE, str(out_path.resolve()))
            else:
                log_ingestion_failure(job.job_id, 'FETCH_FAILED', f'Download failed for URL: {full_xml_url}')
        else:
            log_ingestion_failure(job.job_id, 'MISSING_AT_SOURCE', f'Filing for {conso_status} not found in master list.')
            
        time.sleep(0.5) # A smaller pause is fine here as we are not hammering the API in a loop.

    logging.info(">>> MONOLITHIC ingestion process finished. <<<")

if __name__ == '__main__':
    SEARCH_START_DATE = "01-12-2021"
    SEARCH_END_DATE = "30-04-2024"
    ingest_all_xbrl(start_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)