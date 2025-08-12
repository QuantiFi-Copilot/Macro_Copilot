# earnings_agent/normalization/unit_normalizer.py

import logging
import json
import hashlib
from typing import Dict, Any, List, Tuple, Optional
from datetime import date, datetime

from earnings_agent.storage.database import (
    get_session,
    get_docs_pending_unit_normalization,
    mark_docs_unit_review_status,
    create_unit_review_record,
    get_approved_unit_reviews,
    delete_processed_unit_review,
)
from earnings_agent.storage.models import ParsedDocument, StagedNormalizedData, UnitReviewQueue
from earnings_agent.llm.normalizer_client import call_gemini_with_json

# Standard logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(module)s - %(message)s"
)
logger = logging.getLogger(__name__)

# LLM Configuration
UNIT_ANALYSIS_MODEL = "gemini-2.5-pro" # Using a standard, available model

UNIT_ANALYSIS_PROMPT = """
You are a financial data analyst. You will analyze financial statement figures to determine their exact unit representation.
You will receive financial statements with:
1. Statement-level currency context (e.g., "in lacs", "in crores")  
2. Individual figures with labels and values
3. No industry assumptions - work purely from the data provided

For EACH figure, determine:
1. **representation**: "currency" (uses statement currency), "percentage", "ratio", or "count"
2. **currency_context**: For currency figures, use the statement's currency unit exactly as provided
3. **ratio_context**: For ratios/percentages - "percentage", "absolute", or null
4. **confidence**: "high" or "low" - HIGH only if you can make a completely deterministic decision

**DECISION RULES:**
- **Currency figures**: Use statement-level currency context (e.g., "in lacs" → currency_context: "lacs")
- **Percentage/Ratio indicators**: Labels containing "%", "percentage", "ratio", "rate" → likely percentage/ratio
- **Value patterns**: Very small values (0.01-100) with ratio-suggesting labels → likely percentage  
- **Large values**: Without clear ratio indicators → likely currency amounts
- **MATHEMATICAL CONTEXT:**
Pay very close attention to keywords that imply a negative value, such as "Less:", "(Less)", "Reduction", or if the number is in parentheses `(390.49)`.
If you see these indicators, the extracted `value` should be negative (e.g., -390.49).

**CONFIDENCE RULES:**
- **HIGH confidence**: Clear currency context OR clear percentage/ratio indicators in label or all metrics in the filing.
- **LOW confidence**: Ambiguous labels or unclear value context for any single metric in the filing.

Return this exact JSON structure:
{
  "filing_analysis": {
    "overall_confidence": "high/low",
    "requires_human_review": true/false,
    "currency_contexts_found": ["list of currency contexts from statements"]
  },
  "statement_analyses": [
    {
      "statement_type": "exact statement name from data",
      "standard_mapping": "standalone_pnl/etc",
      "statement_currency": "currency context from this statement",
      "figures": [
        {
          "label": "exact label from data",
          "value": original_value,
          "representation": "currency/percentage/ratio/count",
          "currency_context": "exact currency from statement or null",
          "ratio_context": "percentage/absolute/null", 
          "confidence": "high/low",
          "reasoning": "brief explanation of decision"
        }
      ]
    }
  ]
}

IMPORTANT: 
- Analyze every figure in every statement
- Use exact currency context from statement (don't normalize "in lacs" to "lakhs")
- Be conservative with confidence - mark "low" if there's ANY ambiguity
- Work purely from provided data - no industry assumptions
- IMPORTANT: ENSURE STRICT JSON FORMAT IN THE EXPECTED STRUCTURE GIVEN ABOVE!!!!!!
"""

def get_filing_context_for_analysis(doc_id: int, session) -> Tuple[Dict, Dict]:
    """
    Extract complete filing context for LLM analysis.
    """
    # --- THIS IS THE FIX ---
    # Look up by doc_id using filter_by, not the primary key 'id' with get().
    staged_data = session.query(StagedNormalizedData).filter_by(doc_id=doc_id).one_or_none()
    if not staged_data:
        raise ValueError(f"No staged data found for doc_id {doc_id}")
    # --- END OF FIX ---
    
    statement_normalized_data = staged_data.normalized_data.get('statement_normalized_data', {})
    
    parsed_doc = session.get(ParsedDocument, doc_id)
    if not parsed_doc:
        raise ValueError(f"No parsed document found for doc_id {doc_id}")
    
    original_content = parsed_doc.content
    
    return statement_normalized_data, original_content

def create_llm_analysis_payload(statement_data: Dict, original_content: Dict, ticker: str, fiscal_date: date) -> str:
    """
    Create the analysis payload combining statement data with original currency contexts.
    """
    original_statements = []
    if 'llm_call_2' in original_content:
        for stmt in original_content['llm_call_2']:
            original_statements.append({
                "statement_type": stmt.get('statement_type', ''),
                "currency": stmt.get('currency', ''),
                "quarter": stmt.get('quarter', ''),
                "figures": stmt.get('figures', [])
            })
    
    analysis_context = {
        "company": ticker,
        "fiscal_date": str(fiscal_date),
        "statements_with_currency_context": original_statements,
        "note": "Use the 'currency' field from each statement for currency figures in that statement"
    }
    
    return json.dumps(analysis_context, indent=2)

def parse_llm_unit_analysis(response_text: str) -> Dict:
    """
    Parse and validate LLM response for unit analysis.
    """
    try:
        analysis = json.loads(response_text.strip())
        required_keys = ['filing_analysis', 'statement_analyses']
        if not all(key in analysis for key in required_keys):
            raise ValueError("Missing required keys in LLM response")
        return analysis
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse LLM unit analysis: {e}")
        logger.error(f"Response was: {response_text}")
        raise ValueError("Could not parse unit analysis from LLM") from e

def analyze_units_with_llm(doc_id: int, session) -> Dict:
    """
    Send filing to LLM for contextual unit analysis.
    """
    try:
        statement_data, original_content = get_filing_context_for_analysis(doc_id, session)
        staged_data = session.query(StagedNormalizedData).filter_by(doc_id=doc_id).one()
        
        analysis_payload = create_llm_analysis_payload(
            statement_data, original_content, staged_data.ticker, staged_data.fiscal_date
        )
        
        response_text = call_gemini_with_json(
            model_name=UNIT_ANALYSIS_MODEL,
            prompt=UNIT_ANALYSIS_PROMPT,
            context_text=analysis_payload,
            temperature=0.0,
            max_tokens=65536
        )
        
        analysis_result = parse_llm_unit_analysis(response_text)
        logger.info(f"Completed unit analysis for doc_id {doc_id} ({staged_data.ticker})")
        return analysis_result
        
    except Exception as e:
        logger.error(f"Error in LLM unit analysis for doc_id {doc_id}: {e}", exc_info=True)
        raise

def determine_review_requirement(analysis: Dict) -> Tuple[bool, List[Dict]]:
    """
    Determine if human review is required based on LLM analysis.
    """
    suspicious_figures = []
    for stmt_analysis in analysis['statement_analyses']:
        for figure in stmt_analysis['figures']:
            if figure['confidence'] == 'low':
                suspicious_figures.append({
                    'statement_type': stmt_analysis['statement_type'],
                    'standard_mapping': stmt_analysis.get('standard_mapping', 'unknown'),
                    'label': figure['label'],
                    'value': figure['value'],
                    'representation': figure['representation'],
                    'reasoning': figure.get('reasoning', 'Low confidence from LLM'),
                })
    
    filing_requires_review = analysis['filing_analysis'].get('requires_human_review', False)
    return len(suspicious_figures) > 0 or filing_requires_review, suspicious_figures

def apply_unit_normalization_to_data(staged_data: StagedNormalizedData, analysis: Dict) -> Dict:
    """
    Apply unit normalization analysis to create unit_normalized_data structure.
    """
    # This function needs to be implemented based on the final desired structure
    # For now, we'll just return the analysis to be stored.
    logger.info(f"Applying unit normalization for doc_id {staged_data.doc_id}")
    # This is a placeholder for the actual transformation logic
    return {'llm_unit_analysis': analysis}

def process_unit_normalization_discovery(doc_id: int, session) -> str:
    """
    Process a single document through unit normalization discovery phase.
    """
    try:
        staged_data = session.query(StagedNormalizedData).filter_by(doc_id=doc_id).one_or_none()
        if not staged_data:
            logger.error(f"Could not find staged data for doc_id {doc_id}")
            return 'PENDING'

        parsed_doc = session.get(ParsedDocument, doc_id)
        if not parsed_doc:
             logger.error(f"Could not find parsed document for doc_id {doc_id}")
             return 'PENDING'
        
        logger.info(f"Processing unit discovery for {staged_data.ticker} {staged_data.fiscal_date} (doc_id: {doc_id})")
        
        analysis = analyze_units_with_llm(doc_id, session)
        requires_review, suspicious_figures = determine_review_requirement(analysis)
        
        if requires_review:
            review_data = {
                'doc_id': doc_id, 'asset_id': parsed_doc.asset_id, 'ticker': staged_data.ticker,
                'fiscal_date': staged_data.fiscal_date, 'llm_analysis': analysis,
                'filing_data': {
                    'suspicious_figures': suspicious_figures,
                    'total_figures_analyzed': sum(len(stmt['figures']) for stmt in analysis['statement_analyses']),
                    'low_confidence_count': len(suspicious_figures),
                }
            }
            create_unit_review_record(review_data)
            logger.info(f"Queued {staged_data.ticker} for human review ({len(suspicious_figures)} suspicious figures)")
            return 'PENDING_REVIEW'
        else:
            unit_normalized_data = apply_unit_normalization_to_data(staged_data, analysis)
            
            # Use a dictionary to update JSONB
            updated_data = staged_data.normalized_data.copy()
            updated_data['unit_normalized_data'] = unit_normalized_data
            staged_data.normalized_data = updated_data
            
            session.commit()
            logger.info(f"Auto-approved {staged_data.ticker} (high confidence)")
            return 'AUTO_APPROVED'
            
    except Exception as e:
        logger.error(f"Error processing unit discovery for doc_id {doc_id}: {e}", exc_info=True)
        session.rollback()
        return 'PENDING'

def run_unit_normalizer_discovery(allow_llm: bool):
    """
    Run the discovery phase of unit normalization.
    """
    if not allow_llm:
        logger.info("LLM calls disabled, skipping unit normalizer discovery")
        return
    
    logger.info("=== Starting Unit Normalizer Discovery Phase ===")
    
    session = get_session()
    try:
        doc_ids = get_docs_pending_unit_normalization()
        if not doc_ids:
            logger.info("No documents pending unit normalization discovery.")
            return
        
        # Using temporary slice for testing
        doc_ids_to_process = doc_ids[:0]
        logger.info(f"Found {len(doc_ids_to_process)} documents for unit analysis (out of {len(doc_ids)} total)")
        
        status_updates = {}
        for doc_id in doc_ids_to_process:
            status = process_unit_normalization_discovery(doc_id, session)
            if status not in status_updates:
                status_updates[status] = []
            status_updates[status].append(doc_id)
        
        for status, doc_list in status_updates.items():
            if doc_list:
                mark_docs_unit_review_status(doc_list, status)
                logger.info(f"Marked {len(doc_list)} documents as {status}")
    finally:
        session.close()
    
    logger.info("=== Unit Normalizer Discovery Phase Complete ===")

def run_unit_normalizer_application():
    """
    Run the application phase of unit normalization.
    """
    logger.info("=== Starting Unit Normalizer Application Phase ===")
    session = get_session()
    try:
        approved_reviews = get_approved_unit_reviews()
        if not approved_reviews:
            logger.info("No approved unit reviews to process.")
            return
        
        logger.info(f"Found {len(approved_reviews)} approved reviews to process")
        # Placeholder for processing logic
    finally:
        session.close()
    logger.info("=== Unit Normalizer Application Phase Complete ===")