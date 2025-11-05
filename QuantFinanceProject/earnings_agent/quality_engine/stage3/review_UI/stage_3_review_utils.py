# /app/earnings_agent/quality_engine/stage3/review_UI/stage_3_review_utils.py

import sys
import logging
import yaml
import json
import re # <-- Add this import for the snake_case helper
from pathlib import Path
from typing import Dict, Any, List, Optional
import datetime
from decimal import Decimal, getcontext

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[4]
sys.path.append(str(project_root))

from sqlalchemy import select, update, func
from sqlalchemy.orm import Session
from earnings_agent.storage.database import get_session
from earnings_agent.storage.models import (
    LabelMappingCache, 
    QualityEngineRun, 
    IngestionJob, 
    JobAssetLink, 
    ParsedDocument,
    RawDataAsset
)

# --- Configuration ---
CALCULATIONS_PATH = project_root / "earnings_agent" / "playbooks" / "sebi" / "specs" / "banking_calculations.yml"
getcontext().prec = 18
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Helper Functions ---
def _memoize_rules(func):
    """A simple decorator to cache the calculation rules in memory."""
    cache = {}
    def wrapper():
        if 'rules' not in cache:
            cache['rules'] = func()
        return cache['rules']
    return wrapper

def _to_snake_case(name: str) -> str:
    """Converts a string to snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    return re.sub(r'[\s\W]+', '_', s2).strip('_')

# --- Data Loading and Verification Functions (Unchanged) ---
# ... (get_pending_stage_3_reviews, get_company_context, find_metric_in_content, etc. remain the same) ...
def get_pending_stage_3_reviews() -> List[Dict[str, Any]]:
    session = get_session()
    try:
        stmt = select(LabelMappingCache).where(LabelMappingCache.status == 'PENDING_REVIEW')
        pending_mappings = session.execute(stmt).scalars().all()
        review_items_map = {}
        for mapping in pending_mappings:
            run_stmt = (
                select(QualityEngineRun)
                .join(ParsedDocument, QualityEngineRun.doc_id == ParsedDocument.doc_id)
                .join(RawDataAsset, ParsedDocument.asset_id == RawDataAsset.asset_id)
                .join(JobAssetLink, RawDataAsset.asset_id == JobAssetLink.asset_id)
                .join(IngestionJob, JobAssetLink.job_id == IngestionJob.job_id)
                .where(
                    IngestionJob.ticker == mapping.ticker,
                    QualityEngineRun.stage_3_status == 'AWAITING_APPROVAL'
                )
                .order_by(QualityEngineRun.created_at.desc()).limit(1)
            )
            run = session.execute(run_stmt).scalar_one_or_none()
            if run:
                if run.run_id not in review_items_map:
                    company_info = get_company_context(session, run.doc_id)
                    review_items_map[run.run_id] = {
                        'run_id': run.run_id, 'doc_id': run.doc_id, 'company': company_info['ticker'],
                        'period': f"Q{company_info['quarter']} FY{company_info['fiscal_year']}",
                        'working_content': run.working_content, 'pending_mappings': []
                    }
                metric_details = find_metric_in_content(run.working_content, mapping.raw_label, mapping.statement_key)
                review_items_map[run.run_id]['pending_mappings'].append({
                    'cache_id': mapping.id, 'raw_label': mapping.raw_label,
                    'value': metric_details.get('value') if metric_details else None,
                    'statement_key': mapping.statement_key,
                    'suggested_mapping_type': mapping.mapping_type,
                    'suggested_normalized_label': mapping.normalized_label
                })
        return list(review_items_map.values())
    finally:
        session.close()

def get_company_context(session: Session, doc_id: int) -> Dict[str, Any]:
    stmt = (
        select(IngestionJob.ticker, IngestionJob.fiscal_year, IngestionJob.quarter)
        .join(JobAssetLink, JobAssetLink.job_id == IngestionJob.job_id)
        .join(ParsedDocument, ParsedDocument.asset_id == JobAssetLink.asset_id)
        .where(ParsedDocument.doc_id == doc_id).limit(1)
    )
    result = session.execute(stmt).first()
    return {'ticker': result.ticker, 'fiscal_year': result.fiscal_year, 'quarter': result.quarter} if result else {'ticker': 'Unknown', 'fiscal_year': 0, 'quarter': 0}

def find_metric_in_content(working_content: Dict, raw_label: str, statement_key: str) -> Optional[Dict]:
    for source_list_name in ["unmapped_from_pdf", "company_specific_kpis"]:
        source_list = working_content.get("llm_call_2_extraction", {}).get(statement_key, {}).get(source_list_name, [])
        for metric in source_list:
            if metric.get('raw_label') == raw_label:
                return metric
    return None

def get_null_metrics_for_statement(working_content: Dict, statement_key: str) -> Dict[str, str]:
    figures = working_content.get("llm_call_2_extraction", {}).get(statement_key, {}).get("normalized_figures", [])
    null_metrics = {f"{fig.get('playbook_id')} (null)": fig.get('playbook_id') for fig in figures if fig.get('value') is None}
    return null_metrics

@_memoize_rules
def load_calculation_rules() -> Dict[str, Any]:
    with open(CALCULATIONS_PATH, 'r') as f:
        rules = yaml.safe_load(f)
    child_to_parent_map = {}
    for statement_key, statement_rules in rules.items():
        for rule in statement_rules:
            parent_id = rule['parent']
            children_data = rule.get('children', {})
            children_dict = {k: v for item in children_data for k, v in item.items()} if isinstance(children_data, list) else children_data
            for child_id in children_dict:
                child_to_parent_map[child_id] = {'parent': parent_id, 'rule': rule, 'statement_key': statement_key}
    return child_to_parent_map

def verify_single_rule(transformed_content: Dict, playbook_id_to_check: str) -> bool:
    child_map = load_calculation_rules()
    if playbook_id_to_check not in child_map: return True
    rule_info = child_map[playbook_id_to_check]
    parent_id, rule = rule_info['parent'], rule_info['rule']
    statement_name_map = {'pnl': 'pnl', 'balance_sheet': 'balance_sheet', 'cash_flow_indirect': 'cash_flow'}
    inferred_stmt_key_part = next((val for key, val in statement_name_map.items() if key in rule_info['statement_key']), None)
    if not inferred_stmt_key_part: return True
    actual_statement_key = next((key for key in transformed_content.get("llm_call_2_extraction", {}).keys() if inferred_stmt_key_part in key), None)
    if not actual_statement_key: return True
    figures = transformed_content.get("llm_call_2_extraction", {}).get(actual_statement_key, {}).get("normalized_figures", [])
    parent_figure = next((f for f in figures if f.get("playbook_id") == parent_id), None)
    if not parent_figure or parent_figure.get("value") is None: return True
    children_data = rule.get('children', {})
    children_dict = {k: v for item in children_data for k, v in item.items()} if isinstance(children_data, list) else children_data
    recomputed_sum = sum(Decimal(str(next((f.get("value") for f in figures if f.get("playbook_id") == child_id), 0.0) or 0.0)) * Decimal(str(multiplier)) for child_id, multiplier in children_dict.items())
    original_parent_val = Decimal(str(parent_figure.get("value")))
    tolerance = abs(original_parent_val * Decimal('0.001'))
    return abs(recomputed_sum - original_parent_val) <= tolerance

# --- Action Handler Functions (Backend for UI buttons) ---

def handle_approval(run_id: int, cache_id: int, approved_mapping: Dict, user: str) -> Dict[str, Any]:
    session = get_session()
    try:
        session.execute(update(LabelMappingCache).where(LabelMappingCache.id == cache_id).values(
            status='APPROVED', normalized_label=approved_mapping['target_playbook_id'],
            mapping_type='standard', approved_by=user, approved_at=func.now()
        ))
        session.execute(update(QualityEngineRun).where(QualityEngineRun.run_id == run_id).values(
            stage_3_status='PENDING', failure_reason='Re-queued after human approval.'
        ))
        session.commit()
        return {"status": "success", "message": "Mapping approved. Document has been re-queued for processing."}
    except Exception as e:
        session.rollback()
        logging.error(f"Error during approval for run {run_id}: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


def handle_rejection(run_id: int, cache_id: int, user: str) -> Dict[str, Any]:
    session = get_session()
    try:
        cache_item = session.get(LabelMappingCache, cache_id)
        if not cache_item: raise ValueError("Cache item not found.")
        
        # --- BUG FIX STARTS HERE ---
        # When rejecting, we are confirming it's a KPI. We must generate
        # the correct snake_case name from the raw_label.
        new_normalized_label = _to_snake_case(cache_item.raw_label)
        
        cache_item.status = 'APPROVED'
        cache_item.mapping_type = 'company_specific'
        cache_item.normalized_label = new_normalized_label # Update the label to the KPI name
        cache_item.approved_by = user
        cache_item.approved_at = func.now()
        # --- BUG FIX ENDS HERE ---
        
        ticker = cache_item.ticker
        
        other_pending_count = session.scalar(
            select(func.count(LabelMappingCache.id))
            .where(
                LabelMappingCache.ticker == ticker,
                LabelMappingCache.status == 'PENDING_REVIEW',
                LabelMappingCache.id != cache_id
            )
        )

        if other_pending_count == 0:
             run = session.get(QualityEngineRun, run_id)
             if run and run.stage_3_status == 'AWAITING_APPROVAL':
                run.stage_3_status = 'PENDING'

        session.commit()
        return {"status": "success", "message": "Suggestion rejected. Saved as a company-specific KPI."}
    except Exception as e:
        session.rollback()
        logging.error(f"Error during rejection for run {run_id}: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()