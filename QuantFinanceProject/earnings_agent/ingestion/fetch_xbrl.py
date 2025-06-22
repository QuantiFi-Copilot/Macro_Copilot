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

# --- Configuration (from your working script) ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
JSON_ENDPOINT = BASE_URL + "/api/corporates-financial-results"
# Using the complete, proven set of headers from your original working script
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

# The professional-grade data directory at the project root
DATA_ROOT = Path(__file__).resolve().parent.parent/ "storage" / "data"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

def seed_session() -> requests.Session:
    """Warms up a session by visiting the UI page to get necessary cookies."""
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        logging.info("Seeding new session...")
        resp = sess.get(UI_URL, timeout=15)
        resp.raise_for_status()
        time.sleep(2)  # Allow cookies to settle
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
            
            # Handle authentication/permission errors by reseeding the session
            if r.status_code in (401, 403):
                logging.warning(f"Got status {r.status_code}, reseeding session...")
                sess = seed_session()
                continue
            
            # For other HTTP errors, raise the exception
            r.raise_for_status()

            # Check for a successful response and valid JSON
            if r.status_code == 200:
                response_data = r.json()
                
                # --- START OF CORRECTION ---
                # The API now wraps the results in a dictionary under the 'data' key.
                if isinstance(response_data, dict) and 'data' in response_data:
                    # Successfully found the list of announcements.
                    return response_data.get('data', [])
                # --- END OF CORRECTION ---
                
                # This handles the old case where it might have been a direct list.
                elif isinstance(response_data, list):
                     logging.info("API returned a direct list (old format). Processing as is.")
                     return response_data
                else:
                    # Log the unexpected structure and return empty.
                    logging.warning(f"API returned unexpected JSON structure: {response_data}")
                    return []

        except (requests.RequestException, json.JSONDecodeError) as e:
            logging.error(f"Failed to fetch or parse JSON on attempt {attempt}: {e}")
            if attempt >= MAX_RETRIES:
                raise HTTPError(f"Failed to fetch JSON after all retries: {JSON_ENDPOINT}") from e
            time.sleep(RETRY_DELAY_SECONDS * attempt)
    
    return [] # Return empty list if all retries fail


def ingest_xbrl_for_universe(from_date: str, to_date: str):
    """The main ingestion workflow, corrected and finalized."""
    logging.info(">>> Starting batch XBRL ingestion process <<<")
    try:
        sess = seed_session()
        all_announcements = fetch_financial_results(sess, from_date, to_date)
    except Exception as e:
        logging.critical(f"Could not establish session or fetch master list. Aborting. Error: {e}")
        return

    # If the list is empty after a successful fetch, there's nothing to do.
    if not all_announcements:
        logging.warning("Master list of filings was empty. No data to process for the given date range.")
        logging.info(">>> Batch ingestion process finished. <<<")
        return

    # Group announcements by symbol for efficient lookup
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
                fiscal_date = datetime.strptime(to_dt_str, "%d-%b-%Y").date()
                quarter = (fiscal_date.month - 1) // 3 + 1
            except ValueError:
                logging.warning(f"Skipping filing for {ticker} due to unparsable date: '{to_dt_str}'")
                continue

            quarter_key = (fiscal_date.year, quarter)
            if quarter_key in processed_quarters:
                continue
            processed_quarters.add(quarter_key)

            company_dir = DATA_ROOT / "raw" / "xbrl" / ticker
            company_dir.mkdir(parents=True, exist_ok=True)
            file_name = f"{ticker}_Q{quarter}_{fiscal_date.year}.xml"
            out_path = company_dir / file_name

            if out_path.exists():
                logging.info(f"Skipping (already exists): {out_path.name}")
                # Even if it exists, ensure the metadata is in the DB for resilience
                full_xml_url = BASE_URL + xml_url_path if not xml_url_path.startswith('http') else xml_url_path
                document_data = {
                    "ticker": ticker,
                    "fiscal_date": fiscal_date,
                    "doc_type": "XBRL_INSTANCE",
                    "source_url": full_xml_url,
                    "local_path": str(out_path.resolve()),
                }
                upsert_raw_document(document_data)
                continue

            full_xml_url = BASE_URL + xml_url_path if not xml_url_path.startswith('http') else xml_url_path

            try:
                resp = sess.get(full_xml_url, timeout=30)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
                logging.info(f"Successfully downloaded: {out_path.name}")

                # This is the Data Lineage step
                document_data = {
                    "ticker": ticker,
                    "fiscal_date": fiscal_date,
                    "doc_type": "XBRL_INSTANCE",
                    "source_url": full_xml_url,
                    "local_path": str(out_path.resolve()),
                }
                upsert_raw_document(document_data)

            except Exception as e:
                logging.error(f"Failed to download or log {ticker} {file_name}: {e}")

        time.sleep(3) # Be respectful to the server

    logging.info(">>> Batch ingestion process finished. <<<")

if __name__ == "__main__":
    # CORRECTED: Use the DD-MM-YYYY format required by the API.
    # We are searching the period covering the results for the quarter ending March 2025.
    SEARCH_START_DATE = "01-04-2022"
    SEARCH_END_DATE = "22-06-2025"
    
    logging.info(f"Starting ingestion for date range: {SEARCH_START_DATE} to {SEARCH_END_DATE}")
    ingest_xbrl_for_universe(from_date=SEARCH_START_DATE, to_date=SEARCH_END_DATE)