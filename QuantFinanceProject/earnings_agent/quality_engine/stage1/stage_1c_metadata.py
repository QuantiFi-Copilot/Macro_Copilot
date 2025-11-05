# /app/earnings_agent/quality_engine/stage1/stage_1c_metadata.py

from typing import Dict, Any, Tuple

# This import is still needed for the list of valid enum values
from earnings_agent.parsing.pdf.pdf_extractor_config import UnitScaleType

VALID_UNIT_SCALES = [e.value for e in UnitScaleType]
COMPATIBLE_TYPES = {
    "currency": ["currency"],
    "percentage": ["percentage", "ratio"],
    "ratio": ["ratio", "percentage"]
}

def _validate_and_fix_figure(figure: Dict[str, Any], expectation: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
    """
    Helper function to validate and fix a single metric figure using a universal
    metadata synchronization rule against its specific expectation.
    """
    fixes_made = []

    # Rule 1: Null Value Integrity Check (No change)
    if figure.get('value') is None:
        is_fixed = False
        for key in ['representation', 'currency_context', 'unit_scale', 'ratio_context']:
            if figure.get(key) is not None:
                figure[key] = None
                is_fixed = True
        if is_fixed:
            return "FIXED", figure, f"playbook_id [{figure['playbook_id']}]: Set metadata fields to null because value is null."
        return "SUCCESS", figure, ""

    # Rule 2: Representation Flexibility Check (No change)
    expected_rep = expectation.get('representation')
    if figure.get('representation') not in COMPATIBLE_TYPES.get(expected_rep, []):
        return "FAILURE", figure, f"playbook_id [{figure['playbook_id']}]: Representation Mismatch. Expected compatible with '{expected_rep}' but found '{figure.get('representation')}'."

    # --- NEW: Universal Metadata Synchronization Logic ---
    # This block now systematically checks and corrects all metadata to match the expectation blueprint.

    # Synchronize 'representation'
    if figure.get('representation') != expected_rep:
        fixes_made.append(f"Corrected representation from '{figure.get('representation')}' to '{expected_rep}'")
        figure['representation'] = expected_rep

    # Synchronize 'currency_context'
    expected_cc = expectation.get('currency_context')
    if figure.get('currency_context') != expected_cc:
        fixes_made.append(f"Corrected currency_context from '{figure.get('currency_context')}' to '{expected_cc}'")
        figure['currency_context'] = expected_cc

    # Synchronize 'ratio_context'
    expected_rc = expectation.get('ratio_context')
    if figure.get('ratio_context') != expected_rc:
        fixes_made.append(f"Corrected ratio_context from '{figure.get('ratio_context')}' to '{expected_rc}'")
        figure['ratio_context'] = expected_rc
        
    # --- UNCHANGED: Unit Scale VALIDATION (not synchronization) ---
    expected_unit_scale = expectation.get('unit_scale')
    actual_unit_scale = figure.get('unit_scale')
    if expected_unit_scale is not None: # Expectation requires a scale
        if actual_unit_scale not in VALID_UNIT_SCALES:
            return "FAILURE", figure, f"playbook_id [{figure['playbook_id']}]: Invalid unit_scale '{actual_unit_scale}'. Must be one of {VALID_UNIT_SCALES}."
    else: # Expectation requires NO scale
        if actual_unit_scale is not None:
            # This can now be auto-fixed by the synchronization logic above if we add 'unit_scale' to it.
            # For now, keeping it as a failure for safety as discussed.
            return "FAILURE", figure, f"playbook_id [{figure['playbook_id']}]: unit_scale should be null but found '{actual_unit_scale}'."

    if fixes_made:
        details = f"playbook_id [{figure['playbook_id']}]: " + ", ".join(fixes_made)
        return "FIXED", figure, details
        
    return "SUCCESS", figure, ""


def run_metadata_check_and_fix(parsed_statement_data: Dict[str, Any], expectations: Dict[str, Any]) -> Dict[str, Any]:
    """
    Checks the metadata of each metric against logical rules and expectations.
    Attempts to auto-fix unambiguous issues.
    """
    if not parsed_statement_data or 'normalized_figures' not in parsed_statement_data:
        return {"status": "FAILURE", "data": None, "details": {"reason": "Normalized figures array not found."}}
    
    original_figures = parsed_statement_data['normalized_figures']
    updated_figures = []
    
    overall_status = "SUCCESS"
    details_log = []

    for figure in original_figures:
        playbook_id = figure.get('playbook_id')
        expectation = expectations.get(playbook_id)

        if not expectation or not expectation.get('matchable', False):
            updated_figures.append(figure)
            continue

        status, new_figure, details = _validate_and_fix_figure(dict(figure), expectation)

        if status == "FAILURE":
            # This function now only validates a single statement,
            # so it can return the first failure it finds within that statement.
            # The orchestrator will handle aggregation of failures across statements.
            return {"status": "FAILURE", "data": None, "details": {"reason": details}}
        
        if status == "FIXED":
            overall_status = "FIXED"
            details_log.append(details)
        
        updated_figures.append(new_figure)

    return {
        "status": overall_status,
        "data": updated_figures,
        "details": {"summary": details_log} if details_log else None
    }