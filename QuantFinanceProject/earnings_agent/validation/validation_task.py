# earnings_agent/validation/validation_task.py

import yaml
import logging
from pathlib import Path
from typing import Dict, Any

# --- Core Application Imports ---
from earnings_agent.storage.database import get_session, create_validation_result
from earnings_agent.storage.models import ParsedDocument

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Configuration ---
VALIDATOR_VERSION = "2.1" # Bumping version for playbook fix
PLAYBOOK_PATH = Path(__file__).parent / "playbooks/suspicious_values_v1.yml"


# ================================================================================================
# CORE VALIDATION ENGINE
# ================================================================================================

class ValidationEngine:
    """
    Validates a single parsed earnings JSON object based on a hierarchy of checks.
    """
    def __init__(self, parsed_data: dict):
        self.data = parsed_data
        self.flags = []
        self.checks_run = []
        self.playbook = self._load_playbook()

    def _load_playbook(self):
        try:
            with open(PLAYBOOK_PATH, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logging.error(f"Playbook not found at {PLAYBOOK_PATH}. Proceeding without heuristic checks.")
            return {} # Return a dict so .get() calls don't fail

    def _add_flag(self, flag_type: str, metric: str, details: str, severity: str = "INFO"):
        self.flags.append({"type": flag_type, "metric": metric, "details": details, "severity": severity})

    # (run_completeness_check and run_balance_sheet_check are unchanged)
    def run_completeness_check(self):
        self.checks_run.append("PARSER_COMPLETENESS_CHECK")
        parse_status = self.data.get("parsing_summary", {}).get("status")
        if parse_status == "PARTIAL_DATA":
            self._add_flag("INCOMPLETE_PARSE", "N/A", "Parser indicated a partial data extraction (e.g., missing Balance Sheet).", "CRITICAL")
            return False
        return True

    def run_balance_sheet_check(self, tolerance=0.02):
        self.checks_run.append("BALANCE_SHEET_IDENTITY")
        metrics = self.data.get("core_metrics", {})
        if not metrics: return False
        
        assets = metrics.get("total_assets")
        liabilities = metrics.get("total_liabilities")
        equity = metrics.get("shareholders_equity")

        if any(v is None for v in [assets, liabilities, equity]):
            self._add_flag("MISSING_IDENTITY_METRIC", "total_assets/liabilities/equity", "One or more core metrics for the accounting equation are null.", "CRITICAL")
            return False

        if not all(isinstance(v, (int, float)) for v in [assets, liabilities, equity]):
            self._add_flag("INVALID_DATATYPE_FOR_IDENTITY", "total_assets/liabilities/equity", "One or more core metrics for accounting equation are not numbers.", "CRITICAL")
            return False
            
        if not abs(assets - (liabilities + equity)) <= abs(assets * tolerance):
            self._add_flag("IDENTITY_FAILED", "Assets vs L+E", f"Balance Sheet does not balance. Assets: {assets}, L+E: {liabilities + equity}.", "CRITICAL")
            return False
        return True

    def run_suspicious_value_playbook(self):
        """
        --- MODIFIED: This function now handles the playbook being a list OR a dictionary. ---
        """
        self.checks_run.append("SUSPICIOUS_VALUE_PLAYBOOK")

        # Check the type of the loaded playbook
        if isinstance(self.playbook, dict):
            rules = self.playbook.get('rules', [])
        elif isinstance(self.playbook, list):
            rules = self.playbook # Use the list directly
        else:
            rules = []

        for rule in rules:
            metric_path = rule.get("target_metric", "")
            keys = metric_path.split('.')
            
            value = self.data
            try:
                for key in keys: value = value.get(key)
            except AttributeError:
                value = None

            if value is None: continue

            condition = rule.get("condition")
            if condition == "is_negative" and isinstance(value, (int, float)) and value < 0:
                self._add_flag(rule["flag"]["type"], metric_path, rule["flag"]["message"], rule["flag"].get("severity", "WARNING"))
            elif condition == "equals" and value == rule.get("value"):
                self._add_flag(rule["flag"]["type"], metric_path, rule["flag"]["message"], rule["flag"].get("severity", "WARNING"))
        return True

    def validate(self) -> Dict[str, Any]:
        """Runs all validation tiers and returns the summary."""
        self.run_completeness_check()
        self.run_balance_sheet_check()
        self.run_suspicious_value_playbook()
        
        final_status = "PASSED"
        has_critical_flag = any(f.get("severity") == "CRITICAL" for f in self.flags)
        
        if has_critical_flag:
            final_status = "FAILED"
        elif self.flags:
            final_status = "PASSED_WITH_WARNINGS"

        # --- MODIFIED: Safely get the playbook version ---
        playbook_version = self.playbook.get("version", "unknown") if isinstance(self.playbook, dict) else "unknown"

        return {
            "validator_version": VALIDATOR_VERSION,
            "playbook_version": playbook_version,
            "status": final_status,
            "checks_run": self.checks_run,
            "flags": self.flags
        }

# (The main runner function `validate_parsed_document` is unchanged)
def validate_parsed_document(doc_id: int):
    logging.info(f">>> (TASK) Starting Validation for doc_id: {doc_id} <<<")
    session = get_session()
    
    try:
        parsed_doc = session.get(ParsedDocument, doc_id)
        if not parsed_doc:
            logging.error(f"No ParsedDocument found for doc_id: {doc_id}. Aborting.")
            return
        if not parsed_doc.content:
            logging.warning(f"ParsedDocument {doc_id} has no content to validate. Skipping.")
            return

        engine = ValidationEngine(parsed_doc.content)
        validation_summary = engine.validate()
        
        result_data = {
            "doc_id": doc_id,
            "validation_script_version": VALIDATOR_VERSION,
            "status": validation_summary["status"],
            "summary": validation_summary
        }
        
        create_validation_result(result_data)
        logging.info(f"Successfully validated and stored result for doc_id: {doc_id}. Status: {validation_summary['status']}")

    except Exception as e:
        logging.error(f"An unexpected error occurred validating doc_id {doc_id}: {e}", exc_info=True)
    finally:
        session.close()
        logging.info(f">>> (TASK) Finished Validation for doc_id: {doc_id} <<<")


if __name__ == '__main__':
    TEST_DOC_ID = 1 
    validate_parsed_document(doc_id=TEST_DOC_ID)