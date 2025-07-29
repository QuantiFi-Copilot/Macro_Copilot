# earnings_agent/parsing/nse_scraper/nse_scraper_parser_task.py
# Parser script for transforming raw NSE API JSON into a standardized format.
# Version: 2.1.0 (Aligned with Standard Pipeline Architecture + unit metadata)
# Description:
#   This parser propagates unit metadata for NSE_SCRAPER filings so that the
#   downstream normalizer/QA can treat NSE and XBRL uniformly.
#   - We DO NOT convert or normalize values here.
#   - Monetary facts are assumed to be reported in Lakhs; we record this as
#     assumed_decimals = -5 and assumed_scale = "Lakhs".
#   - Ratios use unitRef = "pure"; EPS/per-share use INRPerShare.
#   - Admin/meta fields are passed through as plain strings.
#
# Output content shape (examples):
# {
#   "presentation_currency": "INR",
#   "rounding_level": "Lakhs",
#   "rounding_confidence": "validated_by_profile",
#   "source_period_end": "2024-12-31",
#   "source_period_range": "2024-10-01/2024-12-31",
#   "source_seqnum": "1190337",
#   "source_filing_date": "23-Jan-2025 18:59",
#   "source_longname": "UltraTech Cement Limited",
#   "re_net_sale": {
#       "value": "1719333",
#       "unitRef": "INR",
#       "unit_measure": "iso4217:INR",
#       "assumed_decimals": -5,
#       "assumed_scale": "Lakhs",
#       "decimals": null,
#       "contextRef": "NSEPeriod_2024-12-31",
#       "unit_inferred": true,
#       "unit_inference_basis": "profile",
#       "representation": "currency"
#   },
#   "re_bsc_eps_bfr_exi": {
#       "value": "50.99",
#       "unitRef": "INRPerShare",
#       "unit_measure": "iso4217:INR/xbrli:shares",
#       "decimals": null,
#       "contextRef": "NSEPeriod_2024-12-31",
#       "unit_inferred": true,
#       "unit_inference_basis": "profile",
#       "representation": "per_share"
#   },
#   "re_debt_ser_cov": {
#       "value": "0.06",
#       "unitRef": "pure",
#       "unit_measure": "xbrli:pure",
#       "decimals": null,
#       "contextRef": "NSEPeriod_2024-12-31",
#       "unit_inferred": true,
#       "unit_inference_basis": "profile",
#       "representation": "ratio"
#   }
# }

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, and_
from sqlalchemy.orm import Session as SQLAlchemySession

# Internal project imports
from earnings_agent.storage.database import get_session, create_parsed_document
from earnings_agent.storage.models import RawDataAsset, ParsedDocument

# --- Standard Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- Configuration & Constants ---
PARSER_VERSION = "2.1.0"
SOURCE_TYPE_FILTER = "NSE_SCRAPER"

DEFAULT_ROUNDING_LEVEL = "Lakhs"
DEFAULT_ROUNDING_CONFIDENCE = "validated_by_profile"  # backed by cross-source checks
DEFAULT_UNIT_INFERENCE_BASIS = "profile"  # how units were inferred for NSE source

# Known key classes
MONETARY_KEYS = {
    "re_net_sale", "re_total_inc", "re_oth_tot_exp", "re_rawmat_consump", "re_staff_cost",
    "re_depr_und_exp", "re_pur_trd_goods", "re_curr_tax", "re_deff_tax", "re_tax",
    "re_pro_loss_bef_tax", "re_pro_bef_int_n_excep", "re_con_pro_loss", "re_proloss_ord_act",
    "re_oth_exp", "re_oth_inc_new", "re_tot_com_ic", "re_oth_cmpr_incm", "re_tot_cmpr_incm",
    "re_pl_own_par", "re_tot_pl_nci", "re_share_associate", "re_net_mov_reg",
}

PER_SHARE_KEYS = {
    "re_face_val", "re_basic_eps", "re_diluted_eps",
    "re_bsc_eps_bfr_exi", "re_dil_eps_bfr_exi",
    "re_basic_eps_for_cont_dic_opr", "re_dilut_eps_for_cont_dic_opr",
}

PURE_RATIO_KEYS = {
    "re_debt_eqt_rat", "re_debt_ser_cov", "re_int_ser_cov",
}

# Admin/meta passthrough keys (kept as plain values)
META_PASSTHROUGH_KEYS = {
    "re_seq_num", "seqnum",
    "re_remarks", "re_seg_remarks",
    "re_desc_note_fin", "re_desc_note_seg",
}

# Lower-cased mirrors for robust checks
MONETARY_KEYS_LC = {k.lower() for k in MONETARY_KEYS}
PER_SHARE_KEYS_LC = {k.lower() for k in PER_SHARE_KEYS}
PURE_RATIO_KEYS_LC = {k.lower() for k in PURE_RATIO_KEYS}
META_PASSTHROUGH_KEYS_LC = {k.lower() for k in META_PASSTHROUGH_KEYS}

# Placeholders to keep as plain strings
PLACEHOLDER_STRINGS = {"", "-", "—", "NA", "N.A.", "na", "n.a."}

# Parentheses negative like "(123.45)"
PAREN_NUM = re.compile(r"^\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)$")

# Variations of seqnum
SEQNUM_RE = re.compile(r'^re?_?seq_?num$', re.IGNORECASE)


# --- Helper Functions ---

def parse_date_dmy_mon(s: Optional[str]) -> Optional[str]:
    """Parse '31-Dec-2024' -> '2024-12-31'. Returns ISO date or None."""
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.strptime(s, "%d-%b-%Y")
        return dt.date().isoformat()
    except Exception:
        return None


def parse_range_dmy_mon(s: Optional[str]) -> Optional[Tuple[str, str]]:
    """Parse '01-Apr-2024 To 31-Mar-2025' -> ('2024-04-01', '2025-03-31') or None."""
    if not s:
        return None
    parts = re.split(r"\b[Tt]o\b", s)
    if len(parts) != 2:
        return None
    start_raw = parts[0].strip()
    end_raw = parts[1].strip()
    start_iso = parse_date_dmy_mon(start_raw)
    end_iso = parse_date_dmy_mon(end_raw)
    if start_iso and end_iso:
        return start_iso, end_iso
    return None


def is_numeric_string(s: str) -> bool:
    return bool(re.match(r"^-?\d+(\.\d+)?$", s.strip()))


def classify_key(key: str, sval: str) -> str:
    """Return 'monetary' | 'per_share' | 'pure'."""
    kl = (key or "").strip().lower()
    if kl in PER_SHARE_KEYS_LC or re.search(r"(eps|face_val|facevalue)", kl):
        return "per_share"
    if kl in PURE_RATIO_KEYS_LC or re.search(r"(rat|ratio|cov|coverage)", kl):
        return "pure"
    if kl in MONETARY_KEYS_LC:
        return "monetary"
    # heuristic: if numeric and small magnitude + decimals => pure
    if is_numeric_string(sval):
        try:
            v = float(sval)
            if abs(v) < 5 and "." in sval:
                return "pure"
        except Exception:
            pass
    return "monetary"


def emit_numeric(key: str, sval: str, context_ref: str, forced_zero_ratio: bool = False) -> Dict[str, Any]:
    """Build the enriched numeric object with unit metadata."""
    klass = classify_key(key, sval)
    out: Dict[str, Any] = {
        "value": sval,
        "contextRef": context_ref,
        "decimals": None,
        # Explicitly mark that NSE units are inferred, not provided by source
        "unit_inferred": True,
        "unit_inference_basis": DEFAULT_UNIT_INFERENCE_BASIS,
    }

    if klass == "per_share":
        out.update({
            "unitRef": "INRPerShare",
            "unit_measure": "iso4217:INR/xbrli:shares",
            "representation": "per_share",
        })
    elif klass == "pure":
        out.update({
            "unitRef": "pure",
            "unit_measure": "xbrli:pure",
            "representation": "ratio",
        })
        if forced_zero_ratio and sval in {"0", "0.0", "0.00"}:
            out["reported_zero_due_to_template_limit"] = True
    else:  # monetary
        out.update({
            "unitRef": "INR",
            "unit_measure": "iso4217:INR",
            "assumed_decimals": -5,      # Lakhs
            "assumed_scale": "Lakhs",
            "representation": "currency",
        })
    return out


# --- Core Parsing Logic ---

def parse_nse_api_asset(asset_id: int, session: SQLAlchemySession):
    """
    Processes a single raw data asset from the NSE API, emitting raw facts with
    unit metadata (no unit conversion).
    """
    logging.info(f"--- Processing Asset ID: {asset_id} ---")
    try:
        asset = session.get(RawDataAsset, asset_id)
        if not asset or not asset.data_content:
            raise ValueError(f"Asset {asset_id} is invalid or has no data_content.")

        data = asset.data_content or {}
        rd2 = data.get("resultsData2") or data.get("resultsData") or {}
        if not rd2:
            raise ValueError("No financial results found in 'resultsData2' or 'resultsData'.")

        # period fields and notes
        period_end_iso = parse_date_dmy_mon(data.get("periodEndDT"))
        rng_text = data.get("finresultDate")
        rng_parsed = parse_range_dmy_mon(rng_text)
        period_range = f"{rng_parsed[0]}/{rng_parsed[1]}" if rng_parsed else None

        # detect "template forced zero" in notes
        try:
            note_fin_text = str(rd2.get("re_desc_note_fin") or data.get("notes") or "")
        except Exception:
            note_fin_text = ""
        note_fin_lc = note_fin_text.lower()
        forced_zero_ratio = (
            ("entered as '0.00'" in note_fin_lc) or
            ("entered as 0.00" in note_fin_lc) or
            ("acceptable limit" in note_fin_lc)
        )

        context_ref = f"NSEPeriod_{period_end_iso}" if period_end_iso else "NSEPeriod"

        enriched: Dict[str, Any] = {}

        for key, sval in rd2.items():
            # normalize to string, preserve None
            if sval is None:
                enriched[key] = None
                continue
            if isinstance(sval, (int, float)):
                sval = str(sval)

            kl = (key or "").strip().lower()

            # passthrough for metadata/admin fields
            if kl in META_PASSTHROUGH_KEYS_LC or SEQNUM_RE.match(key or ""):
                # keep as plain value (unwrap dict if somehow present)
                if isinstance(sval, dict):
                    enriched[key] = sval.get("value") if "value" in sval else str(sval)
                else:
                    enriched[key] = sval
                continue

            # placeholder strings remain plain
            if isinstance(sval, str) and sval.strip() in PLACEHOLDER_STRINGS:
                enriched[key] = sval
                continue

            # handle parentheses negatives "(123.45)"
            if isinstance(sval, str):
                m = PAREN_NUM.match(sval.strip())
                if m:
                    norm = "-" + m.group(1)
                    obj = emit_numeric(key, norm, context_ref, forced_zero_ratio=forced_zero_ratio)
                    obj["original_value"] = sval
                    enriched[key] = obj
                    continue

            # numeric?
            if isinstance(sval, str) and is_numeric_string(sval):
                enriched[key] = emit_numeric(key, sval, context_ref, forced_zero_ratio=forced_zero_ratio)
            else:
                enriched[key] = sval

        # Build final content
        content: Dict[str, Any] = {
            "presentation_currency": "INR",
            "rounding_level": DEFAULT_ROUNDING_LEVEL,
            "rounding_confidence": DEFAULT_ROUNDING_CONFIDENCE,
            "source_period_end": period_end_iso,
            "source_period_range": period_range,
            "source_seqnum": data.get("seqnum") or rd2.get("re_seq_num"),
            "source_filing_date": data.get("filingDate"),
            "source_longname": data.get("longname"),
            **enriched,
        }

        doc_data = {
            "asset_id": asset_id,
            "parser_version": PARSER_VERSION,
            "parse_status": "PARSED_OK",
            "content": content,
        }
        create_parsed_document(doc_data)
        logging.info(f"✅ Successfully parsed and stored result for Asset ID: {asset_id}")

    except Exception as e:
        logging.error(f"❌ An error occurred parsing Asset ID {asset_id}: {e}", exc_info=False)
        doc_data = {
            "asset_id": asset_id,
            "parser_version": PARSER_VERSION,
            "parse_status": "PARSING_ERROR",
            "error_details": str(e),
        }
        create_parsed_document(doc_data)
        logging.error(f"   Created PARSING_ERROR record for Asset ID: {asset_id}")


# --- Main Batch Processing Logic ---

def run_parser_batch():
    """
    Finds and processes all unprocessed NSE_SCRAPER assets in the database.
    This is the production-ready entry point for the task.
    """
    logging.info(f"--- Starting NSE Parser Batch Run v{PARSER_VERSION} ---")
    session = get_session()
    try:
        # Only reprocess if this specific parser version hasn't run
        subquery = select(ParsedDocument.asset_id).where(ParsedDocument.parser_version == PARSER_VERSION)

        stmt = select(RawDataAsset.asset_id).where(
            and_(
                RawDataAsset.source_type == SOURCE_TYPE_FILTER,
                RawDataAsset.asset_id.notin_(subquery),
            )
        )
        asset_ids_to_process = session.execute(stmt).scalars().all()

        if not asset_ids_to_process:
            logging.info("No new NSE API assets to process. Exiting.")
            return

        logging.info(f"Found {len(asset_ids_to_process)} unprocessed NSE assets. Starting batch.")

        for asset_id in asset_ids_to_process:
            try:
                parse_nse_api_asset(asset_id, session)
            except Exception as e:
                logging.critical(f"A critical error occurred in main loop for asset_id {asset_id}: {e}", exc_info=True)

        logging.info("--- Batch run completed successfully. ---")
    finally:
        session.close()


if __name__ == '__main__':
    run_parser_batch()