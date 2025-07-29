import re
import hashlib
from typing import Any, Dict, List, Tuple, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from earnings_agent.storage.database import get_session, create_staged_normalized_data
from earnings_agent.storage.models import ParsedDocument, RawDataAsset, StagedNormalizedData
from earnings_agent.storage.models import IngestionJob, JobAssetLink # <-- Add these imports at the top

# In unit_normalizer.py

# --- Add these imports at the top of the file ---
from datetime import date
from earnings_agent.storage.models import IngestionJob, JobAssetLink
# scale factors for document-level rounding (XBRL) and assumed scales (NSE)
SCALE_MAP = {
    'lakhs': 100_000,
    'crores': 10_000_000,
    'millions': 1_000_000,
    'thousands': 1_000
}

# Heuristic patterns
NAME_RATIO_PATTERN = re.compile(r"(returnon|ratio|margin|rate|yield|percentage)", re.IGNORECASE)

# ----------------------
# Representation Classifier
# ----------------------
def classify_representation(fact: Dict[str, Any]) -> str:
    """
    Decide representation: 'ratio', 'per_share', or 'currency'.
    """
    dt = (fact.get("data_type") or "").lower()
    name = fact.get("concept", "")
    orig_unit = (fact.get("original_unitRef") or "").lower()
    try:
        value = float(fact.get("value", 0))
    except Exception:
        value = None

    # Strong signals from taxonomy
    if "percentitemtype" in dt:
        return "ratio"
    if "pershareitemtype" in dt:
        return "per_share"
    if "monetaryitemtype" in dt or "xbrli:monetaryItemType" in dt:
        return "currency"
    # fallback on original unit
    if orig_unit == "pure":
        return "ratio"
    # name + small value fallback
    if value is not None and -1 < value < 1 and NAME_RATIO_PATTERN.search(name):
        return "ratio"
    return "currency"

# ----------------------
# Normalization Helpers
# ----------------------
def compute_tolerance(fact: Dict[str, Any], representation: str) -> float:
    """Compute comparison tolerance based on decimals or assumed_decimals."""
    if representation == "ratio":
        return 1e-9
    # per_share small
    if representation == "per_share":
        return 0.01
    # currency
    dec = fact.get("decimals") or fact.get("original_decimals")
    assumed_dec = fact.get("assumed_decimals")
    # infer scale tolerance
    if assumed_dec is not None:
        # e.g. Lakhs -> -5
        try:
            tol = 0.5 * (10 ** abs(int(assumed_dec)))
            return tol
        except Exception:
            pass
    if dec and dec.upper() == "INF":
        return 0.01
    if dec:
        try:
            tol = 0.5 * (10 ** abs(int(dec)))
            return tol
        except Exception:
            pass
    return 1.0

def normalize_value(fact: Dict[str, Any], doc_meta: Dict[str, Any]) -> Tuple[float, str, float, List[str], Dict[str, Any]]:
    """
    Convert raw fact to (value, unit, tolerance, flags, trace).
    trace captures original metadata and decisions.
    """
    rep = classify_representation(fact)
    raw_val = 0.0
    try:
        raw_val = float(fact.get("value", 0))
    except Exception:
        pass
    trace: Dict[str, Any] = {
        "concept": fact.get("concept"),
        "original_unitRef": fact.get("original_unitRef"),
        "original_decimals": fact.get("original_decimals"),
        "data_type": fact.get("data_type"),
        "representation": rep,
        "flags": []
    }
    # ratio
    if rep == "ratio":
        val = raw_val
        # correct cases like '5' meaning 5%
        if 'percentitemtype' in (fact.get('data_type') or '').lower() and val > 1:
            val = val / 100.0
            trace['flags'].append('DIVIDED_PERCENT_BY_100')
        if (fact.get("original_unitRef") or "").lower() != "pure":
            trace["flags"].append("UNIT_CONCEPT_MISMATCH")
        tol = compute_tolerance(fact, rep)
        unit_out = "fraction"
        return val, unit_out, tol, trace["flags"], trace
    # per_share
    if rep == "per_share":
        val = raw_val
        tol = compute_tolerance(fact, rep)
        unit_out = "INRPerShare"
        if not fact.get("original_unitRef"):
            trace["flags"].append("MISSING_UNIT")
        return val, unit_out, tol, trace["flags"], trace
    
    # currency
    # --- FIX STARTS HERE ---
    # Get the rounding level object, which might be a dict from XBRL parsed data.
    rounding_level_obj = doc_meta.get('rounding_level')
    # Safely extract the string value. If it's a dict, get obj['value']. Otherwise, use the object itself.
    rounding_level_str = rounding_level_obj.get('value') if isinstance(rounding_level_obj, dict) else rounding_level_obj
    
    # Now, use the safe string in the original logic.
    scale_key = (fact.get('assumed_scale') or rounding_level_str or '').lower()
    # --- FIX ENDS HERE ---
    
    factor = SCALE_MAP.get(scale_key, 1)
    # compute normalized value
    val = raw_val * factor
    # compute tolerance
    tol = compute_tolerance(fact, rep)
    unit_out = 'INR'
    # flag missing unit or explicit missing_unit in XBRL
    if fact.get('missing_unit') or not fact.get('original_unitRef'):
        trace['flags'].append('MISSING_UNIT_MONETARY')
        # fall back to document currency if available
        trace['assumed_unitRef'] = doc_meta.get('presentation_currency') or doc_meta.get('rounding_level') or 'INR'
    # record scale factor
    trace['scale_factor_applied'] = factor
    return val, unit_out, tol, trace['flags'], trace

# ----------------------
# Document-level normalization
# ----------------------
def unit_normalize_document(doc_id: int, session: Session) -> Dict[str, Any]:
    """
    Load parsed_document, normalize each fact, build normalized_data JSON.
    """
    pd = session.get(ParsedDocument, doc_id)
    if not pd:
        return {} # Return empty dict if document not found
    
    content = pd.content or {}
    # document-level metadata
    doc_meta = {
        "presentation_currency": content.get("presentation_currency"),
        "rounding_level": content.get("rounding_level")
    }
    result: Dict[str, Any] = {"facts_by_raw_key": {}}
    # iterate facts
    for key, val in content.items():
        if not isinstance(val, dict) or "value" not in val:
            continue
        fact = {**val, "concept": key}
        val_norm, unit_out, tol, flags, trace = normalize_value(fact, doc_meta)
        result["facts_by_raw_key"][key] = {
            "normalized_unit": unit_out,
            "normalized_value": val_norm,
            "tolerance": tol,
            "flags": flags,
            "trace": trace
        }
    return result

# ----------------------
# Batch runner
# ----------------------
# In unit_normalizer.py

def run_unit_normalizer_batch():
    """
    Find unprocessed parsed_documents, normalize them, and upsert to staged_normalized_data.
    """
    session = get_session()
    try:
        subquery = select(StagedNormalizedData.doc_id)

        # --- MODIFICATION ---
        # Query for all necessary metadata directly from the database.
        # This is the most robust way to get ticker, year, and quarter.
        stmt = select(
            ParsedDocument.doc_id,
            IngestionJob.ticker,
            IngestionJob.fiscal_year,
            IngestionJob.quarter
        ).join(
            RawDataAsset, ParsedDocument.asset_id == RawDataAsset.asset_id
        ).join(
            JobAssetLink, RawDataAsset.asset_id == JobAssetLink.asset_id
        ).join(
            IngestionJob, JobAssetLink.job_id == IngestionJob.job_id
        ).where(
            ParsedDocument.parse_status == 'PARSED_OK',
            ~ParsedDocument.doc_id.in_(subquery)
        )
        docs_to_process = session.execute(stmt).all()

        # The loop now gets all required metadata from our robust query.
        for doc_id, ticker, fiscal_year, quarter in docs_to_process:
            
            # --- MODIFICATION ---
            # Calculate the fiscal_date deterministically. No more guessing from content.
            if quarter == 1:
                fiscal_date = date(fiscal_year, 6, 30)
            elif quarter == 2:
                fiscal_date = date(fiscal_year, 9, 30)
            elif quarter == 3:
                fiscal_date = date(fiscal_year, 12, 31)
            else:  # Quarter 4
                fiscal_date = date(fiscal_year + 1, 3, 31)

            norm = unit_normalize_document(doc_id, session)
            if not norm:
                continue
            
            h = hashlib.sha256(str(norm).encode()).hexdigest()
            
            create_staged_normalized_data({
                "doc_id": doc_id,
                "ticker": ticker,
                "fiscal_date": fiscal_date, # <-- This is now guaranteed to have a value.
                "normalized_data": norm,
                "data_hash": h,
                "unit_normalized": True
            })
    finally:
        session.close()

if __name__ == '__main__':
    run_unit_normalizer_batch()