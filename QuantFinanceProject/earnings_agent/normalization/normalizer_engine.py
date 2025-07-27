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
    create_staged_normalized_data
)
from earnings_agent.storage.models import ParsedDocument, JobAssetLink, IngestionJob
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

def update_staged_data_with_approved_label(label_mapping, session):
    """
    Finds all staged documents affected by a single approved label and
    surgically updates their `normalized_data` JSONB field.
    """
    from sqlalchemy import or_
    from earnings_agent.storage.models import StagedNormalizedData

    raw_label = label_mapping.raw_label
    normalized_label = label_mapping.normalized_label
    logging.info(f"Backfilling '{raw_label}' -> '{normalized_label}'")

    # 1. Find the doc_ids that contain this raw label
    affected_docs = session.query(ParsedDocument.doc_id, ParsedDocument.content).filter(
        or_(
            ParsedDocument.content.has_key(raw_label),
            ParsedDocument.content['raw_facts'].has_key(raw_label)
        )
    ).all()

    if not affected_docs:
        return 0

    # 2. For each affected document, update its corresponding staged record
    updated_count = 0
    for doc_id, parsed_content in affected_docs:
        staged_record = session.query(StagedNormalizedData).filter(StagedNormalizedData.doc_id == doc_id).first()
        if not staged_record:
            continue

        # Get the original value from the parsed content
        original_value = parsed_content.get(raw_label)
        
        # Update the JSONB field
        current_data = staged_record.normalized_data
        current_data[normalized_label] = original_value
        
        flag_modified(staged_record, "normalized_data")
        # Calculate new hash
        new_hash = hashlib.sha256(json.dumps(current_data, sort_keys=True).encode()).hexdigest()

        # Perform the update
        staged_record.normalized_data = current_data
        staged_record.data_hash = new_hash
        updated_count += 1
    
    session.commit()
    logging.info(f"Surgically updated {updated_count} staged records for label '{raw_label}'.")
    return updated_count


if __name__ == '__main__':
    def run_batch():
        logging.info(f"--- Running Normalizer Task in BATCH mode v{NORMALIZER_VERSION} ---")
        session = get_session()
        docs_to_process = []
        try:
            from sqlalchemy import select, and_
            from earnings_agent.storage.models import StagedNormalizedData
            subquery = select(StagedNormalizedData.doc_id)
            stmt = select(ParsedDocument.doc_id).where(and_(ParsedDocument.parse_status == 'PARSED_OK', ParsedDocument.doc_id.notin_(subquery)))
            docs_to_process = session.execute(stmt).scalars().all()
        finally:
            session.close()

        if docs_to_process:
            total_docs = len(docs_to_process)
            logging.info(f"Found {total_docs} new documents to normalize.")
            
            run_level_cache = {}
            
            for i, doc_id in enumerate(docs_to_process):
                logging.info(f"--- Processing document {i+1}/{total_docs} (doc_id: {doc_id}) ---")
                try:
                    normalize_document(doc_id=doc_id, run_cache=run_level_cache)
                except Exception as e:
                    logging.error(f"Failed to process doc_id {doc_id}. Error: {e}", exc_info=True)
                    continue
        else:
            logging.warning("No new, successfully parsed documents found to normalize.")
        logging.info("--- Normalizer BATCH run finished. ---")
    
    run_batch()