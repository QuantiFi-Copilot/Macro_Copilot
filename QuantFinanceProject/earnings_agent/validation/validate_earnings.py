import yaml
import logging
from pathlib import Path
from sqlalchemy import select, or_

# Internal project imports
from earnings_agent.storage.database import get_session, update_parsed_earning_with_validation
from earnings_agent.storage.models import ParsedEarning

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Constants ---
VALIDATOR_VERSION = "1.0"
PLAYBOOK_PATH = Path(__file__).parent / "playbooks/suspicious_values_v1.yml"


class ValidationEngine:
    """
    Validates a single parsed earnings JSON object based on a hierarchy of checks.
    - Tier 1: Universal Accounting Identities
    - Tier 2: Data Integrity & Plausibility
    - Tier 3: Configurable Heuristics from a Playbook
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
            return []

    def _add_flag(self, flag_type: str, metric: str, details: str, severity: str = "INFO"):
        self.flags.append({"type": flag_type, "metric": metric, "details": details, "severity": severity})

    def run_completeness_check(self):
        """Tier 2 Check: Ensures the data from the parser is not marked as partial."""
        self.checks_run.append("PARSER_COMPLETENESS_CHECK")
        parse_status = self.data.get("parsing_summary", {}).get("status")
        if parse_status == "PARTIAL_DATA":
            self._add_flag("INCOMPLETE_PARSE", "N/A", "Parser indicated a partial data extraction (e.g., missing Balance Sheet).", "CRITICAL")
            return False
        return True

    def run_balance_sheet_check(self, tolerance=0.02):
        """Tier 1 Check: Verifies the Accounting Equation (Assets = Liabilities + Equity)."""
        self.checks_run.append("BALANCE_SHEET_IDENTITY")
        metrics = self.data.get("core_metrics", {})
        assets = metrics.get("total_assets")
        liabilities = metrics.get("total_liabilities")
        equity = metrics.get("shareholders_equity")

        if any(v is None for v in [assets, liabilities, equity]):
            self._add_flag("MISSING_IDENTITY_METRIC", "total_assets/liabilities/equity", "One or more core metrics for the accounting equation are null.", "CRITICAL")
            return False

        if not abs(assets - (liabilities + equity)) <= abs(assets * tolerance):
            self._add_flag("IDENTITY_FAILED", "Assets vs L+E", f"Balance Sheet does not balance. Assets: {assets}, L+E: {liabilities + equity}.", "CRITICAL")
            return False
        return True

    def run_suspicious_value_playbook(self):
        """Tier 3 Check: Runs all rules defined in the YAML playbook."""
        self.checks_run.append("SUSPICIOUS_VALUE_PLAYBOOK")
        for rule in self.playbook:
            metric_path = rule.get("target_metric", "")
            keys = metric_path.split('.')
            
            # Safely get the metric value from the nested JSON
            value = self.data
            for key in keys:
                value = value.get(key, {})
            # If the loop finished but we have a dict, the path was incomplete
            if isinstance(value, dict): value = None

            if value is None: continue

            condition = rule.get("condition")
            if condition == "is_negative" and value < 0:
                self._add_flag(rule["flag"]["type"], metric_path, rule["flag"]["message"], rule["flag"].get("severity", "WARNING"))
            elif condition == "equals" and value == rule.get("value"):
                self._add_flag(rule["flag"]["type"], metric_path, rule["flag"]["message"], rule["flag"].get("severity", "WARNING"))
        return True

    def validate(self):
        """Runs all validation tiers and returns the enriched data object."""
        # Run checks
        self.run_completeness_check()
        self.run_balance_sheet_check()
        self.run_suspicious_value_playbook()
        
        # Determine final status
        final_status = "PASSED"
        has_critical_flag = any(f.get("severity") == "CRITICAL" for f in self.flags)
        
        if has_critical_flag:
            final_status = "FAILED"
        elif self.flags: # Has flags, but none are critical
            final_status = "PASSED_WITH_WARNINGS"

        # Assemble the final summary
        self.data["validation_summary"] = {
            "validator_version": VALIDATOR_VERSION,
            "playbook_version": "1.0", # Or get from YAML
            "status": final_status,
            "checks_run": self.checks_run,
            "flags": self.flags
        }
        return self.data

# --- Main Batch Processing Logic ---
# --- Main Batch Processing Logic ---
if __name__ == '__main__':
    logging.info(f"--- Starting ValidationEngine Batch Run v{VALIDATOR_VERSION} ---")
    session = get_session()
    
    try:
        # Find all ParsedEarning records that have not been validated by this version yet.
        
        # CORRECTED SYNTAX: Using dictionary-style access ['key'] instead of .path('key')
        # This is the standard and correct way to query JSONB fields in SQLAlchemy.
        stmt = select(ParsedEarning).where(
            or_(
                ParsedEarning.content["validation_summary"].is_(None),
                ParsedEarning.content["validation_summary"]["validator_version"].astext != VALIDATOR_VERSION
            )
        ).order_by(ParsedEarning.id)
        
        records_to_process = session.execute(stmt).scalars().all()

        if not records_to_process:
            logging.info(f"All records are already validated with v{VALIDATOR_VERSION}. No new records to process.")
        else:
            logging.info(f"Found {len(records_to_process)} records to validate with v{VALIDATOR_VERSION}.")

            for record in records_to_process:
                logging.info(f"Validating record_id: {record.id} for ticker: {record.ticker} ({record.fiscal_date})")
                try:
                    engine = ValidationEngine(record.content)
                    validated_data = engine.validate()
                    
                    update_parsed_earning_with_validation(record.id, validated_data)
                    
                    final_status = validated_data["validation_summary"]["status"]
                    logging.info(f"Successfully processed record_id: {record.id}. Final Status: {final_status}")

                except Exception as e:
                    logging.error(f"An unexpected error occurred validating record_id {record.id}: {e}", exc_info=True)
    finally:
        session.close()
        logging.info(f"--- ValidationEngine Batch Run Finished v{VALIDATOR_VERSION} ---")