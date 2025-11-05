# /app/earnings_agent/quality_engine/expectations_utils.py

import json
from pathlib import Path
from typing import Dict, Any, Optional
from functools import lru_cache

# Define the path to the expectations file
EXPECTATIONS_PATH = Path("/app/earnings_agent/playbooks/sebi/expected_metadata/banking_expectations.json")

@lru_cache(maxsize=1)
def load_expectations() -> Dict[str, Any]:
    """
    Loads the banking_expectations.json file into a dictionary.
    Uses lru_cache to ensure the file is only read from disk once.

    Returns:
        A dictionary containing the metadata expectations.
    """
    if not EXPECTATIONS_PATH.exists():
        raise FileNotFoundError(f"Expectations file not found at: {EXPECTATIONS_PATH}")
    
    with open(EXPECTATIONS_PATH, 'r') as f:
        expectations_data = json.load(f)

    # Restructure the data for faster lookups by playbook_id
    id_to_expectation_map = {}
    for section_data in expectations_data.values():
        for playbook_id, details in section_data.get("ids", {}).items():
            id_to_expectation_map[playbook_id] = details
            
    return id_to_expectation_map

def get_metric_expectation(playbook_id: str, expectations: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Retrieves the expectation rules for a specific playbook_id.

    Args:
        playbook_id: The ID of the metric to look up.
        expectations: The loaded expectations dictionary from load_expectations().

    Returns:
        The dictionary of expectation rules for the metric, or None if not found.
    """
    return expectations.get(playbook_id)