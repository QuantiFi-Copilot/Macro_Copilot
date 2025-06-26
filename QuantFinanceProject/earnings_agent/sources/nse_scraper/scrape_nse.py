# earnings_agent/sources/nse_scraper/scrape_nse.py
# Ingestion script for fetching raw financial data from the NSE website API.
# Version: 1.0
# Description:
# This script is designed to fit into the project's multi-stage data pipeline.
# Its sole responsibility is to:
#   1. Identify expected financial filings for a given period based on the company universe.
#   2. Scrape the NSE's two-step API to fetch the raw JSON data for each filing.
#   3. Store the pristine, untouched JSON response in the `raw_sources` table.
#   4. Log the outcome of every attempt in the `ingestion_log` table for auditability.
#
# It mirrors the "Expectation-Driven" logic and robustness of `fetch_xbrl.py`.

import time
import requests
import logging
import json
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from requests.exceptions import RequestException
from zoneinfo import ZoneInfo

# Internal project imports for database and configuration
from earnings_agent.storage.database import upsert_raw_source, upsert_ingestion_log, get_session
from earnings_agent.storage.models import RawSource
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

# Headers to mimic a browser, crucial for avoiding being blocked.
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

# Politeness and Robustness settings
SESSION_TIMEOUT_SECONDS = 30
API_MAX_RETRIES = 3
API_INITIAL_DELAY_SECONDS = 5 # Initial delay for retries, will be increased exponentially.
POLITE_PAUSE_PER_COMPANY_SECONDS = 2 # Pause between processing each company.
POLITE_PAUSE_PER_REQUEST_SECONDS = 3 # Pause between individual API calls.

# As requested, the source type for data from this script.
SOURCE_TYPE = "NSE_SCRAPER"


# --- Helper Functions (mirrored from fetch_xbrl.py for consistency) ---

def get_indian_fiscal_period(report_end_date: date) -> tuple[int, int]:
    """Calculates the Indian financial year and quarter from a report's end date."""
    month = report_end_date.month
    year = report_end_date.year
    fiscal_year = year if month >= 4 else year - 1
    if month in (4, 5, 6): return fiscal_year, 1
    if month in (7, 8, 9): return fiscal_year, 2
    if month in (10, 11, 12): return fiscal_year, 3
    else: return fiscal_year, 4


def generate_expected_filings(start_date: date, end_date: date, companies: list) -> dict:
    """Generates a dictionary of all expected quarterly filings for each company in the universe."""
    expectations = {}
    for company in companies:
        ticker = company["ticker"]
        expectations[ticker] = []
        # Start from the fiscal period of the provided start_date
        current_fy, current_q = get_indian_fiscal_period(start_date)

        while True:
            # Determine the end date of the current fiscal quarter
            if current_q == 1: quarter_end_date = date(current_fy, 6, 30)
            elif current_q == 2: quarter_end_date = date(current_fy, 9, 30)
            elif current_q == 3: quarter_end_date = date(current_fy, 12, 31)
            else: quarter_end_date = date(current_fy + 1, 3, 31)

            # If the calculated quarter end is past our overall end_date, stop for this company.
            if quarter_end_date > end_date:
                break

            # Only add expectations that fall within the specified date range.
            if quarter_end_date >= start_date:
                expectations[ticker].append({
                    "fiscal_year": current_fy,
                    "quarter": current_q,
                    "fiscal_date": quarter_end_date
                })

            # Advance to the next quarter
            if current_q == 4:
                current_q = 1
                current_fy += 1
            else:
                current_q += 1
    return expectations


def seed_session() -> requests.Session:
    """Warms up a requests session to get necessary cookies from the NSE website."""
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session by visiting the UI page...")
        resp = sess.get(UI_URL, timeout=SESSION_TIMEOUT_SECONDS)
        resp.raise_for_status()
        time.sleep(POLITE_PAUSE_PER_REQUEST_SECONDS) # Wait after seeding
        logging.info("Session seeded successfully.")
        return sess
    except RequestException as e:
        logging.error(f"Fatal error: Failed to seed session: {e}")
        raise


def fetch_json_with_retry(session: requests.Session, url: str, params: dict) -> dict | None:
    """
    Attempts to fetch JSON data from a URL with multiple retries and exponential backoff.
    This is a critical function for handling intermittent network issues with the NSE server.
    """
    for attempt in range(API_MAX_RETRIES):
        try:
            # Add a polite pause before every single request to the details API.
            time.sleep(POLITE_PAUSE_PER_REQUEST_SECONDS)
            
            resp = session.get(url, params=params, timeout=30)
            logging.info(f"Requesting URL: {resp.url}") # Log the exact URL for debugging
            resp.raise_for_status()
            return resp.json()

        except (RequestException, json.JSONDecodeError) as e:
            logging.warning(f"Attempt {attempt + 1}/{API_MAX_RETRIES} failed for URL {url}: {e}")
            if attempt + 1 == API_MAX_RETRIES:
                logging.error(f"All {API_MAX_RETRIES} attempts failed for URL {url}.")
                return None # Return None after all retries are exhausted.
            
            delay = API_INITIAL_DELAY_SECONDS * (2 ** attempt)
            logging.info(f"Waiting for {delay} seconds before retrying...")
            time.sleep(delay)
    return None

# --- Main Ingestion Logic ---

def ingest_nse_data_for_universe(from_date_str: str, to_date_str: str):
    """
    Main orchestration function. Implements the "Expectation-Driven" ingestion workflow for the NSE Scraper.
    """
    logging.info(f">>> Starting Expectation-Driven NSE Scraper Ingestion v1.0 <<<")
    
    start_date = datetime.strptime(from_date_str, "%d-%m-%Y").date()
    end_date = datetime.strptime(to_date_str, "%d-%m-%Y").date()

    # 1. Generate a list of all filings we EXPECT to find.
    expected_filings_by_ticker = generate_expected_filings(start_date, end_date, COMPANIES)
    logging.info(f"Generated expectations for {len(expected_filings_by_ticker)} companies between {from_date_str} and {to_date_str}.")

    # 2. Seed a session and fetch the master list of ALL announcements in the date range.
    try:
        http_session = seed_session()
        # Fetching a broad list of all filings in one go is more efficient.
        master_list_params = {"index": "equities", "from_date": from_date_str, "to_date": to_date_str, "period": "Quarterly"}
        all_announcements = http_session.get(LISTING_API_URL, params=master_list_params, timeout=45).json()
        logging.info(f"Fetched {len(all_announcements)} total announcements from NSE master list.")
    except (RequestException, json.JSONDecodeError) as e:
        logging.critical(f"Could not establish session or fetch master list. Aborting. Error: {e}")
        return

    # 3. Create a lookup map for quick access to announcements.
    # We map (ticker, fiscal_date) to the announcement metadata.
    # We only consider consolidated, non-cumulative results for consistency.
    announcements_map = {
        (ann["symbol"], datetime.strptime(ann["toDate"], "%d-%b-%Y").date()): ann
        for ann in all_announcements
        if isinstance(ann, dict) and "symbol" in ann and "toDate" in ann
        and ann.get("consolidated") == "Consolidated"
        and ann.get("cumulative") == "Non-cumulative"
    }

    db_session = get_session()
    try:
        # 4. Iterate through our expectations and check against the fetched announcements.
        for ticker, expectations in expected_filings_by_ticker.items():
            logging.info(f"--- Processing expectations for {ticker} ---")
            for expectation in expectations:
                fiscal_date = expectation["fiscal_date"]
                log_entry = {
                    "ticker": ticker, "fiscal_year": expectation["fiscal_year"], "quarter": expectation["quarter"],
                    "source_type": SOURCE_TYPE, "checked_at": datetime.now(tz=ZoneInfo("Asia/Kolkata"))
                }

                found_announcement = announcements_map.get((ticker, fiscal_date))
                
                # If an announcement matches our expectation...
                if found_announcement:
                    logging.info(f"FOUND: Filing for {ticker} Q{expectation['quarter']} FY{expectation['fiscal_year']}.")
                    seq_id = found_announcement.get("seqNumber")

                    # Construct the complex 'params' string required by the details API
                    from_date_str_api = found_announcement.get("fromDate", "").replace("-","")
                    to_date_str_api = found_announcement.get("toDate", "").replace("-","")
                    qtr_api = found_announcement.get("relatingTo", "").replace(" Quarter","").replace("First","Q1").replace("Second","Q2").replace("Third","Q3").replace("Fourth","Q4")
                    audited_flag = "A" if found_announcement.get("audited") == "Audited" else "U"
                    cumulative_flag = "N" # We already filtered for Non-cumulative
                    consolidated_flag = "C" # We already filtered for Consolidated
                    ind_as_flag = "N" if "Ind-AS New" in found_announcement.get("indAs", "") else "O"
                    params_string = f"{from_date_str_api}{to_date_str_api}{qtr_api}{audited_flag}{cumulative_flag}{consolidated_flag}{ind_as_flag}{ticker}"
                    
                    details_params = {
                        "index": "equities", "seq_id": seq_id, "params": params_string,
                        "industry": "-", "frOldNewFlag": "N", "ind": "N", "format": "New"
                    }
                    
                    # Fetch the detailed JSON with retry logic
                    raw_json_content = fetch_json_with_retry(http_session, DETAILS_API_URL, details_params)

                    if raw_json_content:
                        log_entry["status"] = "FOUND"
                        
                        # Prepare data for `raw_sources` table, matching the schema
                        source_data = {
                            "ticker": ticker,
                            "fiscal_date": fiscal_date,
                            "source_type": SOURCE_TYPE,
                            "source_url": f"{DETAILS_API_URL}?{'&'.join([f'{k}={v}' for k, v in details_params.items()])}",
                            "raw_content": raw_json_content,
                            "local_path": None # This is an API source, so no local path
                        }
                        upsert_raw_source(source_data)
                        
                        # Get the newly created raw_source_id to link in the log
                        raw_source_obj = db_session.query(RawSource).filter_by(ticker=ticker, fiscal_date=fiscal_date, source_type=SOURCE_TYPE).first()
                        if raw_source_obj:
                            log_entry["raw_source_id"] = raw_source_obj.id
                        logging.info(f"Successfully stored raw JSON for {ticker} Q{expectation['quarter']} in raw_sources table.")
                    else:
                        # Log if fetching the details fails after all retries
                        log_entry["status"] = "FETCH_ERROR"
                        logging.error(f"FETCH_ERROR: Could not retrieve details for {ticker} Q{expectation['quarter']}.")

                else:
                    # Log if no announcement matches our expectation in the master list
                    logging.warning(f"MISSING: No filing found for {ticker} Q{expectation['quarter']} FY{expectation['fiscal_year']} at source.")
                    log_entry["status"] = "MISSING_AT_SOURCE"
                    log_entry["raw_source_id"] = None

                # Finally, upsert the log entry for this expectation.
                upsert_ingestion_log(log_entry)
            
            # Polite pause between processing each company
            logging.info(f"Pausing for {POLITE_PAUSE_PER_COMPANY_SECONDS}s before next company.")
            time.sleep(POLITE_PAUSE_PER_COMPANY_SECONDS)
    finally:
        db_session.close()
    
    logging.info(">>> Expectation-Driven NSE Scraper Ingestion Finished <<<")


if __name__ == "__main__":
    # Define the date range for the ingestion process.
    # Use a wide range for a backfill, or a smaller range for daily updates.
    SEARCH_START_DATE = "01-04-2024"
    SEARCH_END_DATE = "25-06-2025" # Today's date
    
    logging.info(f"Starting NSE Scraper ingestion for date range: {SEARCH_START_DATE} to {SEARCH_END_DATE}")
    ingest_nse_data_for_universe(from_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)
