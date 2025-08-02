# earnings_agent/ingestion/bse/ingestion_task.py

import logging
import os
import random
import time
import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session
from urllib.parse import urljoin

# Import project-specific database functions, models, and configurations
from earnings_agent.storage.database import (
    create_ingestion_jobs,
    get_jobs_by_status,
    log_ingestion_failure,
    log_ingestion_success,
    get_session,
    get_asset_by_hash,
)
from earnings_agent.storage.models import CompanyMaster

# ==============================================================================
# 1. SETUP & CONFIGURATION
# ==============================================================================

# --- Constants ---
SOURCE_TYPE = "BSE_XLS"
INGESTION_SCRIPT_VERSION = "bse-ingestor-v1.0"
DATA_ROOT = Path(__file__).resolve().parents[2] / "storage" / "data"
BASE_URL = "https://www.bseindia.com/corporates/"
SESSION_TIMEOUT_SECONDS = 30
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_INITIAL_DELAY_SECONDS = 5
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
]

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    handlers=[
        logging.FileHandler("bse_ingestion.log"),
        logging.StreamHandler()
    ]
)

# --- Session ---
session = requests.Session()

# ==============================================================================
# 2. DATABASE & DOMAIN HELPERS
# ==============================================================================

def get_companies_from_master() -> List[CompanyMaster]:
    """
    Fetches the list of companies to process directly from the company_master table.
    Filters for listed companies that have a BSE code.
    """
    db_session: Session = get_session()
    try:
        companies = db_session.query(CompanyMaster).filter(
            CompanyMaster.bse_code.isnot(None),
            CompanyMaster.listing_status == 'LISTED'
        ).all()
        logging.info(f"Found {len(companies)} companies with BSE codes in the master table.")
        return companies
    finally:
        db_session.close()

def get_period_key_from_job(job: 'IngestionJob') -> str:
    """Generates the BSE period key (e.g., 'Mar-25') from a job object."""
    # Q1 -> Jun, Q2 -> Sep, Q3 -> Dec, Q4 -> Mar
    month_map = {1: "Jun", 2: "Sep", 3: "Dec", 4: "Mar"}
    # For Q4 (Mar), the calendar year is the fiscal year + 1
    year_offset = 1 if job.quarter == 4 else 0
    year_short = str(job.fiscal_year + year_offset)[-2:]
    return f"{month_map[job.quarter]}-{year_short}"

def get_file_hash(file_path: Path) -> str:
    """Calculates the SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

# ==============================================================================
# 3. CORE SCRAPING & DOWNLOAD LOGIC (Retained & Adapted)
# ==============================================================================

def make_request_with_retry(url: str, method: str = 'get', **kwargs) -> Optional[requests.Response]:
    """A robust request function with exponential backoff and rotating user-agents."""
    headers = kwargs.get('headers', {})
    if 'User-Agent' not in headers:
        headers['User-Agent'] = random.choice(USER_AGENTS)
    kwargs['headers'] = headers

    for i in range(DOWNLOAD_MAX_RETRIES):
        try:
            if method.lower() == 'post':
                response = session.post(url, timeout=SESSION_TIMEOUT_SECONDS, **kwargs)
            else:
                response = session.get(url, timeout=SESSION_TIMEOUT_SECONDS, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            wait_time = DOWNLOAD_INITIAL_DELAY_SECONDS * (2 ** i)
            logging.warning(f"Request failed: {e}. Retrying in {wait_time:.1f} seconds...")
            time.sleep(wait_time)
    logging.error(f"Request for {url} failed after {DOWNLOAD_MAX_RETRIES} retries.")
    return None

def extract_form_data(soup: BeautifulSoup) -> Dict[str, str]:
    """Extracts hidden input values from a form."""
    form_data = {}
    for input_tag in soup.find_all("input", type="hidden"):
        name = input_tag.get("name")
        if name:
            form_data[name] = input_tag.get("value", "")
    return form_data

def get_qtrs_from_soup(soup: BeautifulSoup, existing_links: Dict) -> Dict[str, str]:
    """Helper to extract new quarter links from a BeautifulSoup object."""
    new_links = {}
    links = soup.find_all('a', class_='tablebluelink')
    for link in links:
        text = link.text.strip()
        href = link.get('href')
        parts = text.split('-')
        if len(parts) < 3:
            continue
        # Key format is like "Jun-25"
        period_key = f"{parts[-2]}-{parts[-1]}"
        if href and 'results.aspx' in href and period_key not in existing_links and period_key not in new_links:
            full_url = urljoin(BASE_URL, href)
            new_links[period_key] = full_url
    return new_links

def discover_qtr_ids(company_code: str) -> Dict[str, str]:
    """Scrapes all historical quarter links for a single company to create a master list."""
    all_quarterly_links = {}
    logging.info(f"Discovering all available quarter links for BSE Code: {company_code}")

    base_results_url = f"https://www.bseindia.com/corporates/comp_results.aspx?Code={company_code}"
    # Initial request has no referer
    response = make_request_with_retry(base_results_url)
    if not response:
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    initial_links = get_qtrs_from_soup(soup, {})
    all_quarterly_links.update(initial_links)

    # The first "referer" is the base page itself
    referer_url = base_results_url
    pid = 1
    while True:
        paginated_url = f"{base_results_url}&PID={pid}"
        logging.info(f"Fetching discovery page {pid} for {company_code}...")

        # Add the Referer header to the request
        headers = {'Referer': referer_url}
        response = make_request_with_retry(paginated_url, headers=headers)

        if not response:
            # This can happen on a 404 after retries, which is the expected end of pagination
            logging.info(f"Reached end of pages or failed to fetch page {pid}. Discovery for {company_code} finished.")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        new_links = get_qtrs_from_soup(soup, all_quarterly_links)
        if not new_links:
            logging.info(f"No new unique links found on page {pid}. Discovery for {company_code} finished.")
            break

        all_quarterly_links.update(new_links)

        # Update the referer for the *next* loop iteration
        referer_url = paginated_url
        pid += 1
        time.sleep(random.uniform(0.5, 1.5))

    logging.info(f"Discovered {len(all_quarterly_links)} total links for BSE Code {company_code}.")
    return all_quarterly_links

# ==============================================================================
# 4. MAIN INGESTION LOGIC
# ==============================================================================

def ingest_all_bse(start_date_str: str, to_date_str: str):
    """
    Main function to drive the BSE ingestion process following the project's
    expectation-driven architecture.
    """
    logging.info(f">>> Starting BSE XLS Ingestion v{INGESTION_SCRIPT_VERSION} <<<")
    
    # --- 1. JOB CREATION (Setting Expectations) ---
    start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    end_date = datetime.strptime(to_date_str, "%d-%m-%Y").date()
    
    companies = get_companies_from_master()
    if not companies:
        logging.warning("No companies found in master table. Exiting.")
        return

    jobs_to_create: List[Dict] = []
    consolidation_types = ["Consolidated", "Standalone"]
    
    for company in companies:
        # Start from the beginning of the fiscal year of the provided start_date
        current_fy = start_date.year if start_date.month >= 4 else start_date.year - 1
        current_q = 1

        while True:
            # Calculate the end date of the current quarter
            q_end_month_day = ((6, 30), (9, 30), (12, 31), (3, 31))[current_q - 1]
            q_end_year = current_fy if current_q != 4 else current_fy + 1
            q_end_date = date(q_end_year, q_end_month_day[0], q_end_month_day[1])

            if q_end_date > end_date:
                break # Stop if we have passed the target end date

            if q_end_date >= start_date:
                 for conso_type in consolidation_types:
                    jobs_to_create.append({
                        "ticker": company.ticker,
                        "fiscal_year": current_fy,
                        "quarter": current_q,
                        "source_type": SOURCE_TYPE,
                        "consolidation_status": conso_type,
                        "ingestion_script_version": INGESTION_SCRIPT_VERSION
                    })

            # Move to the next quarter
            current_q, current_fy = (current_q + 1, current_fy) if current_q < 4 else (1, current_fy + 1)

    if jobs_to_create:
        create_ingestion_jobs(jobs_data=jobs_to_create)
        logging.info(f"Created/verified {len(jobs_to_create)} ingestion jobs in the database.")

    # --- 2. JOB PROCESSING (Fulfilling Expectations) ---
    jobs_to_process = get_jobs_by_status(
        statuses=['PENDING', 'FETCH_FAILED', 'MISSING_AT_SOURCE'],
        script_version=INGESTION_SCRIPT_VERSION
    )
    if not jobs_to_process:
        logging.info("No re-triable BSE jobs to process. Exiting.")
        return
        
    logging.info(f"Found {len(jobs_to_process)} BSE jobs to process.")

    # Group jobs by company to minimize discovery calls
    jobs_by_bse_code = defaultdict(list)
    company_map = {c.ticker: c for c in companies}
    for job in jobs_to_process:
        if job.ticker in company_map:
            jobs_by_bse_code[company_map[job.ticker].bse_code].append(job)

    # Process jobs company by company
    for bse_code, jobs in jobs_by_bse_code.items():
        logging.info(f"--- Processing {len(jobs)} jobs for BSE Code: {bse_code} ---")
        
        # Discover all available links for the company once
        available_links = discover_qtr_ids(bse_code)
        if not available_links:
            logging.warning(f"Could not discover any links for BSE code {bse_code}. Failing jobs.")
            for job in jobs:
                log_ingestion_failure(job.job_id, 'FETCH_FAILED', f"Could not discover any financial result pages for BSE Code {bse_code}.")
            continue

        for job in jobs:
            period_key = get_period_key_from_job(job)
            results_url = available_links.get(period_key)

            if not results_url:
                log_ingestion_failure(job.job_id, 'MISSING_AT_SOURCE', f"Filing for period '{period_key}' not found on BSE for {job.ticker}.")
                continue
            
            logging.info(f"Found URL for Job {job.job_id} ({job.ticker} {period_key}): {results_url}")

            # Define save path
            company_dir = DATA_ROOT / "raw" / "bse" / job.ticker
            company_dir.mkdir(parents=True, exist_ok=True)
            file_name = f"FY{job.fiscal_year}_Q{job.quarter}_{job.consolidation_status}.xls"
            filepath = company_dir / file_name

            # Target view to download based on the job
            target_view = job.consolidation_status # "Consolidated" or "Standalone"

            try:
                # 1. Get initial page
                initial_response = make_request_with_retry(results_url)
                if not initial_response:
                    log_ingestion_failure(job.job_id, 'FETCH_FAILED', f"Could not fetch initial results page: {results_url}")
                    continue

                soup_initial = BeautifulSoup(initial_response.text, "html.parser")
                soup_for_download = soup_initial # By default, we download from the initial page

                # 2. Check if the page has navigation, or if it's a direct data page
                view_span = soup_initial.find('span', id='ContentPlaceHolder1_lblresultype')

                if view_span:
                    # This is a full landing page with navigation options
                    logging.info("Detected a results landing page. Checking view type...")
                    current_view_name = view_span.text.strip().replace(' Results', '')
                    target_view = job.consolidation_status

                    if current_view_name != target_view:
                        # We are on the wrong view, so we must navigate
                        view_event_targets = {
                            "Consolidated": 'ctl00$ContentPlaceHolder1$lnkConsolidated',
                            "Standalone": 'ctl00$ContentPlaceHolder1$lnkDetailed',
                        }
                        target_event = view_event_targets.get(target_view)
                        link_id = target_event.replace('ctl00$ContentPlaceHolder1$', 'ContentPlaceHolder1_') if target_event else None

                        if target_event and soup_initial.find('a', id=link_id):
                            logging.info(f"Currently on '{current_view_name}', navigating to '{target_view}' view...")
                            form_data_view = extract_form_data(soup_initial)
                            form_data_view['__EVENTTARGET'] = target_event
                            resp_view = make_request_with_retry(results_url, method='post', data=form_data_view)
                            if resp_view:
                                soup_for_download = BeautifulSoup(resp_view.text, "html.parser")
                            else:
                                raise ConnectionError(f"Failed POST request to navigate to '{target_view}' view.")
                        else:
                            raise FileNotFoundError(f"Landed on '{current_view_name}', but the link to the target view '{target_view}' was not found.")
                else:
                    # This is a direct link to a data page with no navigation
                    logging.info("Detected a direct data page. Skipping navigation.")
                # 3. XBRL-style integrity check & download
                full_url = results_url  # or wherever you post to
                # --- Check for existing file and compare ---
                target_path = filepath
                is_verification = filepath.exists()
                if is_verification:
                    logging.info(f"Local file exists for Job {job.job_id}. Verifying content against source.")
                    target_path = filepath.with_suffix('.tmp')
                    local_hash = get_file_hash(filepath)
                form_data_dl = extract_form_data(soup_for_download)
                form_data_dl['__EVENTTARGET'] = 'ctl00$ContentPlaceHolder1$lnkDownload'
                dl_response = make_request_with_retry(results_url, method='post', data=form_data_dl)
                if not (dl_response and "application/vnd.ms-excel" in dl_response.headers.get("Content-Type", "")):
                    raise ConnectionError("Download request did not return an Excel file.")
                
                with open(target_path, "wb") as f:
                    f.write(dl_response.content)

                new_hash = get_file_hash(target_path)
                if is_verification:
                    if new_hash != local_hash:
                        logging.warning("CONTENT HAS CHANGED. Replacing local file and logging new asset.")
                        filepath.unlink()
                        target_path.rename(filepath)
                        # Log success with the new hash
                        log_ingestion_success(
                            job_id=job.job_id,
                            raw_data_hash=new_hash,
                            source_type=SOURCE_TYPE,
                            storage_location=str(filepath.resolve())
                        )
                    else:
                        logging.info("Content is identical (hash matches). Discarding temp download.")
                        target_path.unlink() # Delete the .tmp file
                        # Log success with the original hash
                        log_ingestion_success(job.job_id, local_hash, SOURCE_TYPE)
                else: 
                    logging.info(f"SUCCESS: Saved {filepath} with hash {new_hash}")
                    log_ingestion_success(
                        job_id=job.job_id,
                        raw_data_hash=new_hash,
                        source_type=SOURCE_TYPE,
                        storage_location=str(filepath.resolve())
                    )
            except (ConnectionError, FileNotFoundError, Exception) as e:
                logging.error(f"FAILED processing Job {job.job_id} for {job.ticker} {period_key}. Reason: {e}")
                log_ingestion_failure(job.job_id, 'FETCH_FAILED', str(e))
            
            time.sleep(random.uniform(2.0, 4.0)) # Be respectful with delays

    logging.info(">>> BSE XLS Ingestion process finished. <<<")

# ==============================================================================
# 5. SCRIPT EXECUTION
# ==============================================================================
if __name__ == '__main__':
    # Define the date range for which to create and process ingestion jobs.
    # Spanning multiple fiscal years to ensure robust testing.
    SEARCH_START_DATE = "01-04-2023"
    SEARCH_END_DATE = "31-07-2025" # Today's date is ~Aug 1, 2025
    ingest_all_bse(start_date_str=SEARCH_START_DATE, to_date_str=SEARCH_END_DATE)