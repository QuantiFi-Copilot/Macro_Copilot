# earnings_agent/parsing/xbrl_parser_task.py
import json
import logging
from decimal import Decimal, InvalidOperation
from lxml import etree
from datetime import datetime, date
from zoneinfo import ZoneInfo
from types import SimpleNamespace

# --- Core Application Imports ---
# In a real run, these would be the live application imports
from earnings_agent.storage.database import get_session, create_parsed_document
from earnings_agent.storage.models import RawDataAsset, JobAssetLink, IngestionJob
from earnings_agent.common.semantic_map import SEMANTIC_MAP # Assuming this file exists

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Configuration ---
PARSER_VERSION = "1.0" # Bumping version for the new architecture

# ================================================================================================
# CORE PARSING ENGINE - PRESERVED VERBATIM
# The entire XBRLParser class and its helpers are unchanged as their internal logic is proven.
# ================================================================================================

def get_indian_fiscal_period(report_end_date: date) -> tuple[int, int]:
    """Calculates the Indian financial year and quarter from a report's end date."""
    month = report_end_date.month
    year = report_end_date.year
    fiscal_year = year if month >= 4 else year - 1
    if month in (4, 5, 6): return fiscal_year, 1
    elif month in (7, 8, 9): return fiscal_year, 2
    elif month in (10, 11, 12): return fiscal_year, 3
    else: return fiscal_year, 4

class XBRLParser:
    """
    An advanced, dual-context parser for XBRL instance files.
    This class is preserved verbatim from your original script. It is initialized
    with a mock object that has the .local_path, .fiscal_date, and .ticker attributes it expects.
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

    def _process_facts(self, context_id: str | None, core_metrics: dict, custom_kpis: dict):
        if not context_id: return 0
        facts_xpath = f".//*[@contextRef='{context_id}']"
        facts = self.root.xpath(facts_xpath, namespaces=self.namespaces)
        for fact in facts:
            tag = etree.QName(fact.tag).localname
            value_str = fact.text.strip() if fact.text else '0'
            try:
                if fact.get('decimals', 'INF').upper() == 'INF': normalized_value = float(value_str)
                else: normalized_value = int(Decimal(value_str))
            except (InvalidOperation, ValueError, TypeError): continue
            
            if tag in SEMANTIC_MAP:
                standard_name = SEMANTIC_MAP[tag]
                if core_metrics.get(standard_name) is None or core_metrics.get(standard_name) == 0:
                    core_metrics[standard_name] = normalized_value
            else:
                if tag not in custom_kpis or custom_kpis.get(tag) == 0:
                    custom_kpis[tag] = normalized_value
        return len(facts)

    def parse(self) -> dict:
        core_metrics = {} # We'll populate this based on a defined schema later if needed
        custom_kpis = {}
        duration_facts_count = self._process_facts(self.duration_context_id, core_metrics, custom_kpis)
        instant_facts_count = self._process_facts(self.instant_context_id, core_metrics, custom_kpis)
        
        fiscal_year, quarter = get_indian_fiscal_period(self.source_info.fiscal_date)
        status = "PARTIAL_DATA" if not self.duration_context_id or not self.instant_context_id else "SUCCESS"

        return {
            "filing_metadata": {"ticker": self.source_info.ticker, "fiscal_date": self.source_info.fiscal_date.isoformat(), "period": f"Q{quarter}"},
            "core_metrics": core_metrics, "custom_kpis": custom_kpis,
            "parsing_summary": {"parser_version": PARSER_VERSION, "status": status, "duration_context_found": bool(self.duration_context_id),
                "instant_context_found": bool(self.instant_context_id), "total_facts_found": duration_facts_count + instant_facts_count,
                "core_metrics_mapped": sum(1 for v in core_metrics.values() if v is not None), "custom_kpis_found": len(custom_kpis),
                "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            }
        }


# ================================================================================================
# NEW WORKER LOGIC - The "Runner" for the parsing task.
# ================================================================================================

def parse_xbrl_asset(asset_id: int):
    """
    Main function for this task. Processes one single raw data asset.
    This function replaces the old `if __name__ == '__main__'` block.
    """
    logging.info(f">>> (TASK) Starting Parse for asset_id: {asset_id} <<<")
    session = get_session()
    
    try:
        # Step 1: Fetch the asset and its related job info from the database
        asset = session.get(RawDataAsset, asset_id)
        if not asset:
            logging.error(f"No RawDataAsset found for asset_id: {asset_id}. Aborting.")
            return

        # The parser needs ticker and fiscal_date, which are on the job. We must fetch them.
        link = session.query(JobAssetLink).filter(JobAssetLink.asset_id == asset_id).first()
        if not link:
            raise Exception(f"Could not find associated job for asset_id {asset_id}. Cannot determine metadata.")
            
        job = session.get(IngestionJob, link.job_id)
        if not job:
            raise Exception(f"Could not find IngestionJob with id {link.job_id}.")

        # Step 2: Create a mock object that the unchanged parser class can use
        if job.quarter == 1: quarter_end_date = date(job.fiscal_year, 6, 30)
        elif job.quarter == 2: quarter_end_date = date(job.fiscal_year, 9, 30)
        elif job.quarter == 3: quarter_end_date = date(job.fiscal_year, 12, 31)
        else: quarter_end_date = date(job.fiscal_year + 1, 3, 31)
        
        source_info_for_parser = SimpleNamespace(
            local_path=asset.storage_location,
            fiscal_date=quarter_end_date,
            ticker=job.ticker
        )

        # Step 3: Run the parser and save the result
        try:
            parser = XBRLParser(source_info=source_info_for_parser)
            parsed_content = parser.parse()
            
            # Prepare data for our new database function
            doc_data = {
                "asset_id": asset_id,
                "parser_version": PARSER_VERSION,
                "parse_status": 'PARSED_OK',
                "content": parsed_content
            }
            create_parsed_document(doc_data)
            logging.info(f"Successfully parsed and stored result for asset_id: {asset_id}")

        except Exception as e:
            logging.error(f"An error occurred parsing asset_id {asset_id}: {e}", exc_info=True)
            # Log a failure record to the database
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
        logging.info(f">>> (TASK) Finished Parse for asset_id: {asset_id} <<<")


if __name__ == '__main__':
    # This block finds a single, successfully ingested asset and tries to parse it.
    # It's a perfect way to test the parser in isolation.
    logging.info("--- Running Parser Task in standalone test mode ---")
    session = get_session()
    try:
        # Find the asset_id of the first job that has a status of SUCCESS.
        asset_to_parse_id = session.query(JobAssetLink.asset_id)\
            .join(IngestionJob, JobAssetLink.job_id == IngestionJob.job_id)\
            .filter(IngestionJob.status == 'SUCCESS')\
            .first()

        if asset_to_parse_id:
            logging.info(f"Found asset_id {asset_to_parse_id[0]} to test.")
            parse_xbrl_asset(asset_id=asset_to_parse_id[0])
        else:
            logging.warning("No successfully ingested assets found to parse. Did the ingestion script run correctly?")
    finally:
        session.close()