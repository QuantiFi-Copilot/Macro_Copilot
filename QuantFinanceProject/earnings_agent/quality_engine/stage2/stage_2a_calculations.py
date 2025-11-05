# /app/earnings_agent/quality_engine/stage2/stage_2a_calculations.py

import yaml
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

# --- NEW: Import database functions ---
from earnings_agent.storage.database import get_session, get_rule_variant

# --- Path Setup ---
project_root = Path(__file__).resolve().parents[3]
BANKING_CALCULATIONS_PATH = project_root / "earnings_agent" / "playbooks" / "sebi" / "calculations" / "banking_calculations.yml"

# --- NEW: Playbook Resolver ---
def get_calculation_rules_for_run(ticker: str, statement_type: str) -> List[Dict[str, Any]]:
    """
    The Playbook Resolver. It loads default rules and merges any issuer-specific overrides from the database.
    """
    # 1. Load default rules from YAML
    try:
        with open(BANKING_CALCULATIONS_PATH, 'r', encoding='utf-8') as f:
            all_rules = yaml.safe_load(f)
        default_rules = all_rules.get(statement_type, [])
        if not default_rules:
            return []
    except Exception as e:
        logging.error(f"Failed to load default calculation rules: {e}")
        return []

    # 2. Query for overrides from the database
    session = get_session()
    try:
        final_rules = []
        for rule in default_rules:
            parent_id = rule['parent']
            # Check for a specific variant for this rule and ticker
            variant = get_rule_variant(session, ticker, parent_id)
            if variant and variant.variant_definition:
                # If an override exists, use it
                logging.info(f"Applying rule variant for {ticker} - {parent_id}")
                override_rule = {'parent': parent_id, 'children': variant.variant_definition.get('children', [])}
                final_rules.append(override_rule)
            else:
                # Otherwise, use the default rule
                final_rules.append(rule)
        return final_rules
    finally:
        session.close()

# --- UNCHANGED FUNCTIONS (get_metric_value, values_match, calculate_statement_rules) ---
def get_metric_value(normalized_figures: List[Dict], playbook_id: str) -> Optional[float]:
    for figure in normalized_figures:
        if figure.get('playbook_id') == playbook_id:
            return figure.get('value')
    return None

def values_match(val1: Optional[float], val2: Optional[float], tolerance_percent: float = 0.01) -> bool:
    if val1 is None or val2 is None: return val1 == val2
    if val1 == 0 and val2 == 0: return True
    if val1 == 0 or val2 == 0: return False
    percentage_diff = abs(val1 - val2) / max(abs(val1), abs(val2)) * 100
    return percentage_diff <= tolerance_percent

def calculate_statement_rules(statement_data: Dict[str, Any], calculation_rules: List[Dict]) -> Dict[str, Any]:
    normalized_figures = statement_data.get('normalized_figures', [])
    results = {'total_calculations': len(calculation_rules), 'passed': 0, 'failed': 0, 'calculations': []}
    for rule in calculation_rules:
        parent_id = rule['parent']
        children = rule.get('children', [])
        parent_value = get_metric_value(normalized_figures, parent_id)
        calculated_value = 0.0
        missing_children = []
        calculation_breakdown = []
        if isinstance(children, list):
            for child_dict in children:
                for child_id, multiplier in child_dict.items():
                    child_value = get_metric_value(normalized_figures, child_id)
                    if child_value is not None:
                        contribution = child_value * multiplier
                        calculated_value += contribution
                        calculation_breakdown.append({'playbook_id': child_id, 'value': child_value, 'multiplier': multiplier, 'contribution': contribution})
                    else:
                        missing_children.append(child_id)
        if parent_value is None:
            status, variance, missing_amount = "PARENT_MISSING", None, None
        elif missing_children:
            if values_match(parent_value, calculated_value):
                status, variance, missing_amount = "PASSED", 0.0, 0.0
            else:
                status, variance, missing_amount = "FAILED", parent_value - calculated_value, parent_value - calculated_value
        else:
            if values_match(parent_value, calculated_value):
                status, variance, missing_amount = "PASSED", 0.0, 0.0
            else:
                status, variance, missing_amount = "FAILED", parent_value - calculated_value, parent_value - calculated_value
        calculation_result = {'rule_id': parent_id, 'parent_value': parent_value, 'calculated_value': calculated_value, 'variance': variance, 'missing_amount': missing_amount, 'status': status, 'missing_children': missing_children, 'calculation_breakdown': calculation_breakdown}
        results['calculations'].append(calculation_result)
        if status in ["PASSED", "PASSED_WITH_MISSING_CHILDREN"]: results['passed'] += 1
        else: results['failed'] += 1
    return results

# --- MODIFIED: Main function to use the new Playbook Resolver ---
# In stage_2a_calculations.py

def run_calculation_validation(working_content: Dict[str, Any], ticker: str) -> Dict[str, Any]:
    """
    Main function to validate all calculations across all statements.
    Now accepts a ticker to fetch the correct rules.
    """
    if not working_content or 'llm_call_2_extraction' not in working_content:
        raise ValueError("No extraction data found in working_content")
    
    extraction_data = working_content['llm_call_2_extraction']
    
    overall_results = {'statement_results': {}, 'overall_passed': 0, 'overall_failed': 0, 'total_statements': 0}
    
    # --- MODIFIED: Simplified rule type matching ---
    statement_types = ['pnl', 'balance_sheet', 'cash_flow_indirect']
    
    for statement_key, statement_data in extraction_data.items():
        matched_type = None
        if 'pnl' in statement_key:
            matched_type = 'pnl'
        elif 'balance_sheet' in statement_key:
            matched_type = 'balance_sheet'
        elif 'cash_flow' in statement_key:  # This will now match both standalone_cash_flow and consolidated_cash_flow
            matched_type = 'cash_flow_indirect'
        
        if not matched_type:
            logging.warning(f"No calculation rule type found for statement key: {statement_key}")
            continue

        # Use the resolver to get rules for this specific ticker and statement type
        rules = get_calculation_rules_for_run(ticker, matched_type)
        
        if not rules:
            logging.warning(f"No calculation rules defined or resolved for statement type: {matched_type} ({statement_key})")
            continue
        
        statement_results = calculate_statement_rules(statement_data, rules)
        statement_results['statement_type'] = statement_key
        
        overall_results['statement_results'][statement_key] = statement_results
        overall_results['total_statements'] += 1
        
        if statement_results['failed'] == 0:
            overall_results['overall_passed'] += 1
        else:
            overall_results['overall_failed'] += 1
    
    return overall_results