# /app/earnings_agent/quality_engine/review_utils.py

import json
from typing import Dict, List, Any, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from earnings_agent.storage.database import get_session, update_quality_run
from earnings_agent.storage.models import QualityEngineRun, IngestionJob, JobAssetLink
from earnings_agent.quality_engine.expectations_utils import load_expectations
from earnings_agent.quality_engine.playbook_utils import load_playbook_leaf_nodes

def get_pending_reviews() -> List[Dict[str, Any]]:
    """Get all documents pending human review across all Stage 1 error types."""
    session = get_session()
    try:
        error_statuses = ['COMPLETENESS_ERROR', 'ORDER_ERROR', 'METADATA_ERROR', 'AI_DISAGREEMENT_ERROR']
        
        stmt = (
            select(QualityEngineRun)
            .where(QualityEngineRun.stage_1_status.in_(error_statuses))
            .options(joinedload(QualityEngineRun.parsed_document))
            .order_by(QualityEngineRun.created_at.desc())
        )
        
        runs = session.execute(stmt).scalars().all()
        
        review_items = []
        for run in runs:
            # Get company context
            company_info = get_company_context(run.parsed_document.asset_id)
            
            # Parse error details from run_history
            error_details = parse_error_details(run)
            
            review_items.append({
                'run_id': run.run_id,
                'doc_id': run.doc_id,
                'error_type': run.stage_1_status,
                'company': company_info['ticker'],
                'quarter': company_info['quarter'],
                'fiscal_year': company_info['fiscal_year'],
                'error_details': error_details,
                'working_content': run.working_content,
                'failure_reason': run.failure_reason
            })
        
        return review_items
    finally:
        session.close()

def get_company_context(asset_id: int) -> Dict[str, Any]:
    """Get company and filing context for an asset_id."""
    session = get_session()
    try:
        stmt = (
            select(IngestionJob.ticker, IngestionJob.fiscal_year, IngestionJob.quarter)
            .join(JobAssetLink, JobAssetLink.job_id == IngestionJob.job_id)
            .filter(JobAssetLink.asset_id == asset_id)
            .limit(1)
        )
        result = session.execute(stmt).first()
        if result:
            return {
                'ticker': result.ticker,
                'fiscal_year': result.fiscal_year,
                'quarter': result.quarter
            }
        return {'ticker': 'Unknown', 'fiscal_year': 0, 'quarter': 0}
    finally:
        session.close()

def parse_error_details(run: QualityEngineRun) -> Dict[str, Any]:
    """Parse error details from run_history based on error type."""
    if not run.run_history:
        return {}
    
    error_type = run.stage_1_status
    
    # Find the last error entry in run_history
    for entry in reversed(run.run_history):
        if entry.get('status') == 'FAILURE':
            details = entry.get('details', {})
            
            if error_type == 'COMPLETENESS_ERROR':
                return {
                    'missing_ids': details.get('missing_ids', []),
                    'statement': extract_statement_from_failure_reason(run.failure_reason)
                }
            elif error_type == 'METADATA_ERROR':
                return {
                    'errors': details.get('errors', []),
                    'affected_statements': parse_metadata_errors(details.get('errors', []))
                }
            elif error_type == 'AI_DISAGREEMENT_ERROR':
                return {
                    'disagreements': details.get('disagreements', ''),
                    'affected_metrics': parse_ai_disagreements(details.get('disagreements', ''))
                }
            elif error_type == 'ORDER_ERROR':
                return {
                    'reason': details.get('reason', ''),
                    'statement': extract_statement_from_failure_reason(run.failure_reason)
                }
    
    return {}

def extract_statement_from_failure_reason(failure_reason: str) -> str:
    """Extract statement type from failure reason."""
    if not failure_reason:
        return 'Unknown'
    
    if 'standalone_pnl' in failure_reason:
        return 'standalone_pnl'
    elif 'consolidated_pnl' in failure_reason:
        return 'consolidated_pnl'
    elif 'standalone_balance_sheet' in failure_reason:
        return 'standalone_balance_sheet'
    elif 'consolidated_balance_sheet' in failure_reason:
        return 'consolidated_balance_sheet'
    elif 'standalone_cash_flow' in failure_reason:
        return 'standalone_cash_flow'
    elif 'consolidated_cash_flow' in failure_reason:
        return 'consolidated_cash_flow'
    
    return 'Unknown'

def parse_metadata_errors(errors: List[str]) -> List[Dict[str, str]]:
    """Parse metadata error strings to extract statement and playbook_id."""
    parsed_errors = []
    for error in errors:
        # Format: "Statement 'standalone_pnl': playbook_id [metric_id]: error details"
        if 'Statement' in error and 'playbook_id' in error:
            parts = error.split(': playbook_id [')
            if len(parts) >= 2:
                statement = parts[0].replace("Statement '", "").replace("'", "")
                metric_part = parts[1].split(']:')
                if metric_part:
                    playbook_id = metric_part[0]
                    error_detail = metric_part[1].strip() if len(metric_part) > 1 else ''
                    parsed_errors.append({
                        'statement': statement,
                        'playbook_id': playbook_id,
                        'error': error_detail
                    })
    return parsed_errors

def parse_ai_disagreements(disagreements: str) -> List[Dict[str, str]]:
    """Parse AI disagreement strings to extract metrics and values."""
    # Expected format: "standalone_pnl: other_income: LLM1=19074.5625, LLM2=18166.25"
    parsed = []
    
    if not disagreements:
        return parsed
    
    # Split by '; ' for multiple disagreements
    if '; ' in disagreements:
        parts = disagreements.split('; ')
    else:
        parts = [disagreements]
    
    for part in parts:
        part = part.strip()
        if ': ' in part and 'LLM1=' in part and 'LLM2=' in part:
            try:
                # Split the statement and the rest
                colon_parts = part.split(': ')
                if len(colon_parts) >= 3:  # statement: metric: LLM1=..., LLM2=...
                    statement = colon_parts[0].strip()
                    metric_id = colon_parts[1].strip()
                    values_part = ': '.join(colon_parts[2:])  # Rejoin in case there are more colons
                elif len(colon_parts) == 2:  # metric: LLM1=..., LLM2=...
                    statement = "unknown"  # Fallback
                    metric_id = colon_parts[0].strip()
                    values_part = colon_parts[1].strip()
                else:
                    continue
                
                # Extract LLM values
                if 'LLM1=' in values_part and 'LLM2=' in values_part:
                    llm1_part = values_part.split('LLM1=')[1].split(',')[0].strip()
                    llm2_part = values_part.split('LLM2=')[1].strip()
                    
                    parsed.append({
                        'statement': statement,
                        'playbook_id': metric_id,
                        'llm1_value': llm1_part,
                        'llm2_value': llm2_part
                    })
            except Exception as e:
                print(f"Error parsing disagreement part '{part}': {e}")
                continue
    
    return parsed

def get_metric_from_working_content(working_content: Dict, statement_type: str, playbook_id: str) -> Optional[Dict]:
    """Extract a specific metric from working_content."""
    if not working_content or 'llm_call_2_extraction' not in working_content:
        return None
    
    extraction_data = working_content['llm_call_2_extraction']
    if statement_type not in extraction_data:
        return None
    
    normalized_figures = extraction_data[statement_type].get('normalized_figures', [])
    for figure in normalized_figures:
        if figure.get('playbook_id') == playbook_id:
            return figure
    
    return None

def get_metadata_options() -> Dict[str, List[str]]:
    """Get valid options for metadata fields."""
    from earnings_agent.parsing.pdf.pdf_extractor_config import (
        RepresentationType, CurrencyType, UnitScaleType, RatioContextType
    )
    
    return {
        'representation': [e.value for e in RepresentationType],
        'currency_context': [e.value for e in CurrencyType] + [None],
        'unit_scale': [e.value for e in UnitScaleType] + [None],
        'ratio_context': [e.value for e in RatioContextType] + [None]
    }

def add_missing_metric_to_content(
    working_content: Dict, 
    statement_type: str, 
    playbook_id: str, 
    metric_data: Dict
) -> Dict:
    """Add a missing metric to the working_content in the correct position."""
    if 'llm_call_2_extraction' not in working_content:
        return working_content
    
    if statement_type not in working_content['llm_call_2_extraction']:
        return working_content
    
    # Get playbook order to insert in correct position
    playbook_leaf_nodes = load_playbook_leaf_nodes()
    
    if 'pnl' in statement_type:
        expected_order = playbook_leaf_nodes.get('pnl', [])
    elif 'balance_sheet' in statement_type:
        expected_order = playbook_leaf_nodes.get('balance_sheet', [])
    elif 'cash_flow' in statement_type:
        expected_order = playbook_leaf_nodes.get('cash_flow', [])
    else:
        expected_order = []
    
    normalized_figures = working_content['llm_call_2_extraction'][statement_type]['normalized_figures']
    
    # Find correct insertion position based on playbook order
    insert_index = len(normalized_figures)
    for i, expected_id in enumerate(expected_order):
        if expected_id == playbook_id:
            # Find where this should be inserted
            for j, existing_figure in enumerate(normalized_figures):
                existing_id = existing_figure.get('playbook_id')
                if existing_id and expected_order.index(existing_id) > i:
                    insert_index = j
                    break
            break
    
    # Insert the new metric
    normalized_figures.insert(insert_index, metric_data)
    
    return working_content

def update_metric_in_content(
    working_content: Dict, 
    statement_type: str, 
    playbook_id: str, 
    updated_data: Dict
) -> Dict:
    """Update an existing metric in working_content."""
    if 'llm_call_2_extraction' not in working_content:
        return working_content
    
    if statement_type not in working_content['llm_call_2_extraction']:
        return working_content
    
    normalized_figures = working_content['llm_call_2_extraction'][statement_type]['normalized_figures']
    
    for i, figure in enumerate(normalized_figures):
        if figure.get('playbook_id') == playbook_id:
            # Update the figure with new data
            figure.update(updated_data)
            break
    
    return working_content

def approve_stage_1_fix(run_id: int, updated_content: Dict, new_status: str):
    """Apply the human fixes and update the run status."""
    import datetime
    from datetime import timezone
    
    history_entry = {
        "step": "human_review",
        "status": "APPROVED",
        "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
        "details": {"message": "Human review completed and approved"}
    }
    
    # Get current run to append to history
    session = get_session()
    try:
        run = session.get(QualityEngineRun, run_id)
        if run:
            current_history = run.run_history or []
            current_history.append(history_entry)
            
            update_quality_run(run_id, {
                "stage_1_status": new_status,
                "working_content": updated_content,
                "run_history": current_history,
                "failure_reason": None  # Clear failure reason
            })
            return True
    finally:
        session.close()
    
    return False