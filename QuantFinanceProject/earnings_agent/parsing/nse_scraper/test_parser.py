#!/usr/bin/env python3
"""
Standalone NSE parser test.
- Picks 5 random NSE_SCRAPER assets from the DB and prints enriched JSON.
- Does NOT write to the DB.
- Alternatively, use --file to load a single JSON payload from disk.

Usage:
  python test_nse_parser.py                         # fetch 5 random from DB
  python test_nse_parser.py --limit 10              # fetch 10 random
  python test_nse_parser.py --file sample.json      # no DB, parse given file
  python test_nse_parser.py --log-level INFO
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Try to use your project's SQLAlchemy session if available.
# Falls back to plain psycopg2 if get_session import fails and DATABASE_URL is present.
DB_AVAILABLE = True
try:
    from earnings_agent.storage.database import get_session
    from earnings_agent.storage.models import RawDataAsset, JobAssetLink, IngestionJob
except Exception:
    DB_AVAILABLE = False

try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None

# -------------------- Configuration --------------------

DEFAULT_LIMIT = 5
DEFAULT_ROUNDING_LEVEL = "Lakhs"
DEFAULT_ROUNDING_CONFIDENCE = "validated_by_profile"  # we validated via cross-source ratios ≈ 1e5

MONETARY_KEYS = {
    "re_net_sale", "re_total_inc", "re_oth_tot_exp", "re_rawmat_consump", "re_staff_cost",
    "re_depr_und_exp", "re_pur_trd_goods", "re_curr_tax", "re_deff_tax", "re_tax",
    "re_pro_loss_bef_tax", "re_pro_bef_int_n_excep", "re_con_pro_loss", "re_proloss_ord_act",
    "re_oth_exp", "re_oth_inc_new", "re_tot_com_ic", "re_oth_cmpr_incm", "re_tot_cmpr_incm",
    "re_pl_own_par", "re_tot_pl_nci", "re_share_associate", "re_net_mov_reg",
    # add more if encountered; unknown numeric keys fall back to MONETARY
}

PER_SHARE_KEYS = {
    "re_face_val", "re_basic_eps", "re_diluted_eps",
    "re_bsc_eps_bfr_exi", "re_dil_eps_bfr_exi",
    "re_basic_eps_for_cont_dic_opr", "re_dilut_eps_for_cont_dic_opr",
}

PURE_RATIO_KEYS = {
    "re_debt_eqt_rat", "re_debt_ser_cov", "re_int_ser_cov",
}

# lower-cased lookup sets for robust matching
MONETARY_KEYS_LC = {k.lower() for k in MONETARY_KEYS}
PER_SHARE_KEYS_LC = {k.lower() for k in PER_SHARE_KEYS}
PURE_RATIO_KEYS_LC = {k.lower() for k in PURE_RATIO_KEYS}

# passthrough meta keys (admin/notes, not numeric)
META_PASSTHROUGH_KEYS = {
    "re_seq_num",       # NSE sequence id
    "seqnum",           # top-level variant
    "re_remarks",
    "re_seg_remarks",
    "re_desc_note_fin",
    "re_desc_note_seg",
}

META_PASSTHROUGH_KEYS_LC = {k.lower() for k in META_PASSTHROUGH_KEYS}

# placeholders that should be treated as plain text (not numeric)
PLACEHOLDER_STRINGS = {"", "-", "—", "NA", "N.A.", "na", "n.a."}

# parentheses negative like "(123.45)" -> "-123.45"
PAREN_NUM = re.compile(r"^\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)$")

# match variations like re_seq_num, reseqnum, re-seq-num (defensive)
SEQNUM_RE = re.compile(r'^re?_?seq_?num$', re.IGNORECASE)

# -------------------- Helpers --------------------

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
    klass = classify_key(key, sval)
    out: Dict[str, Any] = {"value": sval, "contextRef": context_ref, "decimals": None}

    if klass == "per_share":
        out.update({
            "unitRef": "INRPerShare",
            "unit_measure": "iso4217:INR/xbrli:shares",
        })
    elif klass == "pure":
        out.update({
            "unitRef": "pure",
            "unit_measure": "xbrli:pure",
        })
        # Some NSE notes indicate ratios were entered as 0.00 due to template limits.
        if forced_zero_ratio and sval in {"0", "0.0", "0.00"}:
            out["reported_zero_due_to_template_limit"] = True
    else:  # monetary
        out.update({
            "unitRef": "INR",
            "unit_measure": "iso4217:INR",
            "assumed_decimals": -5,      # Lakhs
            "assumed_scale": "Lakhs",
        })
    return out

@dataclass
class NSERecord:
    asset_id: Optional[int]
    ticker: Optional[str]
    data: Dict[str, Any]

def enrich_nse_record(rec: NSERecord,
                      rounding_level: str = DEFAULT_ROUNDING_LEVEL,
                      rounding_confidence: str = DEFAULT_ROUNDING_CONFIDENCE) -> Dict[str, Any]:
    data = rec.data or {}
    rd2 = data.get("resultsData2") or data.get("resultsData") or {}

    # period fields
    period_end_iso = parse_date_dmy_mon(data.get("periodEndDT"))
    rng = data.get("finresultDate")
    rng_parsed = parse_range_dmy_mon(rng)
    period_range = None
    if rng_parsed:
        period_range = f"{rng_parsed[0]}/{rng_parsed[1]}"

    context_ref = f"NSEPeriod_{period_end_iso}" if period_end_iso else "NSEPeriod"
    enriched: Dict[str, Any] = {}

    note_fin_text = ""
    try:
        note_fin_text = str(rd2.get("re_desc_note_fin") or data.get("notes") or "")
    except Exception:
        note_fin_text = ""
    note_fin_lc = note_fin_text.lower()
    forced_zero_ratio = ("entered as '0.00'" in note_fin_lc) or ("entered as 0.00" in note_fin_lc) or ("acceptable limit" in note_fin_lc)

    # copy non-numeric as plain values; numeric as enriched objects
    for key, sval in rd2.items():
        if sval is None:
            enriched[key] = None
            continue
        if isinstance(sval, (int, float)):
            sval = str(sval)

        kl = (key or "").strip().lower()

        # passthrough for metadata/admin fields (keep as plain values)
        if kl in META_PASSTHROUGH_KEYS_LC or SEQNUM_RE.match(key or ""):
            if isinstance(sval, dict):
                # if somehow a dict shows up, unwrap a sensible value
                enriched[key] = sval.get("value") if "value" in sval else json.dumps(sval, ensure_ascii=False)
            else:
                enriched[key] = sval
            continue

        # treat placeholder strings as plain text
        if isinstance(sval, str) and sval.strip() in PLACEHOLDER_STRINGS:
            enriched[key] = sval
            continue

        # convert parentheses negatives "(123.45)" -> "-123.45"
        if isinstance(sval, str):
            m = PAREN_NUM.match(sval.strip())
            if m:
                norm = "-" + m.group(1)
                obj = emit_numeric(key, norm, context_ref, forced_zero_ratio=forced_zero_ratio)
                obj["original_value"] = sval
                enriched[key] = obj
                continue

        if isinstance(sval, str) and is_numeric_string(sval):
            enriched[key] = emit_numeric(key, sval, context_ref, forced_zero_ratio=forced_zero_ratio)
        else:
            enriched[key] = sval

    content = {
        "presentation_currency": "INR",
        "rounding_level": rounding_level,
        "rounding_confidence": rounding_confidence,
        "source_period_end": period_end_iso,
        "source_period_range": period_range,
        "source_seqnum": data.get("seqnum") or rd2.get("re_seq_num"),
        "source_filing_date": data.get("filingDate"),
        "source_longname": data.get("longname"),
        **enriched,
    }
    return content

# -------------------- DB fetch logic --------------------

def fetch_random_nse_assets_sqlalchemy(limit: int) -> list[NSERecord]:
    sess = get_session()
    try:
        # Use TABLES: raw_data_assets, job_asset_link, ingestion_job
        q = (
            sess.query(RawDataAsset.asset_id, IngestionJob.ticker, RawDataAsset.data_content)
            .join(JobAssetLink, JobAssetLink.asset_id == RawDataAsset.asset_id)
            .join(IngestionJob, IngestionJob.job_id == JobAssetLink.job_id)
            .filter(RawDataAsset.source_type == 'NSE_SCRAPER')
        )
        rows = q.all()
        if not rows:
            return []
        sample = random.sample(rows, k=min(limit, len(rows)))
        return [NSERecord(asset_id=r[0], ticker=r[1], data=r[2]) for r in sample]
    finally:
        sess.close()

def fetch_random_nse_assets_psycopg(limit: int) -> list[NSERecord]:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set and SQLAlchemy get_session unavailable.")
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is not installed; cannot use DATABASE_URL fallback.")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT rda.asset_id, ij.ticker, rda.data_content
                FROM earnings_data.raw_data_assets rda
                JOIN earnings_data.job_asset_link jal ON jal.asset_id = rda.asset_id
                JOIN earnings_data.ingestion_job ij ON ij.job_id = jal.job_id
                WHERE rda.source_type = 'NSE_SCRAPER'
            """)
            rows = cur.fetchall()
        if not rows:
            return []
        sample = random.sample(rows, k=min(limit, len(rows)))
        out = []
        for r in sample:
            out.append(NSERecord(
                asset_id=r["asset_id"],
                ticker=r["ticker"],
                data=r["data_content"],
            ))
        return out
    finally:
        conn.close()

# -------------------- CLI --------------------

def main():
    ap = argparse.ArgumentParser(description="Test NSE parser: print enriched JSON for random filings.")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="number of random filings (default 5)")
    ap.add_argument("--file", type=str, help="path to a JSON file containing one NSE data_content object")
    ap.add_argument("--log-level", default="WARNING", help="logging level")
    ap.add_argument("--rounding-level", default=DEFAULT_ROUNDING_LEVEL, help="rounding_level to emit (default Lakhs)")
    ap.add_argument("--rounding-confidence", default=DEFAULT_ROUNDING_CONFIDENCE,
                    help="rounding_confidence tag (default validated_by_profile)")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING),
                        format="%(levelname)s %(message)s")

    records: list[NSERecord] = []

    if args.file:
        path = Path(args.file)
        if not path.exists():
            raise SystemExit(f"File not found: {path}")
        # The file may contain either the whole raw_data_assets row JSON or just the 'data_content' JSON.
        raw = json.loads(path.read_text())
        if "resultsData2" in raw or "seqnum" in raw:
            data_content = raw
        else:
            data_content = raw.get("data_content") or {}
        records = [NSERecord(asset_id=None, ticker=None, data=data_content)]
    else:
        # DB path
        if DB_AVAILABLE:
            records = fetch_random_nse_assets_sqlalchemy(args.limit)
        else:
            records = fetch_random_nse_assets_psycopg(args.limit)

    if not records:
        print("No NSE_SCRAPER records found.")
        return

    for rec in records:
        content = enrich_nse_record(
            rec,
            rounding_level=args.rounding_level,
            rounding_confidence=args.rounding_confidence
        )
        header = {
            "asset_id": rec.asset_id,
            "ticker": rec.ticker,
            "source_type": "NSE_SCRAPER",
        }
        print("\n" + "=" * 120)
        print(json.dumps(header, indent=2))
        print(json.dumps(content, indent=2, ensure_ascii=False))

        # quick unit summary
        unit_refs = []
        for v in content.values():
            if isinstance(v, dict):
                ur = v.get("unitRef")
                if ur:
                    unit_refs.append(ur)
        if unit_refs:
            from collections import Counter
            cnt = Counter(unit_refs)
            print("UnitRef summary:", dict(cnt))

if __name__ == "__main__":
    main()