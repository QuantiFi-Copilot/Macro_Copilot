# earnings_agent/normalization/normalization_task.py
import logging
import yaml
from pathlib import Path
from datetime import date
from typing import Dict, Any
import hashlib
import json
# --- Core Application Imports ---
from earnings_agent.storage.database import (
    get_session,
    get_company_context,
    get_label_mapping,
    upsert_label_mapping,
    create_staged_normalized_data,
    get_docs_pending_label_normalization,
    mark_docs_label_normalized
)
from earnings_agent.storage.models import ParsedDocument, JobAssetLink, IngestionJob, StagedNormalizedData
from earnings_agent.llm.normalizer_client import get_llm_mapping_suggestion
from sqlalchemy.orm.attributes import flag_modified

# --- Standard Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
logging.getLogger().setLevel(logging.DEBUG)

# --- Configuration ---
NORMALIZER_VERSION = "1.0.0" # Final version with all optimizations
PLAYBOOKS_DIR = Path(__file__).resolve().parents[1] / "playbooks"

class PlaybookLoader:
    """Handles the loading and merging of global and industry-specific playbooks."""
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.global_playbook = self._load_yaml(base_dir / "global" / "core_metrics.yml")

    def _load_yaml(self, path: Path) -> dict:
        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logging.info(f"Playbook file not found at: {path}. This may be expected.")
            return {}
        except Exception as e:
            logging.error(f"Error loading or parsing playbook {path}: {e}")
            return {}

    def get_playbook(self, industry_name: str) -> dict:
        """
        Loads the playbook for a specific industry and merges it with the global playbook.
        """
        industry_file_name = industry_name.lower().replace(" ", "_").replace("&", "and") + ".yml"
        industry_playbook_path = self.base_dir / "industry" / industry_file_name
        industry_playbook = self._load_yaml(industry_playbook_path)

        core_metrics = self.global_playbook.get('core_metrics', [])
        custom_kpis = industry_playbook.get('custom_kpis', [])
        standard_names = core_metrics + custom_kpis

        playbook = {
            "standard_names": standard_names,
            "has_industry_playbook": bool(industry_playbook),
            "industry_specific_rules": industry_playbook
        }
        return playbook

def run_label_normalizer_batch(allow_llm: bool = False):
    """
    Process staged_normalized_data rows pending label normalization.
    This version includes enhanced debugging logs.
    """
    session = get_session()
    playbook_loader = PlaybookLoader(PLAYBOOKS_DIR)
    try:
        doc_ids = get_docs_pending_label_normalization()
        logging.info(f"Found {len(doc_ids)} documents pending label normalization.")

        for doc_id in doc_ids:
            logging.debug(f"\n--- Processing doc_id: {doc_id} ---")
            record = session.query(StagedNormalizedData).filter(
                StagedNormalizedData.doc_id == doc_id
            ).one()

            company_context = get_company_context(session, record.ticker)
            if not company_context or not company_context.classification:
                logging.warning(f"Skipping doc_id {doc_id} for {record.ticker}: No classification found.")
                continue
            industry_name = company_context.classification.industry_name
            playbook = playbook_loader.get_playbook(industry_name)
            standard_names = playbook.get("standard_names", [])

            if not standard_names:
                logging.warning(f"Skipping doc_id {doc_id} for {record.ticker}: No standard names in playbook for '{industry_name}'.")
                continue

            data = record.normalized_data
            if not data.get('facts_by_raw_key'):
                logging.warning(f"Skipping doc_id {doc_id}: No 'facts_by_raw_key' found in normalized_data.")
                continue
                
            raw_keys = list(data.get('facts_by_raw_key', {}).keys())
            logging.debug(f"Found {len(raw_keys)} raw keys to process.")

            mappings = {rk: get_label_mapping(rk) for rk in raw_keys}
            
            # --- DEBUG LOG 1: Show what was retrieved from the cache ---
            logging.debug("--- Cache Retrieval Results ---")
            for rk, mapping_obj in mappings.items():
                if mapping_obj:
                    logging.debug(f"  - HIT for '{rk}': Status='{mapping_obj.status}', Mapped to='{mapping_obj.normalized_label}'")
                else:
                    logging.debug(f"  - MISS for '{rk}'")
            # -----------------------------------------------------------

            missing = [rk for rk, m in mappings.items() if m is None]
            
            if allow_llm and missing:
                # ... (LLM logic remains the same) ...
                pass

            approved_keys = [rk for rk, m in mappings.items() if m and m.status == 'APPROVED']
            
            # --- DEBUG LOG 2: Show the final list of approved keys ---
            logging.debug(f"Found {len(approved_keys)} approved keys: {approved_keys}")
            # -------------------------------------------------------

            label_map = {rk: {
                'label': mappings[rk].normalized_label if mappings[rk] else None,
                'status': mappings[rk].status if mappings[rk] else 'MISSING'
            } for rk in raw_keys}
            data['label_map'] = label_map

            facts_by_label = {}
            for rk in approved_keys:
                lbl = mappings[rk].normalized_label
                facts_by_label[lbl] = data['facts_by_raw_key'][rk] # <-- CORRECTED LINE
            data['facts_by_label'] = facts_by_label

            # --- DEBUG LOG 3: Show the final dictionary being saved ---
            logging.debug(f"Final 'facts_by_label' dictionary has {len(facts_by_label)} keys: {list(facts_by_label.keys())}")
            # --------------------------------------------------------

            record.normalized_data = data
            new_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            record.data_hash = new_hash
            
            status = 'APPROVED' if len(approved_keys) == len(raw_keys) else 'PARTIAL'
            mark_docs_label_normalized([doc_id], status)
            logging.info(f"Marking doc_id {doc_id} as '{status}'.")
            flag_modified(record, 'normalized_data')
            
        session.commit()
    finally:
        session.close()