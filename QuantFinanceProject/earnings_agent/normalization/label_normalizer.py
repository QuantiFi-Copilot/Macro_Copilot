# earnings_agent/normalization/label_normalizer.py

import logging
import yaml
from pathlib import Path
import json
from typing import Dict, Any, List, Set

from earnings_agent.storage.database import (
    get_session,
    get_company_context,
    get_label_mapping,
    upsert_label_mapping,
    get_docs_pending_label_normalization,
    get_docs_pending_label_review,
    mark_docs_label_review_status
)
from earnings_agent.storage.models import StagedNormalizedData
from earnings_agent.llm.normalizer_client import call_gemini_with_json
from sqlalchemy.orm.attributes import flag_modified

# --- Configuration ---
PLAYBOOKS_DIR = Path(__file__).resolve().parents[1] / "playbooks"
LABEL_NORMALIZATION_MODEL = "gemini-2.5-pro"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
logger = logging.getLogger(__name__)

# --- LLM Prompt ---
LABEL_NORMALIZATION_PROMPT = """
You are an expert financial analyst and data modeler specializing in Indian financial statements. 
Your task is to map a raw financial statement label to its single best-matching canonical name from a provided list of standard names (the playbook).

You will be provided with:
1.  **raw_labels**: A list of exact label texts from the financial statement.
2.  **industry**: The industry of the company (e.g., "Banking", "IT - Software").
3.  **standard_names**: The official list of valid canonical names for this industry.

**CRITICAL INSTRUCTIONS:**
1.  **ANALYZE FINANCIAL MEANING, NOT JUST WORDS:** Do not perform a simple semantic search. Understand the financial concept behind the raw_label. Is it a top-line revenue item, an operating expense, a non-recurring item, a balance sheet asset? Your mapping must be financially correct.
2.  **BE SPECIFIC, DO NOT GENERALIZE:** This is the most important rule. If a specific mapping exists, you must use it. For example:
    - If `raw_label` is "Revenue from Power Segment" and the `standard_names` list contains `segment_revenue`, you MUST map to `segment_revenue`. Mapping to the more general `revenue` would be a critical error.
    - Only map to a general term like `revenue` if the raw label itself is general (e.g., "Total Revenue from Operations").
3.  **HANDLE NEGATION AND EXCEPTIONS:** Pay close attention to terms like "excluding," "net of," "before," or "after." The mapping must reflect these qualifications.
4.  **NO CONFIDENT MATCH:** If you cannot find a single, high-confidence match in the `standard_names` list for a given label, you MUST return null for its mapping. Do not guess.
5. **AVOID PARENT-CHILD MISMATCHES:** This is crucial. A specific component should NOT be mapped to its broader parent category if a more specific mapping is available. For example:
    - **WRONG:** `raw_label: "(i) Employees cost"` -> `mapping: "operating_expenses"`. (Employee cost is PART OF operating expenses, not equal to it).
    - **CORRECT:** `raw_label: "(i) Employees cost"` -> `mapping: "employee_cost"` (if available in the playbook).
    - **CORRECT:** `raw_label: "(i) Employees cost"` -> `mapping: null` (if `employee_cost` is NOT in the playbook).

Return your response as a single, valid JSON object where keys are the raw_labels and values are the mapped standard_name or null.
Example Format:
{
  "Profit Before Exceptional Items and Tax": "profit_before_tax",
  "Some Unmappable Obscure Label": null
}
"""

class PlaybookLoader:
    """Handles loading and merging of global and industry-specific playbooks."""
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.global_playbook = self._load_yaml(base_dir / "global" / "core_metrics.yml")

    def _load_yaml(self, path: Path) -> dict:
        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.error(f"Error loading playbook {path}: {e}")
            return {}

    def get_playbook(self, industry_name: str) -> dict:
        """Loads and merges the playbook for a specific industry."""
        industry_file_name = industry_name.lower().replace(" ", "_").replace("&", "and") + ".yml"
        industry_playbook = self._load_yaml(self.base_dir / "industry" / industry_file_name)

        core_metrics = self.global_playbook.get('core_metrics', [])
        custom_kpis = industry_playbook.get('custom_kpis', [])
        
        return {
            "standard_names": core_metrics + custom_kpis,
            "has_industry_playbook": bool(industry_playbook)
        }

def run_label_normalizer_discovery(allow_llm: bool = True):
    """Finds new, unmapped labels, gets LLM suggestions, and populates the cache for human review."""
    logger.info("=== Starting Label Normalizer Discovery Phase ===")
    session = get_session()
    playbook_loader = PlaybookLoader(PLAYBOOKS_DIR)
    processed_in_this_run: Set[Tuple[str, str]] = set() # In-run cache: {(raw_label, industry)}
    
    try:
        doc_ids = get_docs_pending_label_normalization()
        if not doc_ids:
            logger.info("No documents pending label normalization discovery.")
            return

        logger.info(f"Found {len(doc_ids)} documents for label discovery.")
        docs_to_update_status = []

        for doc_id in doc_ids:
            record = session.query(StagedNormalizedData).filter_by(doc_id=doc_id).one()
            company_context = get_company_context(session, record.ticker)
            
            if not company_context or not company_context.classification:
                logger.warning(f"Skipping doc_id {doc_id} for {record.ticker}: No industry classification found.")
                continue
            
            industry = company_context.classification.industry_name
            playbook = playbook_loader.get_playbook(industry)
            
            if not playbook['standard_names']:
                logger.warning(f"Skipping doc_id {doc_id} for {record.ticker}: No playbook found for industry '{industry}'.")
                continue

            unit_data = record.normalized_data.get('unit_normalized_data', {}).get('llm_unit_analysis', {})
            raw_labels = {fig['label'] for stmt in unit_data.get('statement_analyses', []) for fig in stmt['figures']}
            
            # Find which labels are new and need processing
            new_labels_to_process = []
            for label in raw_labels:
                if (label, industry) not in processed_in_this_run:
                    if not get_label_mapping(label, industry):
                        new_labels_to_process.append(label)
            
            if allow_llm and new_labels_to_process:
                logger.info(f"Found {len(new_labels_to_process)} new labels for '{record.ticker}' in '{industry}' industry.")
                context_payload = json.dumps({
                    "industry": industry,
                    "standard_names": playbook['standard_names'],
                    "raw_labels": new_labels_to_process
                })

                try:
                    response_text = call_gemini_with_json(
                        model_name=LABEL_NORMALIZATION_MODEL,
                        prompt=LABEL_NORMALIZATION_PROMPT,
                        context_text=context_payload
                    )
                    llm_mappings = json.loads(response_text)

                    for label, mapped_label in llm_mappings.items():
                        upsert_label_mapping({
                            "raw_label": label,
                            "industry": industry,
                            "normalized_label": mapped_label,
                            "status": 'PENDING_REVIEW',
                            "source_context": {'doc_id': doc_id, 'ticker': record.ticker}
                        })
                        processed_in_this_run.add((label, industry))
                except Exception as e:
                    logger.error(f"LLM call failed for doc_id {doc_id}: {e}")
            
            docs_to_update_status.append(doc_id)

        if docs_to_update_status:
            mark_docs_label_review_status(docs_to_update_status, 'PENDING_REVIEW')
            logger.info(f"Marked {len(docs_to_update_status)} documents as PENDING_REVIEW.")

    finally:
        session.close()
    logger.info("=== Label Normalizer Discovery Phase Complete ===")

def run_label_normalizer_application():
    """Finds documents where all labels are approved and creates the final normalized data structure."""
    logger.info("=== Starting Label Normalizer Application Phase ===")
    session = get_session()
    try:
        doc_ids = get_docs_pending_label_review()
        if not doc_ids:
            logger.info("No documents with pending label reviews to apply.")
            return

        logger.info(f"Found {len(doc_ids)} documents to check for application.")
        successful_doc_ids = []

        for doc_id in doc_ids:
            record = session.query(StagedNormalizedData).filter_by(doc_id=doc_id).one()
            company_context = get_company_context(session, record.ticker)
            industry = company_context.classification.industry_name
            
            unit_data = record.normalized_data.get('unit_normalized_data', {}).get('llm_unit_analysis', {})
            all_raw_labels = {fig['label'] for stmt in unit_data.get('statement_analyses', []) for fig in stmt['figures']}
            
            # Critical Check: Are all labels for this document approved?
            approved_mappings = {}
            all_approved = True
            for label in all_raw_labels:
                mapping = get_label_mapping(label, industry)
                if mapping and mapping.status == 'APPROVED':
                    approved_mappings[label] = mapping.normalized_label
                else:
                    all_approved = False
                    break # No need to check further

            if not all_approved:
                continue # Skip to the next document

            # --- If all approved, build the final structure ---
            logger.info(f"All labels approved for doc_id {doc_id}. Applying normalization...")
            
            # Build a lookup for original figures to fetch the 'suspect' flag
            original_figures = {}
            stmt_norm_data = record.normalized_data.get('statement_normalized_data', {})
            for scope in ['standalone', 'consolidated']:
                for stmt_type, stmt_content in stmt_norm_data.get(scope, {}).items():
                    if isinstance(stmt_content, dict):
                        for fig in stmt_content.get('figures', []):
                            original_figures[fig['label']] = fig
            
            final_data = {'standalone': {}, 'consolidated': {}}
            for stmt in unit_data.get('statement_analyses', []):
                scope = 'standalone' if 'standalone' in stmt['standard_mapping'] else 'consolidated'
                for fig in stmt['figures']:
                    raw_label = fig['label']
                    normalized_label = approved_mappings.get(raw_label)
                    
                    if normalized_label: # Only include mapped labels
                        original_fig = original_figures.get(raw_label, {})
                        final_data[scope][normalized_label] = {
                            "value": fig['value'],
                            "representation": fig.get('representation'),
                            "currency_context": fig.get('currency_context'),
                            "suspect": original_fig.get('suspect', False),
                            "suspect_reason": original_fig.get('suspect_reason')
                        }
            
            record.normalized_data['label_normalized_data'] = final_data
            flag_modified(record, 'normalized_data')
            successful_doc_ids.append(doc_id)

        if successful_doc_ids:
            session.commit()
            mark_docs_label_review_status(successful_doc_ids, 'APPROVED')
            logger.info(f"Successfully applied label normalization for {len(successful_doc_ids)} documents.")
        else:
            logger.info("No documents were ready for full application in this run.")
            
    finally:
        session.close()
    logger.info("=== Label Normalizer Application Phase Complete ===")