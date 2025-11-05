# /app/earnings_agent/quality_engine/stage4/stage4.py

import sys
import json
import copy
import re
import logging
import datetime
from pathlib import Path
from typing import Dict, Any, List
from collections import Counter

# --- Path Setup ---
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_ROOT))

# --- Database and Model Imports ---
from earnings_agent.storage.database import get_runs_by_stage_4_status, update_quality_run
from earnings_agent.storage.models import QualityEngineRun

# --- Configuration ---
EXPECTATIONS_PATH = PROJECT_ROOT / "earnings_agent" / "playbooks" / "sebi" / "expected_metadata" / "banking_expectations.json"
CURRENT_STAGE_4_VERSION = "1.2-spec-aligned"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

CONVERSION_FACTORS = {
    "crore": 10_000_000,
    "crores": 10_000_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "billion": 1_000_000_000,
    "billions": 1_000_000_000,
    "thousand": 1_000,
    "thousands": 1_000,
    "hundred": 100,
    "hundreds": 100
}

# --- Helper Functions ---

def load_expectations(path: Path) -> Dict[str, Any]:
    """Loads and restructures the expectations file for fast lookups."""
    with open(path, 'r', encoding='utf-8') as f:
        expectations_data = json.load(f)
    id_to_expectation_map = {}
    for section_data in expectations_data.values():
        for playbook_id, details in section_data.get("ids", {}).items():
            id_to_expectation_map[playbook_id] = details
    return id_to_expectation_map

def _append_history(run_history: list, step: str, status: str, details: dict = None) -> list:
    """Helper to append a new record to the run history."""
    if run_history is None: run_history = []
    record = {
        "step": step,
        "status": status,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    if details: record["details"] = details
    run_history.append(record)
    return run_history

# --- Core Normalization Handlers (Unchanged) ---

def _normalize_currency(metric: Dict[str, Any]) -> Dict[str, Any]:
    value = metric.get('value')
    unit_scale = metric.get('unit_scale')
    if value is None or unit_scale is None: return metric
    multiplier = CONVERSION_FACTORS.get(unit_scale.lower())
    if multiplier:
        raw_value = float(value) * multiplier
        metric['value'] = int(raw_value) if raw_value == int(raw_value) else raw_value
        metric['unit_scale'] = None
        metric.setdefault('transformations_applied', []).append(f"unit_normalized_from_{unit_scale}")
    return metric

def _normalize_percentage(metric: Dict[str, Any]) -> Dict[str, Any]:
    value = metric.get('value')
    if value is None: return metric
    if '%' in metric.get('raw_label', ''): pass
    elif 0 <= float(value) < 1:
        metric['value'] = float(value) * 100
        metric.setdefault('transformations_applied', []).append('heuristic_scaled_x100_based_on_magnitude')
    if metric.get('representation') != 'percentage':
        original_rep = metric.get('representation', 'none')
        metric['representation'] = 'percentage'
        metric.setdefault('transformations_applied', []).append(f'metadata_normalized_representation_{original_rep}_to_percentage')
    return metric

def _normalize_ratio(metric: Dict[str, Any]) -> Dict[str, Any]:
    if metric.get('value') is None: return metric
    if metric.get('representation') != 'ratio':
        original_rep = metric.get('representation', 'none')
        metric['representation'] = 'ratio'
        metric.setdefault('transformations_applied', []).append(f'metadata_normalized_representation_{original_rep}_to_ratio')
    if '%' in metric.get('raw_label', ''):
        metric.setdefault('normalization_flags', []).append('WARNING_percent_symbol_found_on_absolute_ratio')
    return metric

# --- Main Processing Function (Unchanged Pure Logic) ---

def normalize_units_for_document(working_content: Dict[str, Any], expectations: Dict[str, Any]) -> Dict[str, Any]:
    """The core pure function that iterates through the document and applies normalization."""
    normalized_content = copy.deepcopy(working_content)
    extraction_data = normalized_content.get("llm_call_2_extraction", {})
    
    for stmt_key, data in extraction_data.items():
        unit_scales_in_statement = [
            m.get('unit_scale') for m in data.get('normalized_figures', []) if m.get('unit_scale') is not None
        ]
        dominant_unit = Counter(unit_scales_in_statement).most_common(1)[0][0] if unit_scales_in_statement else None
        
        for list_name in ["normalized_figures", "company_specific_kpis"]:
            metrics_list = data.get(list_name, [])
            if not metrics_list: continue

            for metric in metrics_list:
                playbook_id = metric.get('playbook_id') or metric.get('normalized_label')
                expectation = expectations.get(playbook_id)
                
                is_kpi = list_name == "company_specific_kpis"
                if is_kpi and not expectation:
                    label = (metric.get('raw_label') or '').lower()
                    if '%' in label: expectation = {'representation': 'percentage'}
                    elif 'ratio' in label: expectation = {'representation': 'ratio'}
                    else: expectation = {'representation': 'currency'}
                
                if not expectation: continue

                if is_kpi and expectation.get('representation') == 'currency' and metric.get('unit_scale') is None:
                    metric['unit_scale'] = 'crore'
                    metric.setdefault('transformations_applied', []).append("inferred_unit_as_crore")

                rep = expectation.get('representation')
                if rep == 'currency': metric = _normalize_currency(metric)
                elif rep == 'percentage': metric = _normalize_percentage(metric)
                elif rep == 'ratio': metric = _normalize_ratio(metric)
    return normalized_content

# --- NEW: Production Orchestrator ---

def run_stage_4_orchestrator():
    """
    Main orchestrator for Stage 4. Fetches pending runs from the DB,
    processes them, and updates their status.
    """
    logging.info("--- Running Stage 4: Unit Normalization ---")
    
    try:
        expectations = load_expectations(EXPECTATIONS_PATH)
    except Exception as e:
        logging.error(f"Could not load expectations file: {e}. Aborting Stage 4.")
        return

    runs_to_process = get_runs_by_stage_4_status(['PENDING', 'NORMALIZATION_ERROR'])
    if not runs_to_process:
        logging.info("No documents are currently pending Stage 4 processing.")
        return
        
    logging.info(f"Found {len(runs_to_process)} documents for Stage 4 processing.")
    
    for run in runs_to_process:
        logging.info(f"Processing doc_id: {run.doc_id}, run_id: {run.run_id}")
        try:
            # Run the pure normalization logic
            final_content = normalize_units_for_document(run.working_content, expectations)
            
            # Prepare updates for the database
            history = _append_history(run.run_history, "4_unit_normalization", "SUCCESS")
            updates = {
                "working_content": final_content,
                "stage_4_status": "PASSED",
                "stage_4_version": CURRENT_STAGE_4_VERSION,
                "run_history": history,
                "failure_reason": None
            }
            update_quality_run(run.run_id, updates)
            logging.info(f"  -> SUCCESS for doc_id {run.doc_id}. Unit normalization complete.")

        except Exception as e:
            logging.error(f"  -> FAILURE for doc_id {run.doc_id}: {e}", exc_info=True)
            history = _append_history(run.run_history, "4_unit_normalization", "FAILURE", {"error": str(e)})
            updates = {
                "stage_4_status": "NORMALIZATION_ERROR",
                "failure_reason": f"Stage 4 Error: {str(e)}",
                "run_history": history
            }
            update_quality_run(run.run_id, updates)

if __name__ == "__main__":
    # This block can be used for direct testing if needed, but is not part of the main pipeline
    logging.info("Running Stage 4 Orchestrator directly for testing.")
    run_stage_4_orchestrator()