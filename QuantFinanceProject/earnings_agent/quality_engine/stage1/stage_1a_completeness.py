from typing import Dict, List, Any

def run_completeness_check(
    parsed_statement_data: Dict[str, Any],
    expected_leaf_ids: List[str]
) -> Dict[str, Any]:
    """
    Compares the playbook_ids in the parsed data against a master list of expected IDs.

    Args:
        parsed_statement_data: The specific statement dictionary from the parsed document's content.
                               (e.g., the value of 'standalone_pnl').
        expected_leaf_ids: The complete list of leaf-node IDs for this statement type.

    Returns:
        A dictionary with a 'status' ('SUCCESS' or 'FAILURE') and optional 'details'.
    """
    if not parsed_statement_data or 'normalized_figures' not in parsed_statement_data:
        return {
            "status": "FAILURE",
            "details": {"missing_ids": expected_leaf_ids, "reason": "Normalized figures array not found."}
        }
    
    # Create a set of the playbook_ids actually present in the parsed data
    present_ids = {
        item['playbook_id'] for item in parsed_statement_data.get('normalized_figures', []) if item.get('playbook_id')
    }
    expected_set = set(expected_leaf_ids)
    unexpected_ids = sorted(list(present_ids - expected_set))
    # Find which expected IDs are missing from the parsed data
    missing_ids = expected_set - present_ids

    if not missing_ids:
        # Success: no IDs are missing
        details = {}
        if unexpected_ids:
            details["unexpected_ids"] = unexpected_ids
        return {"status": "SUCCESS", "details": details if details else None}
    else:
        # Failure: one or more IDs are missing
        details = {"missing_ids": sorted(list(missing_ids))}
        if unexpected_ids:
            details["unexpected_ids"] = unexpected_ids
        return {
            "status": "FAILURE",
            "details": details
        }