# earnings_agent/sources/nse_scraper/parse_nse.py
# Parser script for transforming raw NSE API JSON into a standardized format.
# Version: 1.0
# Description:
# This is the second stage of the NSE Scraper pipeline. It:
#   1. Selects raw data records of type 'NSE_SCRAPER' from the `raw_sources` table
#      that have not yet been processed by this parser version.
#   2. Uses the `nse_decoder_map` to translate cryptic API keys into our standard metric names.
#   3. Cleans and converts data into a structured format.
#   4. Stores the structured output in the `parsed_earnings` table.
#   5. Updates the `ingestion_log` with the final parsing status.

# In the new nse_parser_task.py
import logging
import json
from datetime import datetime, date
from dateutil.relativedelta import relativedelta # ADD THIS
from zoneinfo import ZoneInfo
from sqlalchemy import select, and_ # ADD 'and_' HERE

# Internal project imports
from earnings_agent.storage.database import get_session, create_parsed_document
# ADD 'ParsedDocument' HERE
from earnings_agent.storage.models import RawDataAsset, JobAssetLink, IngestionJob, ParsedDocument
from earnings_agent.ingestion.nse_scraper.nse_decoder_map import NSE_DECODER_MAP

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Configuration & Constants ---
# This version number is crucial. Incrementing it will cause the script
# to re-process all existing raw sources with the updated logic.
NSE_PARSER_VERSION = "1.0"
SOURCE_TYPE_FILTER = "NSE_SCRAPER"


# --- Helper Functions ---

def to_numeric(value_str: str | None) -> float | None:
    """Safely converts a string to a float, handling commas and None values."""
    if value_str is None or value_str == "" or value_str == "-":
        return None
    try:
        # Replace commas and convert to float
        return float(str(value_str).replace(",", ""))
    except (ValueError, TypeError):
        logging.warning(f"Could not convert value to numeric: '{value_str}'")
        return None

def get_indian_fiscal_period(report_end_date: date) -> tuple[int, int]:
    """Calculates the Indian financial year and quarter from a report's end date."""
    month = report_end_date.month
    year = report_end_date.year
    fiscal_year = year if month >= 4 else year - 1
    if month in (4, 5, 6): return fiscal_year, 1
    elif month in (7, 8, 9): return fiscal_year, 2
    elif month in (10, 11, 12): return fiscal_year, 3
    else: return fiscal_year, 4

# --- Core Parsing Logic ---
# In the new nse_parser_task.py
class NSEParser:
    """
    Transforms a raw JSON response from the NSE API into our standard format.
    MODIFIED: Now initializes with new RawDataAsset and IngestionJob models.
    """
    def __init__(self, raw_asset: RawDataAsset, job: IngestionJob):
        if not raw_asset or not raw_asset.data_content:
            raise ValueError(f"RawDataAsset object is invalid or has no data_content for asset_id: {raw_asset.asset_id}")
        self.raw_asset = raw_asset
        self.job = job
        self.raw_data = raw_asset.data_content

    def parse(self) -> dict:
        """
        Executes the transformation logic using the NSE_DECODER_MAP.
        The core parsing logic here is UNCHANGED.
        """
        financials = self.raw_data.get("resultsData2", {})
        if not financials:
            raise ValueError("`resultsData2` key is missing or empty in the raw JSON.")
            
        core_metrics = {}
        custom_kpis = {}

        for api_key, value in financials.items():
            if api_key in NSE_DECODER_MAP:
                standard_name = NSE_DECODER_MAP[api_key]
                if core_metrics.get(standard_name) is None:
                    core_metrics[standard_name] = to_numeric(value)
            else:
                custom_kpis[api_key] = value

        # Use the job context to get metadata
        fiscal_year, quarter = self.job.fiscal_year, self.job.quarter
        # Reconstruct the fiscal_date for the metadata block
        q_end_month, q_end_year = ((6, fiscal_year), (9, fiscal_year), (12, fiscal_year), (3, fiscal_year + 1))[quarter-1]
        fiscal_date = date(q_end_year if q_end_month < 12 else q_end_year + 1, q_end_month % 12 + 1, 1) - relativedelta(days=1)


        output_content = {
            "filing_metadata": {
                "asset_id": self.raw_asset.asset_id,
                "ticker": self.job.ticker,
                "fiscal_date": fiscal_date.isoformat(),
                "period": f"Q{quarter}",
                "consolidation_status": self.job.consolidation_status
            },
            "core_metrics": core_metrics,
            "custom_kpis": custom_kpis,
            "parsing_summary": {
                "parser_version": NSE_PARSER_VERSION,
                "status": "SUCCESS",
                "core_metrics_mapped": len(core_metrics),
                "custom_kpis_found": len(custom_kpis),
                "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            }
        }
        
        # The function now returns the content blob directly
        return output_content

# --- Main Batch Processing Logic ---

# In the new nse_parser_task.py

# --- NEW: Main Worker Function ---
def parse_nse_api_asset(asset_id: int):
    """
    Main function for this task. Processes one single raw data asset from the NSE API.
    """
    logging.info(f">>> (TASK) Starting NSE Parse for asset_id: {asset_id} <<<")
    session = get_session()
    
    try:
        # Fetch the asset and its related job info to get the full context
        asset = session.get(RawDataAsset, asset_id)
        if not asset:
            logging.error(f"No RawDataAsset found for asset_id: {asset_id}. Aborting.")
            return

        link = session.query(JobAssetLink).filter(JobAssetLink.asset_id == asset.asset_id).first()
        if not link:
            raise Exception(f"Could not find associated job for asset_id {asset_id}. Cannot determine metadata.")
            
        job = session.get(IngestionJob, link.job_id)
        if not job:
            raise Exception(f"Could not find IngestionJob with id {link.job_id}.")
        
        # --- Using the new database function with robust error handling ---
        try:
            parser = NSEParser(raw_asset=asset, job=job)
            parsed_content = parser.parse()
            
            doc_data = {
                "asset_id": asset_id, "parser_version": NSE_PARSER_VERSION,
                "parse_status": 'PARSED_OK', "content": parsed_content
            }
            create_parsed_document(doc_data)
            logging.info(f"Successfully parsed and stored result for asset_id: {asset_id}")

        except Exception as e:
            logging.error(f"An error occurred parsing asset_id {asset_id}: {e}", exc_info=True)
            doc_data = {
                "asset_id": asset_id, "parser_version": NSE_PARSER_VERSION,
                "parse_status": 'PARSING_ERROR', "error_details": str(e)
            }
            create_parsed_document(doc_data)
            logging.error(f"Created PARSING_ERROR record for asset_id: {asset_id}")
            
    finally:
        session.close()
        logging.info(f">>> (TASK) Finished NSE Parse for asset_id: {asset_id} <<<")


# --- NEW: Test runner block ---
if __name__ == '__main__':
    logging.info(f"--- Running NSE Parser Task in standalone test mode v{NSE_PARSER_VERSION} ---")
    session = get_session()
    try:
        # Find one unprocessed asset from the NSE API source to test with
        subquery = select(ParsedDocument.asset_id).where(ParsedDocument.parser_version == NSE_PARSER_VERSION)
        
        stmt = select(RawDataAsset.asset_id).where(
            and_(
                RawDataAsset.source_type == SOURCE_TYPE_FILTER,
                RawDataAsset.asset_id.notin_(subquery)
            )
        ).limit(1)
        
        asset_to_parse = session.execute(stmt).scalar_one_or_none()

        if asset_to_parse:
            logging.info(f"Found asset_id {asset_to_parse} to test.")
            parse_nse_api_asset(asset_id=asset_to_parse)
        else:
            logging.warning("No new assets found from the NSE API to parse.")
    finally:
        session.close()