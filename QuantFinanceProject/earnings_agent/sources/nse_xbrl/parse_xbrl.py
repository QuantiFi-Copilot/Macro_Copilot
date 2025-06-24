# earnings_agent/parsing/parse_xbrl.py

import json
import logging
from decimal import Decimal, InvalidOperation
from lxml import etree
from datetime import datetime, date
from zoneinfo import ZoneInfo
from sqlalchemy import select, and_

# --- Internal project imports ---
from earnings_agent.storage.database import get_session, upsert_parsed_earning
from earnings_agent.storage.models import RawDocument, ParsedEarning, QuarterlyFundamental
from earnings_agent.common.semantic_map import SEMANTIC_MAP

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Centralized Parser Version ---
# This is crucial for the staging table logic. Increment this when you change the parser's logic.
PARSER_VERSION = "2.3" # <-- VERSION INCREMENTED
SOURCE_TYPE = "XBRL_NSE"


class XBRLParser:
    """
    An advanced, dual-context parser for XBRL instance files. (Version 2.3)
    This version adds data quality status to the output based on context availability.
    """

    def __init__(self, raw_document: RawDocument, session):
        self.raw_document = raw_document
        self.session = session
        
        if not self.raw_document or not self.raw_document.local_path:
            raise FileNotFoundError(f"Raw document object is invalid or has no local_path.")
            
        self.tree = etree.parse(self.raw_document.local_path)
        self.root = self.tree.getroot()
        self.namespaces = {k if k is not None else 'xbrli': v for k, v in self.root.nsmap.items()}
        
        self.duration_context_id = self._find_context(instant=False)
        self.instant_context_id = self._find_context(instant=True)

    def _find_context(self, instant: bool) -> str | None:
        date_str = self.raw_document.fiscal_date.strftime('%Y-%m-%d')
        period_element = 'instant' if instant else 'endDate'
        
        xpath = (
            f".//xbrli:context[not(.//xbrli:segment) and .//xbrli:period[xbrli:{period_element}='{date_str}']]"
        )
        contexts = self.root.xpath(xpath, namespaces=self.namespaces)
        
        if contexts:
            return contexts[0].get('id')

        fallback_xpath = f".//xbrli:context[.//xbrli:period[xbrli:{period_element}='{date_str}']]"
        fallback_contexts = self.root.xpath(fallback_xpath, namespaces=self.namespaces)
        if fallback_contexts:
            context_id = fallback_contexts[0].get('id')
            logging.warning(f"Using fallback {'instant' if instant else 'duration'} context for {self.raw_document.ticker}: {context_id}")
            return context_id

        # CHANGED: Logging level changed from ERROR to WARNING
        logging.warning(f"No suitable {'instant' if instant else 'duration'} context found for {self.raw_document.ticker} on date {date_str}.")
        return None

    def _process_facts(self, context_id: str | None, core_metrics: dict, custom_kpis: dict):
        if not context_id: return 0
        facts_xpath = f".//*[@contextRef='{context_id}']"
        facts = self.root.xpath(facts_xpath, namespaces=self.namespaces)
        for fact in facts:
            tag = etree.QName(fact.tag).localname
            value_str = fact.text.strip() if fact.text else '0'
            decimals = fact.get('decimals', 'INF')
            try:
                if decimals.upper() == 'INF':
                    normalized_value = float(value_str)
                else:
                    normalized_value = int(Decimal(value_str))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if tag in SEMANTIC_MAP:
                standard_name = SEMANTIC_MAP[tag]
                if core_metrics.get(standard_name) is None or core_metrics.get(standard_name) == 0:
                    core_metrics[standard_name] = normalized_value
            else:
                if tag not in custom_kpis or custom_kpis.get(tag) == 0:
                    custom_kpis[tag] = normalized_value
        return len(facts)

    def parse(self) -> dict:
        """
        Parses the XBRL file and returns a structured dictionary ready for the staging table.
        The output now includes a rich parsing_summary with a data quality status.
        """
        core_metric_keys = [c.name for c in QuarterlyFundamental.__table__.columns if c.name not in ['id', 'created_at', 'updated_at']]
        core_metrics = {key: None for key in core_metric_keys}
        custom_kpis = {}
        
        duration_facts_count = self._process_facts(self.duration_context_id, core_metrics, custom_kpis)
        instant_facts_count = self._process_facts(self.instant_context_id, core_metrics, custom_kpis)

        month = self.raw_document.fiscal_date.month
        financial_quarter = ((month - 4 + 12) % 12) // 3 + 1
        
        # NEW: Determine the status based on context availability
        status = "SUCCESS"
        if not self.duration_context_id or not self.instant_context_id:
            status = "PARTIAL_DATA"

        # CHANGED: The parsing_summary dictionary is enriched
        output_content = {
            "filing_metadata": { "source_document_id": self.raw_document.id, "ticker": self.raw_document.ticker, "fiscal_date": self.raw_document.fiscal_date.isoformat(), "period": f"Q{financial_quarter}" },
            "core_metrics": core_metrics,
            "custom_kpis": custom_kpis,
            "parsing_summary": {
                "parser_version": PARSER_VERSION,
                "status": status,
                "duration_context_found": bool(self.duration_context_id),
                "instant_context_found": bool(self.instant_context_id),
                "total_facts_found": duration_facts_count + instant_facts_count,
                "core_metrics_mapped": sum(1 for v in core_metrics.values() if v is not None),
                "custom_kpis_found": len(custom_kpis),
                "parsing_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            }
        }
        
        record_for_db = {
            "raw_document_id": self.raw_document.id, "source_type": SOURCE_TYPE, "parser_version": PARSER_VERSION,
            "ticker": self.raw_document.ticker, "fiscal_date": self.raw_document.fiscal_date, "content": output_content
        }

        return record_for_db


# --- Main Batch Processing Logic ---
if __name__ == '__main__':
    logging.info(f"--- Starting XBRL Batch Parse v{PARSER_VERSION} ---")
    session = get_session()
    
    try:
        subquery = select(ParsedEarning.raw_document_id).where(
            and_(
                ParsedEarning.source_type == SOURCE_TYPE,
                ParsedEarning.parser_version == PARSER_VERSION
            )
        ).scalar_subquery()

        stmt = select(RawDocument).where(
            and_(
                RawDocument.doc_type == 'XBRL_INSTANCE',
                RawDocument.id.notin_(subquery)
            )
        ).order_by(RawDocument.id)
        
        documents_to_process = session.execute(stmt).scalars().all()
        
        if not documents_to_process:
            logging.info(f"All XBRL documents are already processed with parser v{PARSER_VERSION}. No new files to parse.")
        else:
            logging.info(f"Found {len(documents_to_process)} XBRL documents to parse with v{PARSER_VERSION}.")
            
            for doc in documents_to_process:
                logging.info(f"Processing doc_id: {doc.id} for ticker: {doc.ticker} ({doc.fiscal_date})")
                try:
                    parser = XBRLParser(raw_document=doc, session=session)
                    parsed_record = parser.parse()
                    
                    upsert_parsed_earning(parsed_record)
                    
                    # CHANGED: More intelligent logging based on the parse status
                    parse_status = parsed_record['content']['parsing_summary']['status']
                    if parse_status == 'PARTIAL_DATA':
                        logging.warning(f"Stored PARTIAL parse for doc_id: {doc.id}. Check parsing_summary for details.")
                    else:
                        logging.info(f"Successfully stored FULL parse for doc_id: {doc.id}")

                except FileNotFoundError as e:
                    logging.error(f"Halting execution for doc_id {doc.id}: {e}")
                except Exception as e:
                    logging.error(f"An unexpected error occurred processing doc_id {doc.id}: {e}", exc_info=True)

    finally:
        session.close()
        logging.info(f"--- XBRL Batch Parse Finished v{PARSER_VERSION} ---")