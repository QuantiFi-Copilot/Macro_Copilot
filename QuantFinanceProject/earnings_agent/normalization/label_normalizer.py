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

class Normalizer:
    """
    The core normalization engine. It is source-agnostic and operates on a
    ParsedDocument's raw_facts using a playbook.
    """
    def __init__(self, raw_facts: dict, playbook: dict, source_context: dict, run_cache: Dict[str, Any]):
        self.raw_facts = raw_facts
        self.playbook = playbook
        self.source_context = source_context
        self.run_cache = run_cache
        self.standard_names_set = {name.lower() for name in self.playbook.get("standard_names", [])}

    def run(self) -> dict:
        """
        Iterates through all raw facts and maps them to standard names.
        """
        normalized_data = {}
        logging.info(f"Normalizing {len(self.raw_facts)} raw facts...")
        for raw_label, value in self.raw_facts.items():
            standard_label = self._get_standard_label(raw_label)
            if standard_label:
                normalized_data[standard_label] = value
                logging.debug(f"Mapped '{raw_label}' -> '{standard_label}'")
        logging.info(f"Successfully normalized {len(normalized_data)} facts.")
        return normalized_data

    def _get_standard_label(self, raw_label: str) -> str | None:
        """
        Implements the final, fully optimized waterfall logic.
        """
        # --- Step 0: In-Memory Run Cache (Fastest Path) ---
        if raw_label in self.run_cache:
            logging.debug(f"In-Memory Cache HIT for '{raw_label}'")
            return self.run_cache[raw_label]

        # --- Step 1: Direct Match ---
        if raw_label.lower() in self.standard_names_set:
            for name in self.playbook["standard_names"]:
                if name.lower() == raw_label.lower():
                    self.run_cache[raw_label] = name # Store result in run-cache
                    return name
        
        # --- Step 2: Persistent DB Cache Query (Now handles PENDING) ---
        cached_mapping = get_label_mapping(raw_label)
        if cached_mapping:
            if cached_mapping.status == 'APPROVED':
                result = cached_mapping.normalized_label
                logging.debug(f"DB Cache HIT ('{raw_label}' -> '{result}')")
                self.run_cache[raw_label] = result
                return result
            elif cached_mapping.status == 'REJECTED':
                logging.debug(f"DB Cache HIT ('{raw_label}' -> REJECTED)")
                self.run_cache[raw_label] = None
                return None
            elif cached_mapping.status == 'PENDING_REVIEW':
                logging.info(f"Skipping LLM for '{raw_label}': A suggestion is already pending review.")
                self.run_cache[raw_label] = None # Cache the decision to skip
                return None

        # --- Step 3: Playbook Gate ---
        if not self.playbook.get("has_industry_playbook"):
            logging.warning(f"SKIPPING LLM for '{raw_label}': No industry playbook loaded.")
            self.run_cache[raw_label] = None
            return None

        # --- Step 4: LLM Query ---
        logging.warning(f"Persistent Cache MISS for '{raw_label}'. Escalating to LLM.")
        suggestion = get_llm_mapping_suggestion(raw_label, self.playbook["standard_names"])

        # --- Step 5: Store and Memoize ---
        if suggestion and suggestion != "N/A":
            logging.info(f"LLM suggested mapping '{raw_label}' -> '{suggestion}'. Storing for review.")
            upsert_label_mapping({
                "raw_label": raw_label, "normalized_label": suggestion,
                "status": "PENDING_REVIEW", "source_context": self.source_context
            })
            self.run_cache[raw_label] = None
        else:
            logging.warning(f"LLM could not map '{raw_label}'. It will be ignored for now.")
            self.run_cache[raw_label] = None

        return None

def normalize_document(doc_id: int, run_cache: Dict[str, Any]):
    """
    Main worker function for the normalization task. Orchestrates the entire process.
    """
    logging.info(f">>> (TASK) Starting Normalization for doc_id: {doc_id} <<<")
    session = get_session()
    playbook_loader = PlaybookLoader(PLAYBOOKS_DIR)
    
    try:
        # 1. Fetch context
        parsed_doc = session.get(ParsedDocument, doc_id)
        if not parsed_doc: return
        link = session.query(JobAssetLink).filter(JobAssetLink.asset_id == parsed_doc.asset_id).first()
        if not link or not link.job: return
        job = link.job
        ticker = job.ticker
        if job.quarter == 1: fiscal_date = date(job.fiscal_year, 6, 30)
        elif job.quarter == 2: fiscal_date = date(job.fiscal_year, 9, 30)
        elif job.quarter == 3: fiscal_date = date(job.fiscal_year, 12, 31)
        else: fiscal_date = date(job.fiscal_year + 1, 3, 31)

        # 2. Get industry
        company_context = get_company_context(session, ticker)
        if not company_context or not company_context.classification: return
        industry_name = company_context.classification.industry_name
        logging.info(f"Company: {ticker}, Industry: {industry_name}")

        # 3. Load playbook
        playbook = playbook_loader.get_playbook(industry_name)
        if not playbook.get("standard_names"): return

        # 4. Instantiate and run Normalizer
        source_context_for_cache = {"doc_id": doc_id, "ticker": ticker, "fiscal_date": fiscal_date.isoformat()}
        raw_facts = parsed_doc.content
        if not raw_facts: return

        normalizer = Normalizer(
            raw_facts=raw_facts,
            playbook=playbook,
            source_context=source_context_for_cache,
            run_cache=run_cache
        )
        normalized_data = normalizer.run()

        # 5. Save output
        staged_data = {"doc_id": doc_id, "ticker": ticker, "fiscal_date": fiscal_date, "normalized_data": normalized_data}
        create_staged_normalized_data(staged_data)
        logging.info(f"Successfully created staged normalized data for doc_id: {doc_id}")

    except Exception as e:
        logging.error(f"A critical, unexpected error occurred during normalization for doc_id {doc_id}: {e}", exc_info=True)
    finally:
        session.close()
        logging.info(f">>> (TASK) Finished Normalization for doc_id: {doc_id} <<<")

def run_label_normalizer_batch(allow_llm: bool = False):
    """
    Process staged_normalized_data rows pending label normalization.
    If allow_llm=True, will call LLM for unmapped raw labels; otherwise skips LLM.
    """
    session = get_session()
    # --- FIX: Initialize the PlaybookLoader once at the start ---
    playbook_loader = PlaybookLoader(PLAYBOOKS_DIR)
    try:
        doc_ids = get_docs_pending_label_normalization()
        for doc_id in doc_ids:
            record = session.query(StagedNormalizedData).filter(
                StagedNormalizedData.doc_id == doc_id
            ).one()

            # --- FIX: Get company context and load the correct playbook ---
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
            raw_keys = list(data.get('facts_by_raw_key', {}).keys())

            mappings = {rk: get_label_mapping(rk) for rk in raw_keys}
            missing = [rk for rk, m in mappings.items() if m is None]
            
            if allow_llm and missing:
                for rk in missing:
                    # --- FIX: Pass the correct standard_names list from the playbook ---
                    suggestion = get_llm_mapping_suggestion(
                        rk, standard_names
                    )
                    if suggestion and suggestion != 'N/A':
                        upsert_label_mapping({
                            'raw_label': rk,
                            'normalized_label': suggestion,
                            'status': 'PENDING_REVIEW',
                            'source_context': {'doc_id': doc_id, 'ticker': record.ticker}
                        })
                # Refresh mappings after seeding
                mappings = {rk: get_label_mapping(rk) for rk in raw_keys}

            approved_keys = [rk for rk, m in mappings.items() if m and m.status == 'APPROVED']
            all_approved = len(approved_keys) == len(raw_keys)

            label_map = {rk: {
                'label': mappings[rk].normalized_label if mappings[rk] else None,
                'status': mappings[rk].status if mappings[rk] else 'MISSING'
            } for rk in raw_keys}
            data['label_map'] = label_map

            facts_by_label = {}
            for rk in approved_keys:
                lbl = mappings[rk].normalized_label
                facts_by_label.setdefault(lbl, data['facts_by_raw_key'][rk])
            data['facts_by_label'] = facts_by_label

            record.normalized_data = data
            new_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            record.data_hash = new_hash
            
            status = 'APPROVED' if all_approved else 'PARTIAL'
            mark_docs_label_normalized([doc_id], status)
            flag_modified(record, 'normalized_data')
            
        session.commit()
    finally:
        session.close()
