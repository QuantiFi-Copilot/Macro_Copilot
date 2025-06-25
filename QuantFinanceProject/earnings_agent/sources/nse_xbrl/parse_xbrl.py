# earnings_agent/sources/xbrl/parse_xbrl.py

import json
import logging
from decimal import Decimal, InvalidOperation
from lxml import etree
from datetime import datetime, date
from zoneinfo import ZoneInfo
from sqlalchemy import select, and_

from earnings_agent.storage.database import get_session, upsert_parsed_earning, upsert_ingestion_log
from earnings_agent.storage.models import RawSource, ParsedEarning, QuarterlyFundamental
from earnings_agent.common.semantic_map import SEMANTIC_MAP

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

PARSER_VERSION = "2.3"
SOURCE_TYPE_FILTER = "XBRL_FILE"

# --- NEW: Added helper function to determine fiscal period from within the parser ---
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
    """
    def __init__(self, raw_source: RawSource):
        self.raw_source = raw_source
        if not self.raw_source or not self.raw_source.local_path:
            raise FileNotFoundError(f"Raw source object is invalid or has no local_path.")
            
        self.tree = etree.parse(self.raw_source.local_path)
        self.root = self.tree.getroot()
        self.namespaces = {k if k is not None else 'xbrli': v for k, v in self.root.nsmap.items()}
        self.duration_context_id = self._find_context(instant=False)
        self.instant_context_id = self._find_context(instant=True)

    def _find_context(self, instant: bool) -> str | None:
        date_str = self.raw_source.fiscal_date.strftime('%Y-%m-%d')
        period_element = 'instant' if instant else 'endDate'
        xpath = f".//xbrli:context[not(.//xbrli:segment) and .//xbrli:period[xbrli:{period_element}='{date_str}']]"
        contexts = self.root.xpath(xpath, namespaces=self.namespaces)
        if contexts: return contexts[0].get('id')
        
        fallback_xpath = f".//xbrli:context[.//xbrli:period[xbrli:{period_element}='{date_str}']]"
        fallback_contexts = self.root.xpath(fallback_xpath, namespaces=self.namespaces)
        if fallback_contexts:
            context_id = fallback_contexts[0].get('id')
            logging.warning(f"Using fallback context for {self.raw_source.ticker}: {context_id}")
            return context_id

        logging.warning(f"No suitable context found for {self.raw_source.ticker} on date {date_str}.")
        return None

    def _process_facts(self, context_id: str | None, core_metrics: dict, custom_kpis: dict):
        # This internal logic is unchanged
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
        # This internal logic is unchanged
        core_metric_keys = [c.name for c in QuarterlyFundamental.__table__.columns if c.name not in ['id', 'created_at', 'updated_at']]
        core_metrics = {key: None for key in core_metric_keys}
        custom_kpis = {}
        duration_facts_count = self._process_facts(self.duration_context_id, core_metrics, custom_kpis)
        instant_facts_count = self._process_facts(self.instant_context_id, core_metrics, custom_kpis)
        
        fiscal_year, quarter = get_indian_fiscal_period(self.raw_source.fiscal_date)
        status = "PARTIAL_DATA" if not self.duration_context_id or not self.instant_context_id else "SUCCESS"

        output_content = {
            "filing_metadata": {"raw_source_id": self.raw_source.id, "ticker": self.raw_source.ticker, "fiscal_date": self.raw_source.fiscal_date.isoformat(), "period": f"Q{quarter}"},
            "core_metrics": core_metrics, "custom_kpis": custom_kpis,
            "parsing_summary": {"parser_version": PARSER_VERSION, "status": status, "duration_context_found": bool(self.duration_context_id),
                "instant_context_found": bool(self.instant_context_id), "total_facts_found": duration_facts_count + instant_facts_count,
                "core_metrics_mapped": sum(1 for v in core_metrics.values() if v is not None), "custom_kpis_found": len(custom_kpis),
                "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            }
        }
        return {"raw_source_id": self.raw_source.id, "parser_version": PARSER_VERSION, "content": output_content}


# --- MODIFIED: Main Batch Processing Logic now updates the ingestion_log table ---
if __name__ == '__main__':
    logging.info(f"--- Starting XBRL Batch Parse v{PARSER_VERSION} ---")
    session = get_session()
    
    try:
        subquery = select(ParsedEarning.raw_source_id).where(ParsedEarning.parser_version == PARSER_VERSION).scalar_subquery()
        # We only want to attempt to parse files that were successfully found.
        stmt = select(RawSource).where(
            and_(RawSource.source_type == SOURCE_TYPE_FILTER, RawSource.id.notin_(subquery))
        ).order_by(RawSource.id)
        
        sources_to_process = session.execute(stmt).scalars().all()
        
        if not sources_to_process:
            logging.info(f"All {SOURCE_TYPE_FILTER} sources are already processed with parser v{PARSER_VERSION}.")
        else:
            logging.info(f"Found {len(sources_to_process)} new {SOURCE_TYPE_FILTER} sources to parse.")
            
            for source in sources_to_process:
                logging.info(f"Processing source_id: {source.id} for ticker: {source.ticker} ({source.fiscal_date})")
                fiscal_year, quarter = get_indian_fiscal_period(source.fiscal_date)
                log_update_data = {
                    "ticker": source.ticker, "fiscal_year": fiscal_year, "quarter": quarter,
                    "source_type": source.source_type, "raw_source_id": source.id,
                    "checked_at": datetime.now(ZoneInfo("Asia/Kolkata"))
                }

                try:
                    parser = XBRLParser(raw_source=source)
                    parsed_record = parser.parse()
                    upsert_parsed_earning(parsed_record)
                    
                    logging.info(f"Successfully stored FULL/PARTIAL parse for source_id: {source.id}")
                    # --- NEW: Update ingestion_log on success ---
                    log_update_data["status"] = "PARSED_OK"
                    upsert_ingestion_log(log_update_data)

                except Exception as e:
                    logging.error(f"An unrecoverable error occurred processing source_id {source.id}: {e}", exc_info=False)
                    
                    failure_content = {"parsing_summary": {"parser_version": PARSER_VERSION, "status": "FAILED_TO_PARSE", "error_message": str(e),
                        "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()}, "core_metrics": None, "custom_kpis": None}
                    failure_record_for_db = {"raw_source_id": source.id, "parser_version": PARSER_VERSION, "content": failure_content}
                    
                    upsert_parsed_earning(failure_record_for_db)
                    logging.error(f"Created FAILED_TO_PARSE record in database for source_id {source.id}.")
                    # --- NEW: Update ingestion_log on failure ---
                    log_update_data["status"] = "PARSE_ERROR"
                    upsert_ingestion_log(log_update_data)

    finally:
        session.close()
        logging.info(f"--- XBRL Batch Parse Finished v{PARSER_VERSION} ---")