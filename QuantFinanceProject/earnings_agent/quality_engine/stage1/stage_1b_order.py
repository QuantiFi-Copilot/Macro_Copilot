# /app/earnings_agent/quality_engine/stage1/stage_1b_order.py

from typing import Dict, List, Any

def run_order_check_and_fix(
    parsed_statement_data: Dict[str, Any],
    expected_leaf_ids: List[str]
) -> Dict[str, Any]:
    """
    Checks if the order of playbook_ids in the parsed data matches the expected order.
    If the order is incorrect but the items are complete, it deterministically fixes the order.

    Args:
        parsed_statement_data: The specific statement dictionary from the parsed document's content.
        expected_leaf_ids: The complete, ordered list of leaf-node IDs for this statement type.

    Returns:
        A dictionary containing:
        - 'status': 'SUCCESS' (order was correct), 'FIXED' (order was corrected), or 'FAILURE'.
        - 'data': The original or reordered `normalized_figures` list, or None on failure.
        - 'details': Information about the mismatch if applicable.
    """
    # Basic validation to ensure the necessary data is present.
    if not parsed_statement_data or 'normalized_figures' not in parsed_statement_data:
        return {
            "status": "FAILURE",
            "data": None,
            "details": {"reason": "Normalized figures array not found in the provided data."}
        }

    normalized_figures = parsed_statement_data['normalized_figures']
    
    # Extract the actual order of playbook_ids from the parsed data.
    actual_ids = [item.get('playbook_id') for item in normalized_figures]

    # Case 1: The order is already correct.
    if actual_ids == expected_leaf_ids:
        return {
            "status": "SUCCESS",
            "data": normalized_figures,
            "details": None
        }

    # Case 2: The order is incorrect. We need to fix it.
    # First, verify that the set of IDs is correct, even if the order is wrong.
    # This is a safeguard, although Stage 1a should have already caught this.
    if set(actual_ids) != set(expected_leaf_ids):
        return {
            "status": "FAILURE",
            "data": None,
            "details": {
                "reason": "Critical: The set of playbook_ids does not match the expected set. This should have been caught by the completeness check.",
                "missing_from_actual": sorted(list(set(expected_leaf_ids) - set(actual_ids))),
                "unexpected_in_actual": sorted(list(set(actual_ids) - set(expected_leaf_ids)))
            }
        }

    # Proceed with the deterministic fix.
    # Create a dictionary for quick lookups of the metric objects by their ID.
    metric_map = {item['playbook_id']: item for item in normalized_figures}
    
    # Rebuild the list of metrics in the correct order as defined by the playbook.
    reordered_figures = [metric_map[pid] for pid in expected_leaf_ids]

    return {
        "status": "FIXED",
        "data": reordered_figures,
        "details": {
            "reason": "The order of metrics was successfully corrected to match the playbook."
        }
    }
