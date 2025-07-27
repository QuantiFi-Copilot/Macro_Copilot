# scripts/test_quality_engine.py

import logging
import sys
import yaml
import json
from pathlib import Path
from datetime import date
from typing import Dict, Any, List

# --- Environment and Path Setup ---
# --- Environment and Path Setup ---
try:
    # Go up 2 levels from the script to get the true project root (/app)
    project_root = Path(__file__).resolve().parents[2] 
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    from earnings_agent.storage.database import get_session, get_staged_data_for_reconciliation
    from earnings_agent.storage.models import CompanyMaster
except ImportError as e:
    print(f"Error: Failed to import project modules. Ensure this script is in the correct directory. Details: {e}")
    sys.exit(1)

# --- Configuration ---
# Construct the path from the correct project root
PLAYBOOKS_DIR = project_root / "earnings_agent" / "playbooks"
TARGET_TICKER = "HDFCBANK"
TARGET_FISCAL_DATE = date(2024, 6, 30)
TARGET_CONSOLIDATION = "Consolidated"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================================================================================
# PHASE 1: A Smart Playbook Loader (Unchanged)
# ================================================================================
class PlaybookLoader:
    """A helper class to load and merge all relevant playbook files into a single master config."""
    def __init__(self, base_dir: Path, industry_name: str):
        self.base_dir = base_dir
        self.industry_name = industry_name

    def _load_yaml(self, path: Path) -> dict:
        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            return {}

    def get_master_playbook(self) -> Dict[str, Any]:
        """Loads all playbooks and merges them into a single configuration object."""
        core_metrics = self._load_yaml(self.base_dir / "global" / "core_metrics.yml").get('core_metrics', [])
        accounting_rules = self._load_yaml(self.base_dir / "global" / "accounting_identities.yml").get('rules', [])
        logical_rules = self._load_yaml(self.base_dir / "global" / "logical_consistency.yml").get('rules', [])
        
        industry_file_name = self.industry_name.lower().replace(" ", "_").replace("&", "and") + ".yml"
        industry_playbook = self._load_yaml(self.base_dir / "industry" / industry_file_name)
        
        custom_kpis = industry_playbook.get('custom_kpis', [])
        industry_validation_rules = industry_playbook.get('suspicious_value_rules', [])
        
        master_playbook = {
            "standard_names": core_metrics + custom_kpis,
            "reconciliation_config": industry_playbook.get('reconciliation_config', {}),
            "validation_rules": accounting_rules + logical_rules + industry_validation_rules
        }
        return master_playbook

# ================================================================================
# PHASE 2 & 3: The Quality Engine
# ================================================================================
class QualityEngine:
    """Performs reconciliation, validation, and completeness checks on normalized data."""
    def __init__(self, ticker: str, fiscal_date: date, consolidation_status: str):
        self.ticker = ticker
        self.fiscal_date = fiscal_date
        self.consolidation_status = consolidation_status
        self.session = get_session()
        self.staged_data_sources = {}
        self.playbook = {}
        self.golden_record: Dict[str, Any] = {}
        self.flags: List[Dict[str, Any]] = []
        self.missing_metrics: List[str] = []

    def _load_data_and_playbook(self):
        """Loads the necessary data and configuration for the engine to run."""
        company = self.session.query(CompanyMaster).filter(CompanyMaster.ticker == self.ticker).first()
        if not company or not company.classification:
            raise ValueError(f"Could not find company or classification for ticker: {self.ticker}")
        industry_name = company.classification.industry_name
        
        loader = PlaybookLoader(PLAYBOOKS_DIR, industry_name)
        self.playbook = loader.get_master_playbook()
        
        staged_records = get_staged_data_for_reconciliation(
            self.ticker, 
            self.fiscal_date,
            self.consolidation_status
        )

        # --- MODIFIED: More robust source identification ---
        for record in staged_records:
            # The entire chain of objects is now pre-loaded and accessible
            source_type = record.parsed_document.asset.job_links[0].job.source_type.lower()
            
            if 'xbrl' in source_type:
                self.staged_data_sources['xbrl_parser'] = record.normalized_data
            elif 'nse_scraper' in source_type:
                self.staged_data_sources['nse_scraper'] = record.normalized_data
        
        logging.info(f"Loaded {len(self.staged_data_sources)} '{self.consolidation_status}' sources for {self.ticker} {self.fiscal_date}")
    
    def run(self):
        self._load_data_and_playbook()
        self._reconcile()
        self._validate()
        self._check_completeness()

    def _reconcile(self):
        """Merges multiple sources into a single golden record based on the playbook's source hierarchy."""
        hierarchy = self.playbook.get('reconciliation_config', {}).get('source_hierarchy', [])
        if not hierarchy or not self.staged_data_sources:
            logging.warning("No reconciliation hierarchy or data sources found. Skipping reconciliation.")
            return
            
        logging.info("--- Starting Reconciliation ---")
        for metric in self.playbook.get('standard_names', []):
            for source in hierarchy:
                # Check if the source exists and if the metric is in that source's data
                if source in self.staged_data_sources and self.staged_data_sources[source].get(metric) is not None:
                    self.golden_record[metric] = self.staged_data_sources[source][metric]
                    break # Found the best source for this metric, move to the next metric

    def _validate(self):
        """Runs all validation rules from the playbook against the golden record."""
        logging.info("--- Starting Validation ---")
        for rule in self.playbook.get('validation_rules', []):
            metric_key = rule.get('target_metric', '').split('.')[-1]
            value = self.golden_record.get(metric_key)
            if value is None:
                continue

            try:
                numeric_value = float(value)
            except (ValueError, TypeError):
                continue

            condition = rule['condition']
            if condition == 'is_negative' and numeric_value < 0:
                self.flags.append(rule['flag'])
            elif condition == 'is_less_than' and numeric_value < rule['value']:
                self.flags.append(rule['flag'])
            elif condition == 'is_greater_than':
                # Handle comparison to either a static value or another metric
                compare_to_value = rule.get('value')
                if 'comparison_metric' in rule:
                    comp_metric_key = rule['comparison_metric'].split('.')[-1]
                    compare_to_value = self.golden_record.get(comp_metric_key)
                
                if compare_to_value is not None:
                    try:
                        if numeric_value > float(compare_to_value):
                            self.flags.append(rule['flag'])
                    except (ValueError, TypeError):
                        continue
            
            elif condition == 'special_check_balance_sheet':
                assets = float(self.golden_record.get('total_assets', 0))
                liab_equity = float(self.golden_record.get('total_liabilities_and_equity', 0))
                if assets and abs(assets - liab_equity) / assets > rule.get('tolerance', 0.01):
                    self.flags.append(rule['flag'])
    
    def _check_completeness(self):
        """Checks for any expected metrics that are missing from the golden record."""
        logging.info("--- Checking Completeness ---")
        expected_metrics = set(self.playbook.get('standard_names', []))
        actual_metrics = set(self.golden_record.keys())
        self.missing_metrics = sorted(list(expected_metrics - actual_metrics))

    def report_to_console(self):
        """Prints a final, human-readable report to the command line."""
        print("\n" + "="*80)
        print(f"✅ QUALITY ENGINE REPORT for {self.ticker} - {self.fiscal_date} ({self.consolidation_status})")
        print("="*80)
        
        print("\n📋 GOLDEN RECORD:")
        # Pretty print numbers if they are numeric
        formatted_record = {}
        for k, v in sorted(self.golden_record.items()):
            try:
                formatted_record[k] = f"{float(v):,.2f}"
            except (ValueError, TypeError):
                formatted_record[k] = v
        print(json.dumps(formatted_record, indent=2))
        
        print("\n" + "-"*40)
        
        if self.flags:
            print(f"\n🚩 FLAGS RAISED ({len(self.flags)}):")
            for flag in self.flags:
                print(f"  - [{flag['severity']}] {flag['message']}")
        else:
            print("\n✅ No flags raised.")
            
        print("\n" + "-"*40)
        
        if self.missing_metrics:
            print(f"\n❓ MISSING METRICS ({len(self.missing_metrics)}):")
            for metric in self.missing_metrics:
                print(f"  - {metric}")
        else:
            print("\n✅ All expected metrics are present.")
            
        print("\n" + "="*80)


if __name__ == "__main__":
    logging.info("Initializing Quality Engine Test Script...")
    engine = QualityEngine(
        ticker=TARGET_TICKER, 
        fiscal_date=TARGET_FISCAL_DATE,
        consolidation_status=TARGET_CONSOLIDATION
    )
    engine.run()
    engine.report_to_console()
    logging.info("Test script finished.")