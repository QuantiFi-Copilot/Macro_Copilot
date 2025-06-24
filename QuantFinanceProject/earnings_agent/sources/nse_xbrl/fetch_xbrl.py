# earnings_agent/ingestion/fetch_xbrl.py

import time
import requests
import logging
import json
from pathlib import Path
from datetime import datetime
from requests.exceptions import HTTPError

# Using your database function and universe config
from earnings_agent.storage.database import upsert_raw_document
from earnings_agent.config.universe import COMPANIES

# --- Production-Grade Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Configuration ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
JSON_ENDPOINT = BASE_URL + "/api/corporates-financial-results"

# Implemented "Lowest Possible Risk" Protocol
# Using a transparent User-Agent instead of mimicking a browser.
# Please replace with your actual contact info for professional courtesy.
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

DATA_ROOT = Path(__file__).resolve().parent.parent / "storage" / "data"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
# --- NEW: Increased timeout for session seeding to prevent read timeouts ---
SESSION_TIMEOUT_SECONDS = 30


def get_indian_fiscal_period(report_end_date: datetime.date) -> tuple[int, int]:
    """Calculates the Indian financial year and quarter from a report's end date."""
    month = report_end_date.month
    year = report_end_date.year
    fiscal_year = year - 1 if month < 4 else year
    if month in (4, 5, 6): fiscal_quarter = 1
    elif month in (7, 8, 9): fiscal_quarter = 2
    elif month in (10, 11, 12): fiscal_quarter = 3
    else: fiscal_quarter = 4
    return fiscal_year, fiscal_quarter

def seed_session() -> requests.Session:
    """Warms up a session by visiting the UI page to get necessary cookies."""
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session...")
        # --- MODIFICATION: Using the new, longer timeout constant ---
        resp = sess.get(UI_URL, timeout=SESSION_TIMEOUT_SECONDS)
        resp.raise_for_status()
        time.sleep(2)
        return sess
    except Exception as e:
        logging.error(f"Failed to seed session: {e}")
        raise

def fetch_financial_results(sess: requests.Session, from_date: str, to_date: str) -> list[dict]:
    """Fetches the master list of announcements, retrying with a new session if needed."""
    params = {"index": "equities", "from_date": from_date, "to_date": to_date, "period": "Quarterly"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"Fetching master list of filings... (Attempt {attempt})")
            r = sess.get(JSON_ENDPOINT, params=params, timeout=20)
            if r.status_code in (401, 403):
                logging.warning(f"Got status {r.status_code}, reseeding session...")
                sess = seed_session()
                continue
            r.raise_for_status()
            if r.status_code == 200:
                response_data = r.json()
                if isinstance(response_data, dict) and 'data' in response_data:
                    return response_data.get('data', [])
                elif isinstance(response_data, list):
                     logging.info("API returned a direct list (old format). Processing as is.")
                     return response_data
                else:
                    logging.warning(f"API returned unexpected JSON structure: {response_data}")
                    return []
        except (requests.RequestException, json.JSONDecodeError) as e:
            logging.error(f"Failed to fetch or parse JSON on attempt {attempt}: {e}")
            if attempt >= MAX_RETRIES:
                raise HTTPError(f"Failed to fetch JSON after all retries: {JSON_ENDPOINT}") from e
            time.sleep(RETRY_DELAY_SECONDS * attempt)
    return []

def ingest_xbrl_for_universe(from_date: str, to_date: str):
    """The main ingestion workflow, updated with the Lowest Possible Risk protocol."""
    logging.info(">>> Starting batch XBRL ingestion process (Risk Protocol v1.1) <<<")
    try:
        sess = seed_session()
        all_announcements = fetch_financial_results(sess, from_date, to_date)
    except Exception as e:
        logging.critical(f"Could not establish session or fetch master list. Aborting. Error: {e}")
        return

    if not all_announcements:
        logging.warning("Master list of filings was empty. No data to process for the given date range.")
        logging.info(">>> Batch ingestion process finished. <<<")
        return

    ann_by_ticker: dict[str, list[dict]] = {}
    for ann in all_announcements:
        if isinstance(ann, dict) and "symbol" in ann:
             ann_by_ticker.setdefault(ann["symbol"], []).append(ann)

    for company in COMPANIES:
        ticker = company["ticker"]
        logging.info(f"--- Processing {ticker} ---")
        company_announcements = ann_by_ticker.get(ticker, [])
        if not company_announcements:
            logging.warning(f"No filings found for {ticker} in API response.")
            continue

        processed_quarters = set()
        for ann in company_announcements:
            xml_url_path = ann.get("xbrl")
            to_dt_str = ann.get("toDate")
            if not (xml_url_path and to_dt_str and not xml_url_path.strip().endswith("/-")):
                continue
            try:
                report_end_date = datetime.strptime(to_dt_str, "%d-%b-%Y").date()
                fiscal_year, quarter = get_indian_fiscal_period(report_end_date)
            except ValueError:
                logging.warning(f"Skipping filing for {ticker} due to unparsable date: '{to_dt_str}'")
                continue

            quarter_key = (fiscal_year, quarter)
            if quarter_key in processed_quarters:
                continue
            processed_quarters.add(quarter_key)

            company_dir = DATA_ROOT / "raw" / "xbrl" / ticker
            company_dir.mkdir(parents=True, exist_ok=True)
            file_name = f"{ticker}_FY{fiscal_year}_Q{quarter}.xml"
            out_path = company_dir / file_name

            if out_path.exists():
                logging.info(f"Skipping (already exists): {out_path.name}")
                full_xml_url = BASE_URL + xml_url_path if not xml_url_path.startswith('http') else xml_url_path
                document_data = {
                    "ticker": ticker, "fiscal_date": report_end_date, "doc_type": "XBRL_INSTANCE",
                    "source_url": full_xml_url, "local_path": str(out_path.resolve()),
                }
                upsert_raw_document(document_data)
                continue

            full_xml_url = BASE_URL + xml_url_path if not xml_url_path.startswith('http') else xml_url_path

            try:
                resp = sess.get(full_xml_url, timeout=30)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
                logging.info(f"Successfully downloaded: {out_path.name}")
                document_data = {
                    "ticker": ticker, "fiscal_date": report_end_date, "doc_type": "XBRL_INSTANCE",
                    "source_url": full_xml_url, "local_path": str(out_path.resolve()),
                }
                upsert_raw_document(document_data)
                
                # Granular rate-limiting after each successful download
                logging.info("Pausing for 2 seconds to respect server limits...")
                time.sleep(2)

            except Exception as e:
                logging.error(f"Failed to download or log {ticker} {file_name}: {e}")

        # This outer sleep is still good for spacing out the master list fetches between companies.
        logging.info(f"Finished processing {ticker}. Pausing before next company...")
        time.sleep(3) 

    logging.info(">>> Batch ingestion process finished. <<<")

if __name__ == "__main__":
    # Expanded date range slightly for testing as requested
    SEARCH_START_DATE = "01-01-2022"
    SEARCH_END_DATE = "24-06-2025"
    
    logging.info(f"Starting ingestion for date range: {SEARCH_START_DATE} to {SEARCH_END_DATE}")
    ingest_xbrl_for_universe(from_date=SEARCH_START_DATE, to_date=SEARCH_END_DATE)