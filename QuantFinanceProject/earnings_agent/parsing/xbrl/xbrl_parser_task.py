# earnings_agent/parsing/xbrl/xbrl_parser_task.py
import json
import logging
from decimal import Decimal, InvalidOperation
from lxml import etree
from datetime import datetime, date
from zoneinfo import ZoneInfo
from types import SimpleNamespace

# --- Core Application Imports ---
from earnings_agent.storage.database import get_session, create_parsed_document
from earnings_agent.storage.models import RawDataAsset, JobAssetLink, IngestionJob, ParsedDocument
from sqlalchemy import select

# --- Standard Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Configuration ---
PARSER_VERSION = "2.0-raw-facts-extractor"

# ================================================================================================
# CORE PARSING ENGINE - REFACTORED
# ================================================================================================

class XBRLParser:
    """
    An advanced, dual-context parser for XBRL instance files.
    REFACTORED: This parser's responsibility is now solely to extract raw,
    un-normalized key-value pairs from the XBRL document.
    """
    def __init__(self, source_info: SimpleNamespace):
        self.source_info = source_info
        if not self.source_info or not self.source_info.local_path:
            raise FileNotFoundError(f"Source info is invalid or has no local_path.")
            
        self.tree = etree.parse(self.source_info.local_path)
        self.root = self.tree.getroot()
        self.namespaces = {k if k is not None else 'xbrli': v for k, v in self.root.nsmap.items()}
        self.duration_context_id = self._find_context(instant=False)
        self.instant_context_id = self._find_context(instant=True)

    def _find_context(self, instant: bool) -> str | None:
        """
        Finds the primary context ID for a given period type (instant or duration).
        This logic is preserved as it is proven and effective.
        """
        date_str = self.source_info.fiscal_date.strftime('%Y-%m-%d')
        period_element = 'instant' if instant else 'endDate'
        xpath = f".//xbrli:context[not(.//xbrli:segment) and .//xbrli:period[xbrli:{period_element}='{date_str}']]"
        contexts = self.root.xpath(xpath, namespaces=self.namespaces)
        if contexts: return contexts[0].get('id')
        
        fallback_xpath = f".//xbrli:context[.//xbrli:period[xbrli:{period_element}='{date_str}']]"
        fallback_contexts = self.root.xpath(fallback_xpath, namespaces=self.namespaces)
        if fallback_contexts:
            context_id = fallback_contexts[0].get('id')
            logging.warning(f"Using fallback context for {self.source_info.ticker}: {context_id}")
            return context_id

        logging.warning(f"No suitable context found for {self.source_info.ticker} on date {date_str}.")
        return None

    def _process_facts(self, context_id: str | None, raw_facts: dict):
        """
        REFACTORED: Extracts all facts for a given context into a single dictionary.
        It no longer normalizes or categorizes them.
        """
        if not context_id:
            return 0
            
        facts_xpath = f".//*[@contextRef='{context_id}']"
        facts = self.root.xpath(facts_xpath, namespaces=self.namespaces)
        
        for fact in facts:
            tag = etree.QName(fact.tag).localname
            value_str = fact.text.strip() if fact.text else '0'
            
            try:
                if fact.get('decimals', 'INF').upper() == 'INF':
                    normalized_value = float(value_str)
                else:
                    normalized_value = int(Decimal(value_str))
            except (InvalidOperation, ValueError, TypeError):
                continue
            
            raw_facts[tag] = normalized_value
            
        return len(facts)

    def parse(self) -> dict:
        """
        REFACTORED: The main parsing method.
        Returns a simple dictionary containing all raw facts found in the document.
        """
        raw_facts = {}
        duration_facts_count = self._process_facts(self.duration_context_id, raw_facts)
        instant_facts_count = self._process_facts(self.instant_context_id, raw_facts)
        
        status = "PARTIAL_DATA" if not self.duration_context_id or not self.instant_context_id else "SUCCESS"

        return {
            "raw_facts": raw_facts,
            "parsing_summary": {
                "parser_version": PARSER_VERSION,
                "status": status,
                "duration_context_found": bool(self.duration_context_id),
                "instant_context_found": bool(self.instant_context_id),
                "total_facts_extracted": len(raw_facts),
                "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            }
        }


# ================================================================================================
# WORKER LOGIC - The "Runner" for the parsing task.
# ================================================================================================

def parse_xbrl_asset(asset_id: int):
    """
    Main function for this task. Processes one single raw data asset.
    """
    logging.info(f">>> (TASK) Starting XBRL Parse for asset_id: {asset_id} <<<")
    # MODIFICATION: A session is passed in, not created, to support batch processing.
    session = get_session()
    
    try:
        asset = session.get(RawDataAsset, asset_id)
        if not asset:
            logging.error(f"No RawDataAsset found for asset_id: {asset_id}. Aborting.")
            return

        link = session.query(JobAssetLink).filter(JobAssetLink.asset_id == asset_id).first()
        if not link:
            raise Exception(f"Could not find associated job for asset_id {asset_id}. Cannot determine metadata.")
            
        job = session.get(IngestionJob, link.job_id)
        if not job:
            raise Exception(f"Could not find IngestionJob with id {link.job_id}.")

        if job.quarter == 1: quarter_end_date = date(job.fiscal_year, 6, 30)
        elif job.quarter == 2: quarter_end_date = date(job.fiscal_year, 9, 30)
        elif job.quarter == 3: quarter_end_date = date(job.fiscal_year, 12, 31)
        else: quarter_end_date = date(job.fiscal_year + 1, 3, 31)
        
        source_info_for_parser = SimpleNamespace(
            local_path=asset.storage_location,
            fiscal_date=quarter_end_date,
            ticker=job.ticker
        )

        try:
            parser = XBRLParser(source_info=source_info_for_parser)
            parsed_content = parser.parse()
            
            doc_data = {
                "asset_id": asset_id,
                "parser_version": PARSER_VERSION,
                "parse_status": 'PARSED_OK',
                "content": parsed_content
            }
            create_parsed_document(doc_data)
            logging.info(f"Successfully parsed and stored raw facts for asset_id: {asset_id}")

        except Exception as e:
            logging.error(f"An error occurred parsing asset_id {asset_id}: {e}", exc_info=True)
            doc_data = {
                "asset_id": asset_id,
                "parser_version": PARSER_VERSION,
                "parse_status": 'PARSING_ERROR',
                "error_details": str(e)
            }
            create_parsed_document(doc_data)
            logging.error(f"Created PARSING_ERROR record for asset_id: {asset_id}")

    finally:
        session.close()
        logging.info(f">>> (TASK) Finished XBRL Parse for asset_id: {asset_id} <<<")


if __name__ == '__main__':
    # === MODIFIED FOR BULK PROCESSING ===
    logging.info(f"--- Running XBRL Parser Task in BULK mode v{PARSER_VERSION} ---")
    session = get_session()
    try:
        # Find all unprocessed assets from the XBRL source.
        subquery = select(ParsedDocument.asset_id).where(ParsedDocument.parser_version == PARSER_VERSION)
        
        # The query now gets all results, not just the first one.
        results = session.query(JobAssetLink.asset_id)\
            .join(IngestionJob, JobAssetLink.job_id == IngestionJob.job_id)\
            .filter(IngestionJob.status == 'SUCCESS')\
            .filter(JobAssetLink.asset_id.notin_(subquery))\
            .all()

        # Extract just the integer asset_ids from the result tuples.
        assets_to_process = [r[0] for r in results]

        if assets_to_process:
            logging.info(f"Found {len(assets_to_process)} assets to process.")
            # Loop through each asset_id and process it.
            for asset_id in assets_to_process:
                try:
                    parse_xbrl_asset(asset_id=asset_id)
                except Exception as e:
                    logging.error(f"An unexpected error occurred processing asset_id {asset_id}. Skipping. Error: {e}")
        else:
            logging.warning(f"No new, successfully ingested XBRL assets found to parse with version '{PARSER_VERSION}'.")
    finally:
        session.close()