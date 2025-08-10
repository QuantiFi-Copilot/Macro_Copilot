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
# Portable path: works both locally and inside Docker (/app)
PLAYBOOK_PATH = PLAYBOOKS_DIR / "sebi" / "metrics" / "sebi_banking.yml"
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
3.  **standard_names**: The official list of valid canonical names **for this statement only**.

**CRITICAL INSTRUCTIONS:
- Map **only** to the provided `standard_names` for this statement.
- **Never** map to abstract/heading nodes (they carry no values).**
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


class StatementPlaybookLoader:
    """
    Loads the nested SEBI banking playbook (ids + children) and exposes per-statement leaves.
    Expected YAML: one or multiple documents with top-level keys: statement, nodes.
    """
    def __init__(self, playbook_path: Path):
        self.playbook_path = playbook_path
        self.statement_trees = self._load_all_statements(playbook_path)
        # Precompute leaves (exclude Abstract parents)
        self.statement_leaves = {k: self._flatten_leaves(v) for k, v in self.statement_trees.items()}

    def _load_all_statements(self, path: Path) -> dict:
        trees = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                docs = list(yaml.safe_load_all(f))
        except Exception:
            docs = []
        for doc in docs:
            if isinstance(doc, dict) and doc.get("statement") and doc.get("nodes"):
                trees[doc["statement"]] = doc["nodes"]
        return trees

    def _flatten_leaves(self, nodes):
        leaves = []
        def rec(n):
            has_children = bool(n.get("children"))
            if has_children:
                for ch in n["children"]:
                    rec(ch)
            else:
                leaves.append(n["id"])
        for node in nodes:
            rec(node)
        return leaves

    def get_leaves_for(self, statement_key: str) -> list:
        # statement_key expected: 'pnl', 'balance_sheet', 'cash_flow_indirect', 'cash_flow_direct'
        return self.statement_leaves.get(statement_key, [])

    def available_statements(self):
        return list(self.statement_trees.keys())


def run_label_normalizer_discovery(allow_llm: bool = True):

    """Find new, unmapped labels per statement, get LLM suggestions, and populate the cache for human review.
       CHANGE: now runs **statement-by-statement** using the nested playbook for Banking (ELRs 100200/100300/100600/100700).
    """
    logger.info("=== Starting Label Normalizer Discovery Phase (Statement-batched) ===")
    session = get_session()
    from sqlalchemy import select
    from earnings_agent.storage.models import StagedNormalizedData
    try:
        playbook_path = PLAYBOOK_PATH
        sp_loader = StatementPlaybookLoader(playbook_path)

        docs_to_update_status = []
        processed_in_this_run = set()

        doc_ids = get_docs_pending_label_normalization()
        if not doc_ids:
            logger.info("No documents pending label normalization discovery.")
            return

        logger.info(f"Found {len(doc_ids)} documents to process for label discovery.")
        for doc_id in doc_ids:
            record = session.execute(select(StagedNormalizedData).where(StagedNormalizedData.doc_id==doc_id)).scalar_one_or_none()
            if not record:
                continue
            company_context = get_company_context(session, record.ticker)
            if not company_context:
                logger.warning(f"Skipping doc_id {doc_id} ({record.ticker}): No company context found.")
                continue

            industry = company_context.classification.industry_name  # keep using industry for cache key
            # Skip if the industry has no matching playbook (Banking only for now)
            if not industry or industry.lower() not in {"banking", "banks"}:
                logger.info(
                    f"Skipping doc_id {doc_id}: industry '{industry}' has no playbook."
                )
                continue
            unit_data = record.normalized_data.get('unit_normalized_data', {}).get('llm_unit_analysis', {})
            stmt_analyses = unit_data.get('statement_analyses', [])

            if not stmt_analyses:
                logger.info(f"No statement analyses found for doc_id {doc_id}.")
                continue

            for sa in stmt_analyses:
                std_map = sa.get('standard_mapping', '') or ''
                figures = sa.get('figures', []) or []
                if not figures:
                    continue

                # Map standard_mapping to playbook statement key
                std_lower = std_map.lower()
                if 'pnl' in std_lower or 'income' in std_lower:
                    stmt_key = 'pnl'
                elif 'balance' in std_lower:
                    stmt_key = 'balance_sheet'
                elif 'cash' in std_lower:
                    # Heuristic to choose indirect vs direct
                    labels_text = " ".join((f.get('label') or '').lower() for f in figures)
                    if any(k in labels_text for k in ['profit before', 'extraordinary', 'adjustments', 'working capital']):
                        stmt_key = 'cash_flow_indirect'
                    elif any(k in labels_text for k in ['receipts from', 'payments to', 'operating activities - receipts']):
                        stmt_key = 'cash_flow_direct'
                    else:
                        stmt_key = 'cash_flow_indirect'  # default for Indian banks
                else:
                    # Unknown statement, skip
                    logger.info(f"Unknown statement mapping '{std_map}' for doc_id {doc_id}; skipping batch.")
                    continue

                standard_names = sp_loader.get_leaves_for(stmt_key)
                if not standard_names:
                    logger.warning(f"No playbook leaves found for statement '{stmt_key}'.")
                    continue

                raw_labels_this_stmt = list({(fig.get('label') or '').strip() for fig in figures if fig.get('label')})

                # Determine which labels are new relative to cache
                new_labels_to_process = []
                for label in raw_labels_this_stmt:
                    if (label, industry) not in processed_in_this_run and not get_label_mapping(label, industry):
                        new_labels_to_process.append(label)

                if not new_labels_to_process:
                    continue

                if allow_llm:
                    logger.info(f"[doc {doc_id}] {record.ticker} | {std_map} | batching {len(new_labels_to_process)} new labels")
                    context_payload = json.dumps({
                        "industry": industry,
                        "statement_key": stmt_key,
                        "statement_name": sa.get('statement_type'),
                        "statement_currency": sa.get('statement_currency'),
                        "standard_names": standard_names,
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
                                "source_context": {
                                    'doc_id': doc_id,
                                    'ticker': record.ticker,
                                    'statement_key': stmt_key,
                                    'standard_mapping': std_map
                                }
                            })
                            processed_in_this_run.add((label, industry))
                    except Exception as e:
                        logger.error(f"LLM call failed for doc_id {doc_id} / {std_map}: {e}")

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
    from sqlalchemy import select
    from earnings_agent.storage.models import StagedNormalizedData
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