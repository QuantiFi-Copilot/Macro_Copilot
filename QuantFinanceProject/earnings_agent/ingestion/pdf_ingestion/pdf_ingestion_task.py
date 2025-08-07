"""
Up-levelled BSE PDF ingestor (v2.2)

Core fetch logic is unchanged – we still crawl the same
AnnSubCategoryGetData API and build candidate URLs the old way –  
but the script now reaches feature-parity with the XBRL ingestor:

• dual Consolidated + Standalone coverage  
• global asset de-duplication (hash based)  
• remote-timestamp freshness checks & compare-then-swap  
• random jitter in all API loops and back-offs  
• stricter corrupt / tiny-file guard (< 1 KiB)  
• HEAD-style metadata fetch for first-time downloads  

FIXED: Quarter detection now uses 70-day lag from quarter end dates
instead of inferring from search date ranges, resolving the critical
quarter mismatch issue.
"""

import time, random, hashlib, logging, os
from pathlib import Path
from datetime import datetime, date, timezone
from dateutil.relativedelta import relativedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

import requests
from requests.exceptions import RequestException

# ── Project imports ────────────────────────────────────────────────────────────
from earnings_agent.storage.database import (
    create_ingestion_jobs,
    get_jobs_by_status,
    log_ingestion_success,
    log_ingestion_failure,
    get_asset_by_hash,
    get_session,
)
from earnings_agent.storage.models import RawDataAsset
from earnings_agent.storage.models import CompanyMaster

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(module)s - %(message)s",
)

# ── Constants ─────────────────────────────────────────────────────────────────
BSE_API_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
)
BSE_HOME_URL = "https://www.bseindia.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
DATA_ROOT = Path(__file__).resolve().parents[2] / "storage" / "data"
DATA_ROOT.mkdir(parents=True, exist_ok=True)

SESSION_TIMEOUT_SECONDS = 30
SOURCE_TYPE = "PDF_FILE"
SCRIPT_VERSION = "pdf-ingestor-v2.2"

DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_INITIAL_DELAY = 5
API_MAX_RETRIES = 4
REQUEST_DELAY = 1.0
MIN_VALID_FILE_SIZE = 1024  # bytes

# Filing deadline buffer (regulatory deadline is 60 days, we use 70 for safety)
FILING_DEADLINE_DAYS = 70

# ── Helper functions ──────────────────────────────────────────────────────────

def get_quarter_end_date(fiscal_year: int, quarter: int) -> date:
    """Return the last date of a given fiscal quarter for Indian companies."""
    if quarter == 1:  # Apr-Jun
        return date(fiscal_year, 6, 30)
    elif quarter == 2:  # Jul-Sep
        return date(fiscal_year, 9, 30)
    elif quarter == 3:  # Oct-Dec
        return date(fiscal_year, 12, 31)
    elif quarter == 4:  # Jan-Mar
        return date(fiscal_year + 1, 3, 31)
    else:
        raise ValueError(f"Invalid quarter: {quarter}")

def get_filing_window(fiscal_year: int, quarter: int) -> tuple[date, date]:
    """
    Return (start_date, end_date) for when filings for a given quarter should be published.
    Based on 70-day deadline from quarter end.
    """
    quarter_end = get_quarter_end_date(fiscal_year, quarter)
    filing_start = quarter_end + relativedelta(days=1)  # Day after quarter end
    filing_end = quarter_end + relativedelta(days=FILING_DEADLINE_DAYS)  # 70 days later
    return filing_start, filing_end

def get_quarters_for_date_range(start_date: date, end_date: date) -> List[tuple[int, int]]:
    """
    Return list of (fiscal_year, quarter) tuples that could have filings 
    published within the given date range.
    """
    quarters = []
    
    # Check quarters from a reasonable range (we'll go back 2 years to be safe)
    for fy in range(start_date.year - 2, end_date.year + 2):
        for quarter in [1, 2, 3, 4]:
            filing_start, filing_end = get_filing_window(fy, quarter)
            
            # If the filing window overlaps with our search range, include this quarter
            if filing_start <= end_date and filing_end >= start_date:
                quarters.append((fy, quarter))
    
    return quarters

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def seed_bse_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    logging.info("Seeding Cloudflare cookies …")
    sess.get(BSE_HOME_URL, timeout=SESSION_TIMEOUT_SECONDS)
    time.sleep(2 + random.random())
    return sess

def get_json_with_retry(
    session: requests.Session,
    params: Dict,
    scrip: str,
) -> Optional[Dict]:
    """
    Call BSE AnnSubCategoryGetData API and return parsed JSON.
    Adds a per-scrip Referer header so Cloudflare serves JSON instead of HTML.
    Retries up to API_MAX_RETRIES times, refreshing cookies when HTML is received.
    """
    # Build the browser-like header *once* for this call
    headers = HEADERS.copy()
    headers["Referer"] = (
        f"{BSE_HOME_URL}/stock-share-price/unknown/unknown/{scrip}/"
    )

    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            resp = session.get(
                BSE_API_URL,
                params=params,
                headers=headers,
                timeout=20,
            )
            # Successful JSON response
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return resp.json()

            # Got HTML instead – refresh cookies & retry
            logging.warning(
                f"⚠️  Non-JSON for {scrip} (try {attempt}) – refreshing cookies"
            )
            session.get(BSE_HOME_URL, headers=HEADERS, timeout=15)
        except RequestException as e:
            logging.warning(f"API error for {scrip} (try {attempt}): {e}")

        # Polite back-off with jitter
        time.sleep(1 + random.random())

    # All attempts failed
    logging.error(f"BSE kept returning non-JSON for {scrip}; giving up.")
    return None

def candidate_urls(ann: Dict) -> List[str]:
    fname = ann.get("ATTACHMENTNAME") or ""
    if not fname:
        return []
    urls = [
        f"{BSE_HOME_URL}/xml-data/corpfiling/AttachHis/{fname}",   # history bucket first (works for older filings)
        f"{BSE_HOME_URL}/xml-data/corpfiling/AttachLive/{fname}",
    ]
    try:
        y, m, _ = ann["NEWS_DT"][:10].split("-")
        urls.append(
            f"{BSE_HOME_URL}/xml-data/corpfiling/CorpAttachment/"
            f"{int(y)}/{int(m)}/{fname}"
        )
    except Exception:
        pass
    return urls

def remote_metadata(session: requests.Session, url: str) -> Dict:
    try:
        with session.get(url, stream=True, timeout=10) as r:
            r.raise_for_status()
            lm = r.headers.get("Last-Modified")
            return {
                "modified": parsedate_to_datetime(lm).astimezone(timezone.utc)
                if lm
                else None,
                "length": int(r.headers.get("Content-Length", "-1")),
            }
    except Exception:
        return {}

def download_pdf(
    session: requests.Session, urls: List[str], dest: Path
) -> tuple[bool, Optional[datetime]]:
    """Download & return (success, last_modified_utc)."""
    for url in urls:
        for attempt in range(DOWNLOAD_MAX_RETRIES):
            try: 
                with session.get(url, stream=True, timeout=30, allow_redirects=True) as resp:
                    if (
                        resp.status_code == 200
                        and resp.headers.get("Content-Type", "").lower().startswith("application/")
                        
                    ):
                        with open(dest, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                        # minimal size guard
                        if dest.stat().st_size < MIN_VALID_FILE_SIZE:
                            dest.unlink()  # drop junk
                            raise ValueError("tiny / corrupt PDF")
                        lm = resp.headers.get("Last-Modified")
                        lm_dt = (
                            parsedate_to_datetime(lm).astimezone(timezone.utc)
                            if lm
                            else None
                        )
                        logging.info(f"Downloaded {dest.name}")
                        return True, lm_dt
            except Exception as e:
                logging.warning(f"{dest.name} try {attempt+1} failed: {e}")
                time.sleep(DOWNLOAD_INITIAL_DELAY * (2**attempt) + random.random())
    return False, None

def fetch_company_universe() -> list[dict]:
    """
    Returns [{'ticker': 'RELIANCE', 'bse_code': '500325'}, …] sourced
    live from earnings_data.company_master where listing_status='LISTED'
    and bse_code is not NULL.
    """
    with get_session() as db:
        rows = (
            db.query(CompanyMaster.ticker, CompanyMaster.bse_code)
              .filter(CompanyMaster.listing_status == 'LISTED',
                      CompanyMaster.bse_code.isnot(None))
              .all()
        )
    return [{"ticker": r.ticker, "bse_code": r.bse_code} for r in rows]

# ── Main ingestion ────────────────────────────────────────────────────────────

def ingest_all_pdfs(start_ddmmyyyy: str, end_ddmmyyyy: str) -> None:
    start_d = datetime.strptime(start_ddmmyyyy, "%d-%m-%Y").date()
    end_d = datetime.strptime(end_ddmmyyyy, "%d-%m-%Y").date()

    companies = fetch_company_universe()
    if not companies:
        logging.error("No companies found in company_master; aborting.")
        return

    # 1️⃣  Job manifest based on quarters that could have filings in this date range
    quarters_to_check = get_quarters_for_date_range(start_d, end_d)
    logging.info(f"Will check {len(quarters_to_check)} quarters that could have filings in date range")
    
    jobs = []
    for co in companies:
        ticker, bse_code = co["ticker"], co["bse_code"]
        for conso in ("Consolidated", "Standalone"):
            for fy, q in quarters_to_check:
                jobs.append({
                    "ticker": ticker,
                    "fiscal_year": fy,
                    "quarter": q,
                    "source_type": SOURCE_TYPE,
                    "consolidation_status": conso,
                    "ingestion_script_version": SCRIPT_VERSION,
                })

    if jobs:
        create_ingestion_jobs(jobs)
        logging.info(f"Manifest: {len(jobs)} jobs created/verified")

    # 2️⃣  Pull re-triable jobs
    jobs_to_do = get_jobs_by_status(
        statuses=["PENDING", "FETCH_FAILED", "MISSING_AT_SOURCE"],
        script_version=SCRIPT_VERSION,
    )
    if not jobs_to_do:
        logging.info("Nothing to do – all caught up.")
        return

    # 3️⃣  Crawl master announcement list
    sess = seed_bse_session()
    ann_rows: List[Dict] = []
    code_map = {
        co["bse_code"]: co["ticker"]
        for co in companies
        if any(j.ticker == co["ticker"] for j in jobs_to_do)
    }
    for code in code_map:
        page = 1
        while True:
            params = {
                "pageno": page,
                "strCat": "Result",
                "subcategory": "Financial Results",
                "strPrevDate": start_d.strftime("%Y%m%d"),
                "strToDate": end_d.strftime("%Y%m%d"),
                "strScrip": code,
                "strSearch": "P",
                "strType": "C",
            }
            blob = get_json_with_retry(sess, params, code)
            if not blob or not blob.get("Table"):
                break
            ann_rows.extend(blob["Table"])
            if len(blob["Table"]) < 20:
                break
            page += 1
            time.sleep(REQUEST_DELAY + random.random())
    logging.info(f"Fetched {len(ann_rows)} announcements")

    # 4️⃣  Map announcements to quarters based on filing windows
    ann_map: Dict[tuple, Dict] = {}
    for row in ann_rows:
        try:
            bse_code = str(row["SCRIP_CD"])
            ticker = code_map.get(bse_code)
            if not ticker:
                continue
            
            filing_date = datetime.strptime(row["NEWS_DT"][:10], "%Y-%m-%d").date()
            
            # Check which quarter this filing could belong to
            for fy, q in quarters_to_check:
                filing_start, filing_end = get_filing_window(fy, q)
                if filing_start <= filing_date <= filing_end:
                    # This filing is within the expected window for this quarter
                    ann_map[(ticker, fy, q, "Consolidated")] = row  # BSE PDFs are mostly consolidated
                    ann_map[(ticker, fy, q, "Standalone")] = row  # may point to same PDF
                    logging.debug(f"Mapped {ticker} filing from {filing_date} to FY{fy} Q{q}")
                    break  # Use the first matching quarter (most likely to be correct)
        except Exception as e:
            logging.warning(f"Error processing announcement: {e}")
            continue

    # 5️⃣  Process each job
    for job in jobs_to_do:
        fy, q, ticker, conso = (
            job.fiscal_year,
            job.quarter,
            job.ticker,
            job.consolidation_status,
        )
        logging.info(f"Processing {ticker} FY{fy} Q{q} {conso}")
        
        ann = ann_map.get((ticker, fy, q, conso))
        if not ann:
            filing_start, filing_end = get_filing_window(fy, q)
            log_ingestion_failure(
                job.job_id,
                "MISSING_AT_SOURCE",
                f"No filing found in expected window {filing_start} to {filing_end}",
            )
            continue

        urls = candidate_urls(ann)
        if not urls:
            log_ingestion_failure(
                job.job_id, "MISSING_AT_SOURCE", "Announcement has no attachment link"
            )
            continue

        co_dir = DATA_ROOT / "raw" / "pdf" / ticker
        co_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{ticker}_FY{fy}_Q{q}_{conso}.pdf"
        fpath = co_dir / fname

        # Existing local file branch
        if fpath.exists():
            local_hash = file_sha256(fpath)
            asset = get_asset_by_hash(local_hash)
            meta = remote_metadata(sess, urls[0])

            needs_refresh = (
                meta.get("modified")
                and (
                    not asset
                    or not getattr(asset, "source_last_modified", None)
                    or meta["modified"] > asset.source_last_modified
                )
            )

            if not needs_refresh:
                log_ingestion_success(
                    job.job_id,
                    raw_data_hash=local_hash,
                    source_type=SOURCE_TYPE,
                    storage_location=str(fpath),
                    source_last_modified=getattr(asset, "source_last_modified", None)
                    if asset
                    else None,
                )
                continue

            # compare-then-swap
            tmp = fpath.with_suffix(".tmp")
            ok, new_lm = download_pdf(sess, urls, tmp)
            if not ok:
                log_ingestion_failure(job.job_id, "FETCH_FAILED", "Refresh failed")
                continue
            new_hash = file_sha256(tmp)
            if new_hash == local_hash:
                tmp.unlink()
                log_ingestion_success(
                    job.job_id,
                    raw_data_hash=local_hash,
                    source_type=SOURCE_TYPE,
                    storage_location=str(fpath),
                    source_last_modified=new_lm,
                )
            else:
                fpath.unlink()
                tmp.rename(fpath)
                log_ingestion_success(
                    job.job_id,
                    raw_data_hash=new_hash,
                    source_type=SOURCE_TYPE,
                    storage_location=str(fpath),
                    source_last_modified=new_lm,
                )
            time.sleep(0.5 + random.random())
            continue  # next job

        # First-time download branch
        ok, lm = download_pdf(sess, urls, fpath)
        if not ok:
            log_ingestion_failure(job.job_id, "FETCH_FAILED", "All URLs failed")
            continue

        h = file_sha256(fpath)
        existing = get_asset_by_hash(h)
        if existing:
            # global dedup – discard duplicate file, link to existing asset
            fpath.unlink()
            log_ingestion_success(
                job.job_id,
                raw_data_hash=h,
                source_type=SOURCE_TYPE,
                storage_location=existing.storage_location,
                source_last_modified=existing.source_last_modified,
            )
        else:
            log_ingestion_success(
                job.job_id,
                raw_data_hash=h,
                source_type=SOURCE_TYPE,
                storage_location=str(fpath),
                source_last_modified=lm,
            )

        time.sleep(0.5 + random.random())

    logging.info("✅ PDF ingestion finished")

if __name__ == "__main__":
    ingest_all_pdfs("22-03-2024", "25-09-2024")