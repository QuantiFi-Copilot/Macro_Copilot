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

import logging
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
from sqlalchemy import select, and_

# Internal project imports
from earnings_agent.storage.database import get_session, upsert_parsed_earning, upsert_ingestion_log
from earnings_agent.storage.models import RawSource, ParsedEarning
from .nse_decoder_map import NSE_DECODER_MAP

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

class NSEParser:
    """
    Transforms a raw JSON response from the NSE API into our standard format.
    """
    def __init__(self, raw_source: RawSource):
        if not raw_source or not raw_source.raw_content:
            raise ValueError(f"Raw source object is invalid or has no raw_content for ID: {raw_source.id}")
        self.raw_source = raw_source
        self.raw_data = raw_source.raw_content

    def parse(self) -> dict:
        """
        Executes the transformation logic using the NSE_DECODER_MAP.
        
        Returns:
            A dictionary structured for insertion into the `parsed_earnings` table.
        """
        # The actual financial numbers are nested within the 'resultsData2' key.
        financials = self.raw_data.get("resultsData2", {})
        if not financials:
            raise ValueError("`resultsData2` key is missing or empty in the raw JSON.")
            
        core_metrics = {}
        custom_kpis = {}

        # Iterate through all key-value pairs from the raw API financial data
        for api_key, value in financials.items():
            # If the key is in our decoder map, it's a core metric.
            if api_key in NSE_DECODER_MAP:
                standard_name = NSE_DECODER_MAP[api_key]
                # Avoid overwriting a value if multiple API keys map to the same standard name.
                # The first one found (as defined in the map) takes precedence.
                if core_metrics.get(standard_name) is None:
                    core_metrics[standard_name] = to_numeric(value)
            else:
                # If not in the map, store it as a custom KPI to preserve all data.
                custom_kpis[api_key] = value

        fiscal_year, quarter = get_indian_fiscal_period(self.raw_source.fiscal_date)

        # Construct the final, structured object, mirroring the XBRL parser's output
        # to ensure compatibility with the downstream validation stage.
        output_content = {
            "filing_metadata": {
                "raw_source_id": self.raw_source.id,
                "ticker": self.raw_source.ticker,
                "fiscal_date": self.raw_source.fiscal_date.isoformat(),
                "period": f"Q{quarter}"
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
        
        # This is the final dictionary that will be upserted into the database.
        return {
            "raw_source_id": self.raw_source.id,
            "parser_version": NSE_PARSER_VERSION,
            "content": output_content
        }

# --- Main Batch Processing Logic ---

if __name__ == '__main__':
    logging.info(f"--- Starting NSE Batch Parse v{NSE_PARSER_VERSION} ---")
    session = get_session()
    
    try:
        # This subquery finds all raw_source_ids that have already been
        # successfully parsed by this version of the parser.
        subquery = select(ParsedEarning.raw_source_id).where(
            ParsedEarning.parser_version == NSE_PARSER_VERSION
        ).scalar_subquery()
        
        # The main query selects all raw sources of type 'NSE_SCRAPER' that
        # are NOT in the subquery of already-parsed records.
        stmt = select(RawSource).where(
            and_(
                RawSource.source_type == SOURCE_TYPE_FILTER,
                RawSource.id.notin_(subquery)
            )
        ).order_by(RawSource.id)
        
        sources_to_process = session.execute(stmt).scalars().all()
        
        if not sources_to_process:
            logging.info(f"All '{SOURCE_TYPE_FILTER}' sources are already processed with parser v{NSE_PARSER_VERSION}.")
        else:
            logging.info(f"Found {len(sources_to_process)} new '{SOURCE_TYPE_FILTER}' sources to parse.")
            
            for source in sources_to_process:
                logging.info(f"Processing source_id: {source.id} for ticker: {source.ticker} ({source.fiscal_date})")
                
                fiscal_year, quarter = get_indian_fiscal_period(source.fiscal_date)
                # Prepare data for updating the ingestion log
                log_update_data = {
                    "ticker": source.ticker, "fiscal_year": fiscal_year, "quarter": quarter,
                    "source_type": source.source_type, "raw_source_id": source.id,
                    "checked_at": datetime.now(ZoneInfo("Asia/Kolkata"))
                }

                try:
                    parser = NSEParser(raw_source=source)
                    parsed_record = parser.parse()
                    upsert_parsed_earning(parsed_record)
                    
                    logging.info(f"Successfully stored parsed record for source_id: {source.id}")
                    # Update the ingestion log to show parsing was successful
                    log_update_data["status"] = "PARSED_OK"
                    upsert_ingestion_log(log_update_data)

                except Exception as e:
                    logging.error(f"An unrecoverable error occurred processing source_id {source.id}: {e}", exc_info=False)
                    
                    # Create a failure record in the database for auditability
                    failure_content = {
                        "parsing_summary": {
                            "parser_version": NSE_PARSER_VERSION,
                            "status": "FAILED_TO_PARSE",
                            "error_message": str(e),
                            "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                        },
                        "core_metrics": None,
                        "custom_kpis": None
                    }
                    failure_record_for_db = {
                        "raw_source_id": source.id,
                        "parser_version": NSE_PARSER_VERSION,
                        "content": failure_content
                    }
                    upsert_parsed_earning(failure_record_for_db)
                    logging.error(f"Created FAILED_TO_PARSE record in database for source_id {source.id}.")
                    
                    # Update the ingestion log to show parsing failed
                    log_update_data["status"] = "PARSE_ERROR"
                    upsert_ingestion_log(log_update_data)

    finally:
        session.close()
        logging.info(f"--- NSE Batch Parse Finished v{NSE_PARSER_VERSION} ---")
