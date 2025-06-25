# earnings_agent/sources/nse_api/scrape_nse.py

import requests
import logging
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# Using an explicit relative import for the decoder map
from .nse_decoder_map import NSE_DECODER_MAP

# --- Standard Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Configuration ---
BASE_URL = "https://www.nseindia.com"
UI_URL = BASE_URL + "/companies-listing/corporate-filings-financial-results"
LISTING_API_URL = BASE_URL + "/api/corporates-financial-results"
DETAILS_API_URL = BASE_URL + "/api/corporates-financial-results-data"

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": UI_URL,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
SESSION_TIMEOUT_SECONDS = 30
NSE_SCRAPER_VERSION = "1.0"

# --- Helper Functions ---

def seed_session() -> requests.Session:
    """Warms up a requests session to get necessary cookies from NSE."""
    try:
        sess = requests.Session()
        sess.headers.update(HEADERS)
        logging.info("Seeding new session by visiting the UI page...")
        sess.get(UI_URL, timeout=SESSION_TIMEOUT_SECONDS)
        time.sleep(2)
        logging.info("Session seeded successfully.")
        return sess
    except requests.RequestException as e:
        logging.error(f"Failed to seed session: {e}")
        raise

def to_numeric(value_str: str | None) -> float | None:
    if value_str is None or value_str == "": return None
    try:
        return float(str(value_str).replace(",", ""))
    except (ValueError, TypeError):
        return None

# --- Core Scraping Logic ---

def get_filing_sequence_ids(session: requests.Session, symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Step 1: Fetches the master list of all filings to get their unique sequence IDs and other metadata."""
    logging.info(f"Fetching filing list for {symbol} from {start_date} to {end_date}...")
    params = {"index": "equities", "symbol": symbol, "from_date": start_date, "to_date": end_date, "period": "Quarterly"}
    try:
        response = session.get(LISTING_API_URL, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        
        filings = []
        if isinstance(data, list):
            for item in data:
                # We only want to process consolidated, non-cumulative, quarterly results for consistency
                if item.get("cumulative") == "Non-cumulative" and item.get("consolidated") == "Consolidated":
                    filings.append(item) # Return the whole item, as it contains info for the params string
        logging.info(f"Found {len(filings)} relevant consolidated quarterly filings.")
        return filings
    except (requests.RequestException, json.JSONDecodeError) as e:
        logging.error(f"Could not fetch or parse filing list for {symbol}: {e}")
        return []

# --- MODIFIED: This function now builds the full, complex request to mimic the browser ---
def fetch_and_transform_details(session: requests.Session, symbol: str, filing_info: dict) -> dict | None:
    """Step 2: Fetches the detailed financial data for a single filing and transforms it."""
    seq_id = filing_info.get("seqNumber")
    fiscal_date = datetime.strptime(filing_info.get("toDate"), "%d-%b-%Y").date()
    
    logging.info(f"Fetching details for {symbol} (Seq ID: {seq_id}, Date: {fiscal_date})...")

    # --- NEW: Logic to construct the complex 'params' string ---
    # Example: 01-Oct-202431-Dec-2024Q3UNNCNERELIANCE
    from_date_str = filing_info.get("fromDate", "").replace("-","")
    to_date_str = filing_info.get("toDate", "").replace("-","")
    qtr = filing_info.get("relatingTo", "").replace(" Quarter","").replace("First","Q1").replace("Second","Q2").replace("Third","Q3").replace("Fourth","Q4")
    audited_flag = "A" if filing_info.get("audited") == "Audited" else "U"
    cumulative_flag = "C" if filing_info.get("cumulative") == "Cumulative" else "N"
    consolidated_flag = "C" if filing_info.get("consolidated") == "Consolidated" else "N"
    ind_as_flag = "O" if filing_info.get("indAs") == "Ind-AS Old" else "N"
    
    # This is a best guess at the format. May need slight tweaks.
    params_string = f"{from_date_str}{to_date_str}{qtr}{audited_flag}{cumulative_flag}{consolidated_flag}{ind_as_flag}{symbol}"

    params = {
        "index": "equities",
        "seq_id": seq_id,
        "params": params_string,
        "industry": "-",
        "frOldNewFlag": "N",
        "ind": "N",
        "format": "New"
    }
    
    try:
        time.sleep(3) # Respectful pause
        response = session.get(DETAILS_API_URL, params=params, timeout=20)
        logging.info(f"Requesting URL: {response.url}") # Log the exact URL for debugging
        response.raise_for_status()
        raw_data = response.json()
        
        # --- Transformation Logic (Unchanged) ---
        financials = raw_data.get("resultsData2", {})
        core_metrics = {}
        custom_kpis = {}

        for api_key, value in financials.items():
            if api_key in NSE_DECODER_MAP:
                standard_name = NSE_DECODER_MAP[api_key]
                if core_metrics.get(standard_name) is None:
                    core_metrics[standard_name] = to_numeric(value)
            else:
                custom_kpis[api_key] = value

        transformed_record = {
            "source_type": "NSE_API", "parser_version": NSE_SCRAPER_VERSION,
            "raw_nse_api_response": raw_data,
            "filing_metadata": {"ticker": symbol, "fiscal_date": fiscal_date.isoformat(), "source_url": response.url},
            "core_metrics": core_metrics, "custom_kpis": custom_kpis,
            "parsing_summary": {"status": "SUCCESS", "parser_version": NSE_SCRAPER_VERSION, "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()}
        }
        return transformed_record

    except (requests.RequestException, json.JSONDecodeError) as e:
        logging.error(f"Could not fetch or parse details for Seq ID {seq_id}: {e}")
        logging.info(f"Response text that failed: {response.text}") # Print the failed response text
        return None

# --- Main Execution Block (Unchanged) ---
if __name__ == '__main__':
    TEST_SYMBOL = "RELIANCE"
    START_DATE = "01-01-2023"
    END_DATE = "25-06-2025"
    
    logging.info("--- Starting NSE Scraper Test Run ---")
    
    try:
        session = seed_session()
        filings_to_process = get_filing_sequence_ids(session, TEST_SYMBOL, START_DATE, END_DATE)
        
        if filings_to_process:
            filings_to_process.sort(key=lambda x: datetime.strptime(x['filingDate'], "%d-%b-%Y %H:%M"), reverse=True)
            
            for filing in filings_to_process[:3]:
                transformed_data = fetch_and_transform_details(session, TEST_SYMBOL, filing)
                if transformed_data:
                    print("\n" + "="*80)
                    print(f"SUCCESSFULLY TRANSFORMED DATA FOR {TEST_SYMBOL} - {filing['toDate']}")
                    print("="*80)
                    print(json.dumps(transformed_data, indent=4))
                else:
                    print(f"\nFAILED to transform data for {TEST_SYMBOL} - {filing['toDate']}")
        else:
            logging.warning(f"No filings found for {TEST_SYMBOL} in the given date range.")

    except Exception as e:
        logging.critical(f"A critical error occurred during the test run: {e}", exc_info=True)
        
    logging.info("--- NSE Scraper Test Run Finished ---")
