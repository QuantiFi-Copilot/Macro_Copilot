# earnings_agent/sources/xbrl/fetch_xbrl.py [PRODUCTION VERSION v3.3 with Full Guardrails]

import time
import requests
import logging
import json
from pathlib import Path
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from requests.exceptions import RequestException, HTTPError
from zoneinfo import ZoneInfo

from earnings_agent.storage.database import upsert_raw_source, upsert_ingestion_log, get_session
from earnings_agent.storage.models import RawSource
from earnings_agent.config.universe import COMPANIES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# --- Configuration ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
JSON_ENDPOINT = BASE_URL + "/api/corporates-financial-results"
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01", "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
DATA_ROOT = Path(__file__).resolve().parents[2] / "storage" / "data"
SESSION_TIMEOUT_SECONDS = 30
SOURCE_TYPE = "XBRL_FILE"
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_INITIAL_DELAY_SECONDS = 5

def get_indian_fiscal_period(report_end_date: date) -> tuple[int, int]:
    month = report_end_date.month
    year = report_end_date.year
    fiscal_year = year if month >= 4 else year - 1
    if month in (4, 5, 6): return fiscal_year, 1
    elif month in (7, 8, 9): return fiscal_year, 2
    elif month in (10, 11, 12): return fiscal_year, 3
    else: return fiscal_year, 4

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

def fetch_financial_results(sess: requests.Session, from_date: str, to_date: str) -> list[dict]:
    params = {"index": "equities", "from_date": from_date, "to_date": to_date, "period": "Quarterly"}
    try:
        logging.info(f"Fetching master list of filings for range {from_date} to {to_date}...")
        r = sess.get(JSON_ENDPOINT, params=params, timeout=20)
        r.raise_for_status()
        response_data = r.json()
        if isinstance(response_data, list):
            return response_data
        elif isinstance(response_data, dict) and 'data' in response_data:
            return response_data.get('data', [])
        else:
            logging.warning("API returned an unknown or empty format.")
            return []
    except (RequestException, json.JSONDecodeError) as e:
        logging.error(f"Failed to fetch or parse master list: {e}")
    return []

def download_file_with_retry(session: requests.Session, url: str, output_path: Path) -> bool:
    """
    Attempts to download a file with multiple retries using exponential backoff.
    Includes a polite pause AFTER a successful download.
    """
    for attempt in range(DOWNLOAD_MAX_RETRIES):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            logging.info(f"Successfully downloaded: {output_path.name}")
            
            # --- GUARDRAIL RESTORED: Polite 2-second pause after every successful download ---
            time.sleep(2)
            return True
            
        except RequestException as e:
            logging.warning(f"Attempt {attempt + 1} failed for {output_path.name}: {e}")
            if attempt + 1 == DOWNLOAD_MAX_RETRIES:
                logging.error(f"All {DOWNLOAD_MAX_RETRIES} download attempts failed for {output_path.name}.")
                return False
            
            delay = DOWNLOAD_INITIAL_DELAY_SECONDS * (2 ** attempt)
            logging.info(f"Waiting for {delay} seconds before retrying...")
            time.sleep(delay)
    return False

def generate_expected_filings(start_date: date, end_date: date, companies: list) -> dict:
    # This function is correct and remains unchanged
    expectations = {}
    for company in companies:
        ticker = company["ticker"]
        expectations[ticker] = []
        current_fy, current_q = get_indian_fiscal_period(start_date)
        while True:
            if current_q == 1: quarter_end_month, quarter_end_year = 6, current_fy
            elif current_q == 2: quarter_end_month, quarter_end_year = 9, current_fy
            elif current_q == 3: quarter_end_month, quarter_end_year = 12, current_fy
            else: quarter_end_month, quarter_end_year = 3, current_fy + 1
            
            next_month_year = quarter_end_year if quarter_end_month < 12 else quarter_end_year + 1
            next_month = quarter_end_month + 1 if quarter_end_month < 12 else 1
            quarter_end_date = date(next_month_year, next_month, 1) - relativedelta(days=1)

            if quarter_end_date > end_date: break
            
            if quarter_end_date >= start_date:
                expectations[ticker].append({
                    "fiscal_year": current_fy, "quarter": current_q, "fiscal_date": quarter_end_date
                })
            
            if current_q == 4: current_q, current_fy = 1, current_fy + 1
            else: current_q += 1
    return expectations

def ingest_xbrl_for_universe(from_date_str: str, to_date_str: str):
    logging.info(">>> Starting Expectation-Driven XBRL ingestion process (v3.3 with Full Guardrails) <<<")
    
    start_date = datetime.strptime(from_date_str, "%d-%m-%Y").date()
    end_date = datetime.strptime(to_date_str, "%d-%m-%Y").date()
    
    expected_filings_by_ticker = generate_expected_filings(start_date, end_date, COMPANIES)
    logging.info(f"Generated expectations for {len(expected_filings_by_ticker)} companies.")

    try:
        http_session = seed_session()
        all_announcements = fetch_financial_results(http_session, from_date_str, to_date_str)
        logging.info(f"Fetched {len(all_announcements)} total announcements from NSE API.")
    except Exception as e:
        logging.critical(f"Could not establish session or fetch master list. Aborting. Error: {e}")
        return

    announcements_map = { (ann["symbol"], datetime.strptime(ann["toDate"], "%d-%b-%Y").date()): ann 
                          for ann in all_announcements if isinstance(ann, dict) and "symbol" in ann and "toDate" in ann }
    
    db_session = get_session()
    try:
        for ticker, expectations in expected_filings_by_ticker.items():
            logging.info(f"--- Processing expectations for {ticker} ---")
            for expectation in expectations:
                fiscal_date = expectation["fiscal_date"]
                log_entry = {
                    "ticker": ticker, "fiscal_year": expectation["fiscal_year"], "quarter": expectation["quarter"],
                    "source_type": SOURCE_TYPE, "checked_at": datetime.now(tz=ZoneInfo("Asia/Kolkata"))
                }
                
                found_announcement = announcements_map.get((ticker, fiscal_date))
                
                if found_announcement and found_announcement.get("xbrl") and not found_announcement.get("xbrl").strip().endswith("/-"):
                    log_entry["status"] = "FOUND"
                    logging.info(f"FOUND: Filing for {ticker} Q{expectation['quarter']} FY{expectation['fiscal_year']}.")
                    xml_url_path = found_announcement["xbrl"]
                    full_xml_url = BASE_URL + xml_url_path if not xml_url_path.startswith('http') else xml_url_path
                    company_dir = DATA_ROOT / "raw" / "xbrl" / ticker
                    company_dir.mkdir(parents=True, exist_ok=True)
                    file_name = f"{ticker}_FY{expectation['fiscal_year']}_Q{expectation['quarter']}.xml"
                    out_path = company_dir / file_name

                    source_data = {"ticker": ticker, "fiscal_date": fiscal_date, "source_type": SOURCE_TYPE, "source_url": full_xml_url, "local_path": str(out_path.resolve()), "raw_content": None}
                    upsert_raw_source(source_data)
                    
                    raw_source_obj = db_session.query(RawSource).filter_by(ticker=ticker, fiscal_date=fiscal_date, source_type=SOURCE_TYPE).first()
                    if raw_source_obj: log_entry["raw_source_id"] = raw_source_obj.id

                    if not out_path.exists():
                        if not download_file_with_retry(http_session, full_xml_url, out_path):
                            log_entry["status"] = "FETCH_ERROR"
                    else:
                        logging.info(f"Skipping download (already exists): {file_name}")
                else:
                    logging.warning(f"MISSING: No XBRL filing found for {ticker} Q{expectation['quarter']} FY{expectation['fiscal_year']}.")
                    log_entry["status"] = "MISSING_AT_SOURCE"
                    log_entry["raw_source_id"] = None

                upsert_ingestion_log(log_entry)
            
            # GUARDRAIL CONFIRMED: Pause between processing each company
            time.sleep(1) 
    finally:
        db_session.close()
    
    logging.info(">>> Expectation-Driven ingestion process finished. <<<")

if __name__ == "__main__":
    SEARCH_START_DATE = "01-04-2022"
    SEARCH_END_DATE = "25-06-2025"
    
    logging.info(f"Starting ingestion for date range: {SEARCH_START_DATE} to {SEARCH_END_DATE}")
    ingest_xbrl_for_universe(from_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)