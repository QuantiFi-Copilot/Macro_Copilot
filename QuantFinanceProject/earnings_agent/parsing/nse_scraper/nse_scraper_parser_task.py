# earnings_agent/parsing/nse_scraper/nse_parser_task.py
# Parser script for transforming raw NSE API JSON into a standardized format.
# Version: 2.0 (Aligned with Standard Pipeline Architecture)
# Description:
# This version is a significant refactor to align with the robust, multi-stage
# data processing pipeline. Its sole responsibility is to parse, not normalize.
#   1. Finds all unprocessed raw data assets of type 'NSE_SCRAPER'.
#   2. Extracts the raw key-value pairs from the source JSON payload.
#   3. Cleans and converts numeric data types.
#   4. Stores the simple, flat dictionary output in the `parsed_documents` table,
#      making it ready for the downstream Normalization Engine.

import logging
from sqlalchemy import select, and_
from sqlalchemy.orm import Session as SQLAlchemySession

# Internal project imports
from earnings_agent.storage.database import get_session, create_parsed_document
from earnings_agent.storage.models import RawDataAsset, ParsedDocument

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Configuration & Constants ---
PARSER_VERSION = "2.0.0" # Updated version number
SOURCE_TYPE_FILTER = "NSE_SCRAPER"


# --- Helper Functions ---

def to_numeric(value_str: str | None) -> float | None:
    """Safely converts a string from the API to a float, handling commas and None."""
    if value_str is None or value_str == "" or value_str == "-":
        return None
    try:
        return float(str(value_str).replace(",", ""))
    except (ValueError, TypeError):
        logging.warning(f"Could not convert value to numeric: '{value_str}'")
        return None

# --- Core Parsing Logic ---

def parse_nse_api_asset(asset_id: int, session: SQLAlchemySession):
    """
    Main worker function. Processes a single raw data asset from the NSE API.
    It extracts the raw key-value pairs without performing any normalization.
    """
    logging.info(f"--- Processing Asset ID: {asset_id} ---")
    try:
        asset = session.get(RawDataAsset, asset_id)
        if not asset or not asset.data_content:
            raise ValueError(f"Asset {asset_id} is invalid or has no data_content.")

        financials = asset.data_content.get("resultsData2", {})
        if not financials:
            raise ValueError("`resultsData2` key is missing or empty in the raw JSON.")
            
        # The output is now a simple, flat dictionary of raw facts.
        parsed_content = {api_key: to_numeric(value) for api_key, value in financials.items()}

        doc_data = {
            "asset_id": asset_id, "parser_version": PARSER_VERSION,
            "parse_status": 'PARSED_OK', "content": parsed_content
        }
        create_parsed_document(doc_data)
        logging.info(f"✅ Successfully parsed and stored result for Asset ID: {asset_id}")

    except Exception as e:
        logging.error(f"❌ An error occurred parsing Asset ID {asset_id}: {e}", exc_info=False)
        doc_data = {
            "asset_id": asset_id, "parser_version": PARSER_VERSION,
            "parse_status": 'PARSING_ERROR', "error_details": str(e)
        }
        create_parsed_document(doc_data)
        logging.error(f"   Created PARSING_ERROR record for Asset ID: {asset_id}")


# --- Main Batch Processing Logic ---

def run_parser_batch():
    """
    Finds and processes all unprocessed NSE_SCRAPER assets in the database.
    This is the production-ready entry point for the task.
    """
    logging.info(f"--- Starting NSE Parser Batch Run v{PARSER_VERSION} ---")
    session = get_session()
    try:
        subquery = select(ParsedDocument.asset_id).where(ParsedDocument.parser_version == PARSER_VERSION)
        
        stmt = select(RawDataAsset.asset_id).where(
            and_(
                RawDataAsset.source_type == SOURCE_TYPE_FILTER,
                RawDataAsset.asset_id.notin_(subquery)
            )
        )
        asset_ids_to_process = session.execute(stmt).scalars().all()

        if not asset_ids_to_process:
            logging.info("No new NSE API assets to process. Exiting.")
            return

        logging.info(f"Found {len(asset_ids_to_process)} unprocessed NSE assets. Starting batch.")
        
        for asset_id in asset_ids_to_process:
            try:
                parse_nse_api_asset(asset_id, session)
            except Exception as e:
                logging.critical(f"A critical error occurred in main loop for asset_id {asset_id}: {e}", exc_info=True)

        logging.info("--- Batch run completed successfully. ---")
    finally:
        session.close()


if __name__ == '__main__':
    run_parser_batch()