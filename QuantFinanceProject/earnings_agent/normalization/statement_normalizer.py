# earnings_agent/normalization/statement_normalizer.py

import logging
import json
import hashlib
from typing import Dict, Any, List, Tuple
from datetime import date
from sqlalchemy import select

from earnings_agent.storage.database import (
    get_session,
    get_docs_pending_statement_normalization,
    create_staged_normalized_data,
    mark_docs_statement_normalized, # We will use this again
)
from earnings_agent.storage.models import (
    ParsedDocument,
    StagedNormalizedData,
    JobAssetLink,
    IngestionJob
)

# Standard logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(module)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Expected statements mapping
EXPECTED_STATEMENTS = [
    'standalone_pnl',
    'standalone_balance_sheet', 
    'standalone_cash_flow',
    'consolidated_pnl',
    'consolidated_balance_sheet',
    'consolidated_cash_flow'
]

def extract_company_and_period_metadata(doc_id: int, session) -> Tuple[str, date]:
    """
    Extract company ticker and fiscal_date from the document lineage.
    """
    parsed_doc = session.get(ParsedDocument, doc_id)
    if not parsed_doc:
        raise ValueError(f"ParsedDocument with doc_id {doc_id} not found")
    
    asset = parsed_doc.asset
    if not asset or not asset.job_links:
        raise ValueError(f"No job links found for asset_id {parsed_doc.asset_id}")
    
    job_link = asset.job_links[0]
    job = job_link.job
    
    ticker = job.ticker
    
    if job.quarter == 1:
        fiscal_date = date(job.fiscal_year, 6, 30)
    elif job.quarter == 2:
        fiscal_date = date(job.fiscal_year, 9, 30)
    elif job.quarter == 3:
        fiscal_date = date(job.fiscal_year, 12, 31)
    else:
        fiscal_date = date(job.fiscal_year + 1, 3, 31)
    
    return ticker, fiscal_date

# --- START: MISSING HELPER FUNCTIONS ---
def create_statement_mapping(statements_found: List[Dict], raw_statements: List[Dict]) -> Dict[str, str]:
    """
    Create a mapping from statement_name to standard_mapping.
    """
    mapping = {}
    for stmt in statements_found:
        statement_name = stmt['statement_name']
        standard_mapping = stmt['mapping']
        mapping[statement_name] = standard_mapping
    
    return mapping

def categorize_statements(raw_statements: List[Dict], statement_mapping: Dict[str, str]) -> Dict[str, Any]:
    """
    Categorize statements into standalone and consolidated filings.
    """
    standalone_statements = []
    consolidated_statements = []
    found_statements = set()
    
    for statement in raw_statements:
        statement_name = statement['statement_type']
        standard_mapping = statement_mapping.get(statement_name)
        
        if not standard_mapping:
            logger.warning(f"No mapping found for statement: {statement_name}")
            continue
            
        statement_with_mapping = statement.copy()
        statement_with_mapping['standard_mapping'] = standard_mapping
        found_statements.add(standard_mapping)
        
        if 'standalone' in standard_mapping:
            standalone_statements.append(statement_with_mapping)
        elif 'consolidated' in standard_mapping:
            consolidated_statements.append(statement_with_mapping)
        else:
            logger.warning(f"Unknown statement type mapping: {standard_mapping}")
    
    standalone_filing = {}
    consolidated_filing = {}
    
    for expected in ['standalone_pnl', 'standalone_balance_sheet', 'standalone_cash_flow']:
        found_stmt = next((stmt for stmt in standalone_statements if stmt['standard_mapping'] == expected), None)
        standalone_filing[expected] = found_stmt if found_stmt else "NOT_PROVIDED"
    
    for expected in ['consolidated_pnl', 'consolidated_balance_sheet', 'consolidated_cash_flow']:
        found_stmt = next((stmt for stmt in consolidated_statements if stmt['standard_mapping'] == expected), None)
        consolidated_filing[expected] = found_stmt if found_stmt else "NOT_PROVIDED"
    
    return {
        'standalone': standalone_filing,
        'consolidated': consolidated_filing,
        'found_statements': list(found_statements),
        'missing_statements': [stmt for stmt in EXPECTED_STATEMENTS if stmt not in found_statements]
    }
# --- END: MISSING HELPER FUNCTIONS ---

def normalize_single_document(doc_id: int, session) -> bool:
    """
    Normalize statements for a single parsed document and UPDATE the existing staged record.
    Returns True if successful, False otherwise.
    """
    try:
        parsed_doc = session.get(ParsedDocument, doc_id)
        if not parsed_doc or not parsed_doc.content:
            logger.error(f"No content found for doc_id {doc_id}")
            return False
        
        content = parsed_doc.content
        if 'llm_call_1' not in content or 'llm_call_2' not in content:
            logger.error(f"Invalid content structure for doc_id {doc_id}")
            return False
        
        statements_found = content['llm_call_1'].get('statements_found', [])
        raw_statements = content['llm_call_2']
        if not isinstance(raw_statements, list):
            logger.error(f"llm_call_2 is not a list for doc_id {doc_id}")
            return False
        
        ticker, fiscal_date = extract_company_and_period_metadata(doc_id, session)
        logger.info(f"Processing document {doc_id} for {ticker} {fiscal_date}")
        
        statement_mapping = create_statement_mapping(statements_found, raw_statements)
        categorized_data = categorize_statements(raw_statements, statement_mapping)
        
        normalized_data = {
            'statement_normalized_data': {
                'standalone': categorized_data['standalone'],
                'consolidated': categorized_data['consolidated']
            },
            'metadata': {
                'found_statements': categorized_data['found_statements'],
                'missing_statements': categorized_data['missing_statements'],
                'total_statements_processed': len(raw_statements),
                'statement_mapping': statement_mapping
            }
        }
        
        data_hash = hashlib.sha256(json.dumps(normalized_data, sort_keys=True).encode()).hexdigest()
        
        staged_record = session.query(StagedNormalizedData).filter_by(doc_id=doc_id).one()
        staged_record.normalized_data = normalized_data
        staged_record.data_hash = data_hash
        
        # This commit is for the update of a single document
        session.commit()
        
        logger.info(f"Successfully normalized and updated staged record for doc_id {doc_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error processing doc_id {doc_id}: {e}", exc_info=True)
        session.rollback()
        return False

def run_statement_normalizer_batch():
    """
    Main function to run statement normalization on all pending documents.
    """
    logger.info("=== Starting Statement Normalizer Batch ===")
    session = get_session()
    
    try:
        logger.info("   🔍 Searching for parsed documents not yet staged for normalization...")
        stmt = (
            select(ParsedDocument.doc_id)
            .outerjoin(StagedNormalizedData, ParsedDocument.doc_id == StagedNormalizedData.doc_id)
            .where(
                ParsedDocument.parse_status == 'PARSED_OK',
                StagedNormalizedData.id == None
            )
        )
        docs_to_stage = session.execute(stmt).scalars().all()
        
        if docs_to_stage:
            logger.info(f"   Found {len(docs_to_stage)} new documents to stage. Creating initial records...")
            for doc_id in docs_to_stage:
                ticker, fiscal_date = extract_company_and_period_metadata(doc_id, session)
                staged_data = {
                    'doc_id': doc_id,
                    'ticker': ticker,
                    'fiscal_date': fiscal_date,
                    'normalized_data': {}
                }
                create_staged_normalized_data(staged_data)
            logger.info("   ✅ Initial staging records created.")
        else:
            logger.info("   All parsed documents are already staged.")

        doc_ids_to_normalize = get_docs_pending_statement_normalization()
        if not doc_ids_to_normalize:
            logger.info("No documents pending statement normalization.")
            return

        logger.info(f"Found {len(doc_ids_to_normalize)} documents pending statement normalization.")
        successful_docs = []
        failed_docs = []
        
        for doc_id in doc_ids_to_normalize:
            if normalize_single_document(doc_id, session):
                successful_docs.append(doc_id)
            else:
                failed_docs.append(doc_id)
        
        if successful_docs:
            mark_docs_statement_normalized(successful_docs)
            logger.info(f"Marked {len(successful_docs)} documents as statement-normalized.")
        
        if failed_docs:
            logger.warning(f"Failed to process {len(failed_docs)} documents: {failed_docs}")

    except Exception as e:
        logger.error(f"Batch processing error: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()

    logger.info("=== Statement Normalizer Batch Complete ===")