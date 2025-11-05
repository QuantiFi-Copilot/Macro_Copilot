# /app/earnings_agent/quality_engine/stage2/stage_2b_reconciliation.py

import logging
from typing import Dict, List, Any, Optional
from itertools import combinations

# --- UNCHANGED: Tolerance check functions ---
def auto_reconciliation_match(val1: float, val2: float, tolerance_percent: float = 0.01) -> bool:
    if val1 == 0 and val2 == 0: return True
    if val1 == 0 or val2 == 0: return False
    percentage_diff = abs(val1 - val2) / max(abs(val1), abs(val2)) * 100
    return percentage_diff <= tolerance_percent

def suggestion_match(val1: float, val2: float, tolerance_percent: float = 0.5) -> bool:
    if val1 == 0 and val2 == 0: return True
    if val1 == 0 or val2 == 0: return False
    percentage_diff = abs(val1 - val2) / max(abs(val1), abs(val2)) * 100
    return percentage_diff <= tolerance_percent

# --- MODIFIED: The core combination logic is now more generic ---
def find_combination_matches(missing_amount: float, candidate_metrics: List[Dict[str, Any]], max_combination_size: int = 3) -> Dict[str, Any]:
    auto_reconcile_candidates = []
    suggestion_candidates = []
    valid_metrics = [m for m in candidate_metrics if m.get('value') is not None]
    if not valid_metrics:
        return {'auto_reconcile_candidates': [], 'suggestion_candidates': [], 'has_auto_reconciliation': False, 'has_suggestions': False}

    for combination_size in range(1, min(max_combination_size + 1, len(valid_metrics) + 1)):
        for combo in combinations(valid_metrics, combination_size):
            combo_sum = sum(metric.get('value', 0) for metric in combo)
            if abs(missing_amount) == 0:
                if abs(combo_sum) == 0: percentage_diff = 0.0
                else: continue
            else:
                percentage_diff = abs(abs(missing_amount) - abs(combo_sum)) / abs(missing_amount) * 100

            candidate = {'combination': list(combo), 'combination_size': combination_size, 'missing_amount': missing_amount, 'combination_sum': combo_sum, 'percentage_diff': percentage_diff, 'is_combination': combination_size > 1}
            
            if auto_reconciliation_match(abs(missing_amount), abs(combo_sum)):
                auto_reconcile_candidates.append(candidate)
                # Early exit for auto-reconciliation remains for performance
                return {'auto_reconcile_candidates': auto_reconcile_candidates, 'suggestion_candidates': [], 'has_auto_reconciliation': True, 'has_suggestions': False}
            
            elif suggestion_match(abs(missing_amount), abs(combo_sum)):
                candidate['confidence_score'] = 100 - percentage_diff
                suggestion_candidates.append(candidate)
    
    suggestion_candidates.sort(key=lambda x: x['percentage_diff'])
    return {'auto_reconcile_candidates': auto_reconcile_candidates, 'suggestion_candidates': suggestion_candidates[:5], 'has_auto_reconciliation': len(auto_reconcile_candidates) > 0, 'has_suggestions': len(suggestion_candidates) > 0}

# --- NEW: Suggestion Engine Class ---
class SuggestionEngine:
    def __init__(self, statement_data: Dict[str, Any]):
        self.unmapped_metrics = statement_data.get('unmapped_from_pdf', [])
        self.normalized_figures = statement_data.get('normalized_figures', [])
    
    def find_best_suggestion(self, failed_calculation: Dict[str, Any]) -> Dict[str, Any]:
        """
        Finds the best suggestion for a failed calculation by expanding the search to include
        both unmapped and other mapped metrics.
        """
        missing_amount = failed_calculation.get('missing_amount')
        if missing_amount is None:
            return {'has_suggestions': False, 'suggestion_candidates': []}

        # Get playbook IDs of metrics already used in the failed rule
        original_child_ids = {item['playbook_id'] for item in failed_calculation.get('calculation_breakdown', [])}
        
        # Create a comprehensive list of candidates
        all_candidates = list(self.unmapped_metrics)
        for fig in self.normalized_figures:
            # Add mapped metrics that were NOT part of the original failed calculation
            if fig.get('playbook_id') not in original_child_ids and fig.get('playbook_id') != failed_calculation.get('rule_id'):
                all_candidates.append(fig)
        
        # Use the combination logic on this expanded set of candidates
        # We only care about suggestions here, not auto-reconciliation
        results = find_combination_matches(missing_amount, all_candidates)
        return {'has_suggestions': results['has_suggestions'], 'suggestion_candidates': results['suggestion_candidates']}

def check_reconciliation_possibilities(calculation_results: Dict[str, Any], working_content: Dict[str, Any]) -> Dict[str, Any]:
    extraction_data = working_content.get('llm_call_2_extraction', {})
    enhanced_results = calculation_results.copy()
    
    for statement_key, statement_results in enhanced_results['statement_results'].items():
        if statement_key not in extraction_data: 
            continue
        
        statement_data = extraction_data[statement_key]
        unmapped_metrics = statement_data.get('unmapped_from_pdf', [])
        suggestion_engine = SuggestionEngine(statement_data)  # Instantiate the engine

        # Initialize reconciliation summary (backwards compatible)
        statement_results['reconciliation_summary'] = {
            'total_failed': 0, 
            'tier_1_auto_reconciled': 0, 
            'tier_2_suggested': 0, 
            'tier_3_no_reconciliation': 0
        }

        for calculation in statement_results.get('calculations', []):
            # Pre-compute the mapped-only sum (before any unmapped use) for audit if we can
            breakdown = calculation.get('calculation_breakdown', [])
            try:
                mapped_only_sum = 0.0
                for item in breakdown:
                    val = item.get('value', 0) or 0
                    mul = item.get('multiplier', 1) or 1
                    mapped_only_sum += (val * mul)
            except Exception:
                mapped_only_sum = calculation.get('calculated_sum') or calculation.get('recomputed_children_sum')

            if calculation.get('status') == 'FAILED' and calculation.get('missing_amount') is not None:
                missing_amount = calculation['missing_amount']
                
                # Tier 1: Auto-Reconciliation (strictly UNMAPPED metrics)
                auto_recon_analysis = find_combination_matches(missing_amount, unmapped_metrics)
                
                if auto_recon_analysis['has_auto_reconciliation']:
                    calculation['reconciliation_tier'] = 1
                    calculation['reconciliation_status'] = 'auto_reconciled'
                    calculation['auto_reconciled'] = True

                    # Keep original candidate (back-compat)
                    top_candidate = auto_recon_analysis['auto_reconcile_candidates'][0]
                    calculation['auto_reconcile_candidate'] = top_candidate

                    # NEW: normalized components actually used
                    components_used = []
                    for m in top_candidate.get('combination', []):
                        components_used.append({
                            "raw_label": m.get('raw_label') or m.get('label'),
                            "value": m.get('value'),
                            "source_statement_key": statement_key,
                            "source_index": m.get('source_index'),
                            "unit_scale": m.get('unit_scale'),
                            "currency_context": m.get('currency_context'),
                            "confidence": m.get('confidence')
                        })

                    combination_sum = top_candidate.get('combination_sum', 0.0) or 0.0
                    calculation['recomputed_children_sum_before'] = mapped_only_sum
                    calculation['recomputed_children_sum_after'] = (mapped_only_sum if mapped_only_sum is not None else 0.0) + combination_sum
                    calculation['missing_amount'] = missing_amount
                    calculation['auto_reconcile_components_used'] = components_used
                    calculation['delta_after'] = abs(abs(missing_amount) - abs(combination_sum))

                    statement_results['reconciliation_summary']['tier_1_auto_reconciled'] += 1

                else:
                    # Tier 2: Suggestions (for human review)
                    suggestion_analysis = suggestion_engine.find_best_suggestion(calculation)
                    if suggestion_analysis['has_suggestions']:
                        calculation['reconciliation_tier'] = 2
                        calculation['reconciliation_status'] = 'suggestions_available'
                        calculation['suggestion_candidates'] = suggestion_analysis['suggestion_candidates']
                        statement_results['reconciliation_summary']['tier_2_suggested'] += 1
                    else:
                        # Tier 3: No reconciliation possible
                        calculation['reconciliation_tier'] = 3
                        calculation['reconciliation_status'] = 'no_reconciliation_possible'
                        statement_results['reconciliation_summary']['tier_3_no_reconciliation'] += 1
                
                statement_results['reconciliation_summary']['total_failed'] += 1
            else:
                calculation['reconciliation_tier'] = 0  # unchanged behavior

        # === Minimal fix: recompute failures AFTER auto-reconciliation ===
        failed_after = sum(
            1 for c in statement_results.get('calculations', [])
            if c.get('status') == 'FAILED' and not c.get('auto_reconciled')
        )
        statement_results['failed_after_reconciliation'] = failed_after

        # Preserve old fields but base pass flag on the recomputed number
        # (keeps backward compatibility for consumers reading 'failed')
        statement_results['passable_with_reconciliation'] = (failed_after == 0)
        statement_results['requires_human_review'] = (failed_after > 0)
        # Optional sync of 'failed' so legacy readers don’t see a stale number:
        statement_results['failed'] = failed_after

    total_statements = enhanced_results.get('total_statements', 0)
    passable_statements = sum(
        1 for res in enhanced_results['statement_results'].values()
        if res.get('passable_with_reconciliation', False)
    )
    enhanced_results['reconciliation_summary'] = {
        'total_statements': total_statements, 
        'auto_passable_statements': passable_statements, 
        'requires_human_review_statements': total_statements - passable_statements
    }
    
    return enhanced_results