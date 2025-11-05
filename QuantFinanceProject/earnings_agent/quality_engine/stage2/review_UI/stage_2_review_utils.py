# /app/earnings_agent/quality_engine/stage2/review_UI/stage_2_review_utils.py

import json
import logging
import datetime
from typing import Dict, List, Any, Optional, Tuple
from datetime import timezone
from sqlalchemy import select
from sqlalchemy.orm import joinedload

# --- MODIFIED: Import new functions and models ---
from earnings_agent.storage.database import get_session, update_quality_run, save_rule_variant
from earnings_agent.storage.models import QualityEngineRun, IngestionJob, JobAssetLink, ParsedDocument
from earnings_agent.quality_engine.stage2.stage_2a_calculations import run_calculation_validation
from earnings_agent.quality_engine.stage2.stage_2b_reconciliation import check_reconciliation_possibilities, SuggestionEngine

def get_pending_stage_2_reviews() -> List[Dict[str, Any]]:
    """Get all documents pending human review for Stage 2 calculation issues."""
    session = get_session()
    try:
        stmt = (
            select(QualityEngineRun)
            .where(QualityEngineRun.stage_2_status == 'CALCULATION_MISMATCH')
            .options(joinedload(QualityEngineRun.parsed_document))
            .order_by(QualityEngineRun.created_at.desc())
        )
        
        runs = session.execute(stmt).scalars().all()
        
        review_items = []
        for run in runs:
            company_info = get_company_context(run.parsed_document.asset_id)
            review_items.append({
                'run_id': run.run_id,
                'doc_id': run.doc_id,
                'company': company_info['ticker'],
                'quarter': company_info['quarter'],
                'fiscal_year': company_info['fiscal_year'],
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

def get_live_calculation_results(working_content: Dict[str, Any], ticker: str) -> Dict[str, Any]:
    """
    Get fresh calculation results, now including intelligent suggestions for Tier 3 failures.
    """
    try:
        # Step 1: Run calculation validation using the ticker-aware resolver
        calculation_results = run_calculation_validation(working_content, ticker)
        
        # Step 2: Run standard reconciliation (for Tier 1)
        enhanced_results = check_reconciliation_possibilities(calculation_results, working_content)

        # Step 3: Run Suggestion Engine for remaining Tier 3 failures
        extraction_data = working_content.get('llm_call_2_extraction', {})
        for statement_key, statement_results in enhanced_results.get('statement_results', {}).items():
            statement_data = extraction_data.get(statement_key, {})
            suggestion_engine = SuggestionEngine(statement_data)
            
            for calc in statement_results.get('calculations', []):
                # If a calculation is still Tier 3, try to find an intelligent suggestion
                if calc.get('reconciliation_tier') == 3:
                    suggestion = suggestion_engine.find_best_suggestion(calc)
                    if suggestion['has_suggestions']:
                        # Upgrade it to Tier 2 and add the suggestion
                        calc['reconciliation_tier'] = 2
                        calc['reconciliation_status'] = 'suggestions_available'
                        calc['suggestion_candidates'] = suggestion['suggestion_candidates']
        
        return enhanced_results
    except Exception as e:
        logging.error(f"Error in get_live_calculation_results: {e}", exc_info=True)
        return {'error': f"Failed to generate calculation results: {str(e)}", 'statement_results': {}}

def save_correction_and_re_run(run_id: int, ticker: str, rule_id: str, new_formula: List[Dict], user: str = "human_reviewer"):
    """Saves the corrected rule to the database and re-runs Stage 2 validation."""
    session = get_session()
    try:
        # 1. Save the new rule variant to the database
        variant_data = {
            'issuer_ticker': ticker,
            'parent_playbook_id': rule_id,
            'variant_definition': {'children': new_formula},
            'created_by': user
        }
        save_rule_variant(variant_data)

        # 2. Re-run the Stage 2 process for immediate feedback
        run = session.get(QualityEngineRun, run_id)
        if not run:
            raise ValueError(f"Run ID {run_id} not found")
        
        live_results = get_live_calculation_results(run.working_content, ticker)
        
        summary = live_results.get('reconciliation_summary', {})
        if summary.get('requires_human_review_statements', 1) == 0:
            # It passes! Update status to PASSED and add history
            history_entry = {
                "step": "2_human_review", "status": "CORRECTED_AND_PASSED",
                "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
                "details": {"message": f"Rule '{rule_id}' corrected by {user}. Document now passes."}
            }
            run.run_history = (run.run_history or []) + [history_entry]
            run.stage_2_status = "PASSED"
            session.commit()
            return {"status": "success", "message": f"Correction saved and document for {ticker} now passes Stage 2!"}
        else:
            # It still fails, but the correction is saved for next time
            history_entry = {
                "step": "2_human_review", "status": "CORRECTION_SAVED",
                "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
                "details": {"message": f"Rule '{rule_id}' corrected by {user}. Document still requires further review."}
            }
            run.run_history = (run.run_history or []) + [history_entry]
            session.commit()
            return {"status": "success", "message": f"Correction saved for {ticker}. The document still requires further review."}

    except Exception as e:
        session.rollback()
        logging.error(f"Error saving correction for run_id {run_id}: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()

def format_currency_value(value: Optional[float]) -> str:
    """Format a currency value for display."""
    if value is None:
        return "null"
    return f"{value:,.2f}"

def get_calculation_status_summary(statement_results: Dict[str, Any]) -> Dict[str, Any]:
    """Get a summary of calculation status for a statement using three-tier logic."""
    total = statement_results.get('total_calculations', 0)
    passed = statement_results.get('passed', 0)
    
    reconciliation_summary = statement_results.get('reconciliation_summary', {})
    tier_1_auto = reconciliation_summary.get('tier_1_auto_reconciled', 0)
    
    # Re-calculate tier 2/3 counts based on live suggestions from the engine
    tier_2_suggestions = 0
    tier_3_no_reconciliation = 0
    for calc in statement_results.get('calculations', []):
        if calc.get('reconciliation_tier') == 2:
            tier_2_suggestions += 1
        elif calc.get('reconciliation_tier') == 3:
            tier_3_no_reconciliation += 1

    needs_human_review = tier_2_suggestions + tier_3_no_reconciliation
    passable = needs_human_review == 0

    return {
        'total': total,
        'passed': passed,
        'tier_1_auto_reconciled': tier_1_auto,
        'tier_2_suggestions': tier_2_suggestions,
        'tier_3_no_reconciliation': tier_3_no_reconciliation,
        'effective_passed': passed + tier_1_auto,
        'needs_human_review': needs_human_review,
        'passable': passable
    }

def format_calculation_display(calculation: Dict[str, Any]) -> Dict[str, str]:
    """Format calculation data for nice display in the UI with three-tier reconciliation."""
    reconciliation_tier = calculation.get('reconciliation_tier', 0)
    
    return {
        'rule_id': calculation.get('rule_id', 'Unknown'),
        'parent_value': format_currency_value(calculation.get('parent_value')),
        'calculated_value': format_currency_value(calculation.get('calculated_value')),
        'variance': format_currency_value(calculation.get('variance')),
        'missing_amount': format_currency_value(calculation.get('missing_amount')),
        'status': calculation.get('status', 'Unknown'),
        'missing_children': calculation.get('missing_children', []),
        'reconciliation_tier': reconciliation_tier,
        'suggestion_candidates': calculation.get('suggestion_candidates', [])
    }

def format_reconciliation_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Format reconciliation candidate for display, handling both individual metrics and combinations."""
    is_combination = candidate.get('is_combination', False)
    combination = candidate.get('combination', [])
    
    if is_combination:
        labels = [metric.get('raw_label', 'Unknown') for metric in combination]
        values = [format_currency_value(metric.get('value')) for metric in combination]
        return {
            'is_combination': True,
            'combination_size': len(combination),
            'combination_labels': labels,
            'combination_values': values,
            'combination_sum': format_currency_value(candidate.get('combination_sum')),
            'percentage_diff': f"{candidate.get('percentage_diff', 0):.2f}%"
        }
    else: # Single metric
        metric = combination[0] if combination else {}
        return {
            'is_combination': False,
            'raw_label': metric.get('raw_label', 'Unknown'),
            'value': format_currency_value(metric.get('value')),
            'percentage_diff': f"{candidate.get('percentage_diff', 0):.2f}%"
        }

def approve_stage_2_calculation_review(run_id: int, approval_notes: str) -> bool:
    """
    Approve Stage 2 calculation review and update the run status.
    This action implies that all suggestions, if any, are accepted as valid.
    """
    session = get_session()
    try:
        run = session.get(QualityEngineRun, run_id)
        if not run: return False
        
        history_entry = {
            "step": "2_human_review", "status": "APPROVED",
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
            "details": {"message": "Human review approved the document as is.", "notes": approval_notes}
        }
        run.run_history = (run.run_history or []) + [history_entry]
        run.stage_2_status = "PASSED"
        run.stage_2_version = "1.0" # Use a consistent version for human-approved runs
        run.failure_reason = None
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logging.error(f"Error approving run_id {run_id}: {e}", exc_info=True)
        return False
    finally:
        session.close()

def reject_stage_2_calculation_review(run_id: int, rejection_reason: str) -> bool:
    """
    Reject Stage 2 calculation review with a reason.
    Keeps the document in review queue with updated failure reason.
    """
    session = get_session()
    try:
        run = session.get(QualityEngineRun, run_id)
        if not run: return False

        history_entry = {
            "step": "2_human_review", "status": "REJECTED",
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
            "details": {"message": "Human review rejected the document.", "reason": rejection_reason}
        }
        run.run_history = (run.run_history or []) + [history_entry]
        run.failure_reason = f"Human rejected: {rejection_reason}"
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logging.error(f"Error rejecting run_id {run_id}: {e}", exc_info=True)
        return False
    finally:
        session.close()