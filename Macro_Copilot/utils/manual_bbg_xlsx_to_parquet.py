"""Manual Bloomberg XLSX → parquet converter (generic, substrate-parameterised).

Phase B Wave 2 / BBG batch (2026-05-25). Extended for BBG warehouse seeding
session (2026-05-25 night) — supports 15 FX substrates total now.

Original BBG batch substrates (PR #198 + #203):
  - g10_forwards_long    (G10 forwards 2Y/5Y, extends fx_forwards.yml)
  - ndf                  (NDF outrights, new fx_ndf.yml)
  - em_forwards          (EM deliverable forward points, new fx_em_forwards.yml)
  - em_vol               (EM ATM vol, extends fx_vol.yml — incl. CNH/INR vol-only)
  - vol_smile_g10        (G10 smile RR/BF, new fx_vol_smile.yml)
  - vol_smile_em         (EM smile RR/BF, extends fx_vol_smile.yml)
  - spot_topup           (CNH/CNY/INR spot top-up, extends spot_fx.yml)

Warehouse seeding substrates (this PR — covers ~268 net-new instruments):
  - macro_indices        (DXY, BBDXY, JPMVXYG7, JPMVXYEM — new fx_macro_indices.yml)
  - em_ext_spot          (USDSGD/TWD/THB/CLP/COP/PEN spots, extends spot_fx.yml)
  - em_ext_ndf           (TWD NDF NTN+, extends fx_ndf.yml)
  - em_ext_forwards      (USDSGD/THB deliverable forwards, extends fx_em_forwards.yml)
  - em_ext_vol           (6 new EM ATM vol pairs × 5 tenors, extends fx_vol.yml)
  - atm_extended         (17 pairs × 5 extended tenors ON/2W/2M/9M/2Y, extends fx_vol.yml)
  - cnhinr_smile         (CNH/INR × 4 deltas × 3 tenors, extends fx_vol_smile.yml)
  - smile_extended       (13 pairs × 4 deltas × 2 new tenors 1W/6M, extends fx_vol_smile.yml)

Same Codex garde-fous as Phase B EM spot:
  1. XLSX in read-only mode, never write back
  2. Fail-loud strict validation BEFORE GCS upload (sheet count, sheet
     name format, date parsing, range bounds, no duplicates, no all-null)
  3. Excel/BDH array-formula host-cell quirk handled via opt-in
     --infer-missing-first-date (reconstructs row 0 date = row 1 - 1 BDay)
  4. Substrate-specific playbook_name + instrument_type baked into the
     parquet via the substrate config (see SUBSTRATES below)
  5. Summary table printed; interactive y/N prompt unless --yes
  6. Local parquet first; --upload required to push to GCS

Usage:
    # Convert + validate + write parquet locally (dry-run)
    python utils/manual_bbg_xlsx_to_parquet.py \
        --substrate g10_forwards_long \
        --input /Users/sachamim/Downloads/retickers-3/g10_forwards_long_extraction_20260525.xlsx \
        --infer-missing-first-date

    # Same but actually upload
    python utils/manual_bbg_xlsx_to_parquet.py \
        --substrate g10_forwards_long \
        --input /path/to/xlsx \
        --infer-missing-first-date \
        --upload \
        --yes

After upload, ingest from Docker as usual:
    docker compose exec rates-agent-dev micromamba run -n macro-env \
        env GCP_BUCKET_NAME=quantifi-fx-data-sacha \
        python ingestion/ingest_parquet.py
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import pandas as pd


# ============================================================================
# SUBSTRATE CONFIGS
# ============================================================================


@dataclass(frozen=True)
class SubstrateConfig:
    """Per-substrate config for the generic XLSX converter.

    Attributes
    ----------
    name
        Short identifier (e.g. "g10_forwards_long"). Used in CLI + logs.
    playbook_name
        Goes in parquet's `playbook_name` column. The ingester uses this
        to find the matching playbook YAML during the upsert sanity-gate.
    dataset_name
        Goes in parquet's `dataset_name` column. Should match the
        `dataset_name` in the corresponding playbook YAML.
    instrument_type
        Goes in parquet's `instrument_type` column. The ingester writes
        this to instrument_master.instrument_type for new instruments.
    sheet_name_pattern
        Compiled regex that validates each sheet name in the XLSX. The
        named groups are passed to ``ticker_builder``.
    ticker_builder
        Callable that takes the regex match.groupdict() and returns the
        vendor_ticker (e.g. "EURUSD2Y Curncy"). Pure function.
    expected_sheet_count
        Validation: total sheets in the XLSX must equal this.
    requested_start_date / requested_end_date
        ISO strings baked into the parquet for audit-trail (echoes the
        BDH formula's start/end dates).
    min_rows_per_sheet
        Each sheet must have >= this many valid rows post-cleaning.
        Looser default for vol substrates (which started later — e.g.
        CNH vol only from ~2012) vs forwards (full 2000 history).
    combine_existing_filter
        Optional WHERE-clause fragment for fetching EXISTING rows from
        the live DB and concat-ing them into the parquet before ingest.
        Use case (g10_forwards_long): the playbook fx_forwards has 30
        existing G10 tickers; we're adding 12 more (2Y/5Y). The ingest
        sanity gate refuses incoming-count < 80% of prior load. Combining
        existing + new lets us hit 42 incoming vs 30 prior → 140% pass,
        while preserving the single-playbook semantics Codex locked.
        Filter must reference `im` (instrument_master) and `d`
        (market_data_daily) aliases.
    """

    name: str
    playbook_name: str
    dataset_name: str
    instrument_type: str
    sheet_name_pattern: re.Pattern
    ticker_builder: Callable[[Dict[str, str]], str]
    expected_sheet_count: int
    requested_start_date: str = "2000-01-01"
    requested_end_date: str = "2026-05-22"
    min_rows_per_sheet: int = 500
    combine_existing_filter: Optional[str] = None


def _build_g10_forwards_long_ticker(g: Dict[str, str]) -> str:
    # sheet "EURUSD_2Y" → "EURUSD2Y Curncy"
    return f"{g['pair']}{g['tenor']} Curncy"


def _build_ndf_ticker(g: Dict[str, str]) -> str:
    # sheet "CCN_1W" → "CCN+1W Curncy"
    return f"{g['ndf_code']}+{g['tenor']} Curncy"


def _build_em_forwards_ticker(g: Dict[str, str]) -> str:
    # sheet "USDMXN_1M" → "USDMXN1M Curncy"
    return f"{g['pair']}{g['tenor']} Curncy"


def _build_em_vol_ticker(g: Dict[str, str]) -> str:
    # sheet "USDMXN_V1M" → "USDMXNV1M Curncy"
    return f"{g['pair']}V{g['tenor']} Curncy"


def _build_vol_smile_ticker(g: Dict[str, str]) -> str:
    # sheet "EURUSD_25R_1M" → "EURUSD25R1M Curncy"
    return f"{g['pair']}{g['delta']}{g['tenor']} Curncy"


def _build_spot_topup_ticker(g: Dict[str, str]) -> str:
    # sheet "USDCNH" → "USDCNH Curncy"  (spot top-up for CNH/CNY/INR)
    return f"USD{g['ccy']} Curncy"


# ============================================================================
# Warehouse seeding (2026-05-25) — 8 new substrate ticker builders
# ============================================================================

# Bloomberg suffix per macro-index code. DXY trades on the ICE futures
# exchange but uses "Curncy" yellow-key by convention; the JPMVXY* and
# BBDXY indices use "Index". Verified Phase 1 discovery 2026-05-25.
_MACRO_INDEX_SUFFIX: Dict[str, str] = {
    "DXY": "Curncy",
    "BBDXY": "Index",
    "JPMVXYG7": "Index",
    "JPMVXYEM": "Index",
}


def _build_macro_index_ticker(g: Dict[str, str]) -> str:
    # sheet "DXY" → "DXY Curncy" ; sheet "JPMVXYG7" → "JPMVXYG7 Index"
    code = g["index_code"]
    return f"{code} {_MACRO_INDEX_SUFFIX[code]}"


def _build_em_ext_spot_ticker(g: Dict[str, str]) -> str:
    # sheet "USDSGD" → "USDSGD Curncy"  (SGD/TWD/THB/CLP/COP/PEN spots)
    return f"USD{g['ccy']} Curncy"


def _build_em_ext_ndf_ticker(g: Dict[str, str]) -> str:
    # sheet "NTN_1W" → "NTN+1W Curncy"  (TWD NDF)
    return f"NTN+{g['tenor']} Curncy"


# Note: atm_extended (sheet "EURUSD_VON" → "EURUSDVON Curncy") and
# em_ext_forwards / em_ext_vol / cnhinr_smile / smile_extended REUSE the
# existing _build_em_vol_ticker / _build_em_forwards_ticker /
# _build_vol_smile_ticker builders — same f"{pair}V{tenor} Curncy" /
# f"{pair}{tenor} Curncy" / f"{pair}{delta}{tenor} Curncy" output shape.


SUBSTRATES: Dict[str, SubstrateConfig] = {
    "g10_forwards_long": SubstrateConfig(
        name="g10_forwards_long",
        playbook_name="fx_forwards",  # extends fx_forwards.yml
        dataset_name="fx_forwards",
        instrument_type="fx_forward",
        sheet_name_pattern=re.compile(r"^(?P<pair>[A-Z]{6})_(?P<tenor>2Y|5Y)$"),
        ticker_builder=_build_g10_forwards_long_ticker,
        expected_sheet_count=12,
        min_rows_per_sheet=5000,  # full 2000 history
        # Codex Option A: combine the 12 new 2Y/5Y rows with the existing
        # 30 G10 1W-12M rows from DB so the parquet covers all 42
        # fx_forwards instruments. Sanity gate sees 42/30 = 140% → passes,
        # preserving the "single playbook fx_forwards" semantics.
        combine_existing_filter=(
            "im.instrument_type = 'fx_forward' "
            "AND im.attributes->>'fx_family' = 'G10_FORWARDS' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),
    "ndf": SubstrateConfig(
        name="ndf",
        playbook_name="fx_ndf",
        dataset_name="fx_ndf",
        instrument_type="fx_ndf",
        sheet_name_pattern=re.compile(
            r"^(?P<ndf_code>CCN|IRN|BCN|KWN|IHN)_(?P<tenor>1W|1M|3M|6M|12M)$"
        ),
        ticker_builder=_build_ndf_ticker,
        expected_sheet_count=25,
        # NDFs have variable history — most start ~2000-2001, but BCN+1W
        # only from 2017-11 and is frozen since 2024-08 (low liquidity at
        # the BRL NDF 1W tenor). Threshold 1000 accepts BCN+1W while still
        # failing-loud on substantially worse data.
        min_rows_per_sheet=1000,
    ),
    "em_forwards": SubstrateConfig(
        name="em_forwards",
        playbook_name="fx_em_forwards",
        dataset_name="fx_em_forwards",
        instrument_type="fx_forward",
        sheet_name_pattern=re.compile(
            r"^(?P<pair>USD[A-Z]{3})_(?P<tenor>1W|1M|3M|6M|12M)$"
        ),
        ticker_builder=_build_em_forwards_ticker,
        expected_sheet_count=30,
        min_rows_per_sheet=4000,  # most EM deliverable forwards back to ~2002-2005
    ),
    "em_vol": SubstrateConfig(
        name="em_vol",
        playbook_name="fx_vol",  # extends fx_vol.yml
        dataset_name="fx_vol",
        instrument_type="fx_vol",
        sheet_name_pattern=re.compile(
            r"^(?P<pair>USD[A-Z]{3})_V(?P<tenor>1W|1M|3M|6M|1Y)$"
        ),
        ticker_builder=_build_em_vol_ticker,
        expected_sheet_count=55,  # 11 currencies × 5 tenors
        min_rows_per_sheet=500,  # CNH vol from ~2012, others variable
    ),
    # Smile is split across 2 XLSX files (G10 and EM) but lands in ONE
    # playbook fx_vol_smile.yml per Codex's call. Run twice: once with
    # --substrate vol_smile_g10 and once with --substrate vol_smile_em.
    # Both use playbook_name="fx_vol_smile" so they share the same audit
    # lineage and refresh_instrument_metadata --playbook fx_vol_smile
    # updates both batches in one pass.
    "vol_smile_g10": SubstrateConfig(
        name="vol_smile_g10",
        playbook_name="fx_vol_smile",
        dataset_name="fx_vol_smile",
        instrument_type="fx_vol_smile",
        sheet_name_pattern=re.compile(
            r"^(?P<pair>[A-Z]{6})_(?P<delta>25R|25B|10R|10B)_(?P<tenor>1M|3M|1Y)$"
        ),
        ticker_builder=_build_vol_smile_ticker,
        expected_sheet_count=72,  # 6 G10 pairs × 4 smile points × 3 tenors
        min_rows_per_sheet=500,
    ),
    "vol_smile_em": SubstrateConfig(
        name="vol_smile_em",
        playbook_name="fx_vol_smile",
        dataset_name="fx_vol_smile",
        instrument_type="fx_vol_smile",
        sheet_name_pattern=re.compile(
            r"^(?P<pair>USD[A-Z]{3})_(?P<delta>25R|25B|10R|10B)_(?P<tenor>1M|3M|1Y)$"
        ),
        ticker_builder=_build_vol_smile_ticker,
        expected_sheet_count=84,  # 7 EM pairs × 4 smile points × 3 tenors
        min_rows_per_sheet=300,  # EM smile sometimes has shorter history
    ),
    # Spot top-up: close a gap discovered after the BBG batch — CNY/INR
    # have NDFs + vol but no spot. USDCNH (offshore tradable),
    # USDCNY (onshore PBOC fix), USDINR (composite spot/reference) are
    # ingested here so Phase D's calculate_ndf_implied_carry can compute
    # NDF-vs-spot carry for CCN+ and IRN+. Extends spot_fx.yml (Codex's
    # "single playbook" preference for same-shape data, same as Phase B
    # EM spot extension). Combines existing 18 spot_fx rows + 3 new
    # = 21 incoming vs 9 prior load (load_id=21) → 233% sanity pass.
    "spot_topup": SubstrateConfig(
        name="spot_topup",
        playbook_name="spot_fx",  # extends spot_fx.yml
        dataset_name="spot_fx",
        instrument_type="fx_spot",
        sheet_name_pattern=re.compile(r"^USD(?P<ccy>CNH|CNY|INR)$"),
        ticker_builder=_build_spot_topup_ticker,
        expected_sheet_count=3,
        min_rows_per_sheet=500,  # USDCNH from ~2010-2011 (offshore launch)
        combine_existing_filter=(
            "im.instrument_type = 'fx_spot' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),
    # ====================================================================
    # Warehouse seeding (2026-05-25) — 8 new substrates
    # --------------------------------------------------------------------
    # Strategic pivot from "minimum viable substrate" → "BBG data warehouse
    # seeding while terminal is available". User plans many downstream
    # tools, wants to avoid future surprise data gaps. Phase 1 discovery
    # session validated which tickers BBG actually serves. Phase 2 extracted
    # 8 XLSX files. This block wires the 8 substrates into the converter.
    #
    # Ordering for ingestion (sanity-gate friendly, attribute-refresh
    # cascade aware):
    #   1. macro_indices       (NEW playbook → no combine needed)
    #   2. em_ext_spot         (extends spot_fx — 21 → 27 rows after merge)
    #   3. em_ext_ndf          (extends fx_ndf — 25 → 30)
    #   4. em_ext_forwards     (extends fx_em_forwards — 30 → 40)
    #   5. em_ext_vol          (extends fx_vol — 85 → 115)
    #   6. atm_extended        (extends fx_vol — 115 → 200, depends on #5
    #                           landing first so combine fetches the freshly
    #                           added 30 EM ATM tickers too)
    #   7. cnhinr_smile        (extends fx_vol_smile — 156 → 180)
    #   8. smile_extended      (extends fx_vol_smile — 180 → 284, same
    #                           combine-after rationale as #6)
    # Refresh metadata for the affected playbook after EACH ingest because
    # the ingester wipes attributes (cross-playbook attribute lesson from
    # PR #203).
    # ====================================================================

    "macro_indices": SubstrateConfig(
        name="macro_indices",
        playbook_name="fx_macro_indices",  # NEW playbook
        dataset_name="fx_macro_indices",
        instrument_type="fx_macro_index",
        sheet_name_pattern=re.compile(r"^(?P<index_code>DXY|BBDXY|JPMVXYG7|JPMVXYEM)$"),
        ticker_builder=_build_macro_index_ticker,
        expected_sheet_count=4,
        # DXY is back to ~1971 in BBG but the BDH start_date is bounded by
        # the converter default 2000-01-01. JPMVXY* histories vary — JPMVXYG7
        # is back to 1992, JPMVXYEM to 2007. Threshold 500 accepts the
        # ~5000-row JPMVXYEM series.
        min_rows_per_sheet=500,
        # No combine — first load for fx_macro_indices, prior_count=0,
        # sanity gate skipped.
    ),

    "em_ext_spot": SubstrateConfig(
        name="em_ext_spot",
        playbook_name="spot_fx",  # extends spot_fx.yml
        dataset_name="spot_fx",
        instrument_type="fx_spot",
        sheet_name_pattern=re.compile(r"^USD(?P<ccy>SGD|TWD|THB|CLP|COP|PEN)$"),
        ticker_builder=_build_em_ext_spot_ticker,
        expected_sheet_count=6,
        # SGD/TWD/THB full 2000+ history. CLP/COP/PEN should also be
        # >4000 rows (verified discovery 2026-05-25). Threshold 4000 is
        # conservative.
        min_rows_per_sheet=4000,
        combine_existing_filter=(
            "im.instrument_type = 'fx_spot' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),

    "em_ext_ndf": SubstrateConfig(
        name="em_ext_ndf",
        playbook_name="fx_ndf",  # extends fx_ndf.yml
        dataset_name="fx_ndf",
        instrument_type="fx_ndf",
        sheet_name_pattern=re.compile(r"^NTN_(?P<tenor>1W|1M|3M|6M|12M)$"),
        ticker_builder=_build_em_ext_ndf_ticker,
        expected_sheet_count=5,
        # TWD NDF (NTN+) histories vary by tenor. 1W is sparser than 12M
        # (the standard NDF carry tenor); discovery showed all 4 tested
        # tenors deliver. Threshold 1000 mirrors `ndf` substrate.
        min_rows_per_sheet=1000,
        combine_existing_filter=(
            "im.instrument_type = 'fx_ndf' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),

    "em_ext_forwards": SubstrateConfig(
        name="em_ext_forwards",
        playbook_name="fx_em_forwards",  # extends fx_em_forwards.yml
        dataset_name="fx_em_forwards",
        instrument_type="fx_forward",
        sheet_name_pattern=re.compile(
            r"^(?P<pair>USD(SGD|THB))_(?P<tenor>1W|1M|3M|6M|12M)$"
        ),
        ticker_builder=_build_em_forwards_ticker,
        expected_sheet_count=10,
        # SGD/THB deliverable forwards have deep history back to ~2002-2003.
        min_rows_per_sheet=4000,
        combine_existing_filter=(
            "im.instrument_type = 'fx_forward' "
            "AND im.attributes->>'fx_family' = 'EM_FORWARDS' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),

    "em_ext_vol": SubstrateConfig(
        name="em_ext_vol",
        playbook_name="fx_vol",  # extends fx_vol.yml
        dataset_name="fx_vol",
        instrument_type="fx_vol",
        sheet_name_pattern=re.compile(
            r"^(?P<pair>USD(SGD|TWD|THB|CLP|COP|PEN))_V(?P<tenor>1W|1M|3M|6M|1Y)$"
        ),
        ticker_builder=_build_em_vol_ticker,
        expected_sheet_count=30,  # 6 currencies × 5 tenors
        min_rows_per_sheet=500,
        combine_existing_filter=(
            "im.instrument_type = 'fx_vol' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),

    "atm_extended": SubstrateConfig(
        name="atm_extended",
        playbook_name="fx_vol",  # extends fx_vol.yml (new tenors VON/V2W/V2M/V9M/V2Y)
        dataset_name="fx_vol",
        instrument_type="fx_vol",
        # Note: the 6-char regex `[A-Z]{6}` matches BOTH G10 (EURUSD,
        # GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF) AND EM (USDMXN, USDBRL,
        # USDZAR, USDTRY, USDPLN, USDHUF, USDKRW, USDIDR, USDPHP, USDCNH,
        # USDINR). 17 pairs × 5 tenors = 85 sheets.
        sheet_name_pattern=re.compile(
            r"^(?P<pair>[A-Z]{6})_V(?P<tenor>ON|2W|2M|9M|2Y)$"
        ),
        ticker_builder=_build_em_vol_ticker,  # same f"{pair}V{tenor} Curncy"
        expected_sheet_count=85,
        # ATM vol histories at extended tenors (ON/2W/2M/9M/2Y) are
        # typically thinner than the standard strip. Threshold 500 accepts
        # the shortest series (likely USDCNH/USDINR extended tenors).
        min_rows_per_sheet=500,
        combine_existing_filter=(
            "im.instrument_type = 'fx_vol' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),

    "cnhinr_smile": SubstrateConfig(
        name="cnhinr_smile",
        playbook_name="fx_vol_smile",  # extends fx_vol_smile.yml
        dataset_name="fx_vol_smile",
        instrument_type="fx_vol_smile",
        # USDCNH + USDINR × 4 deltas × 3 tenors (1M/3M/1Y, no 1W/6M per
        # discovery — CNH/INR smile sparse at the short/medium-edge tenors)
        sheet_name_pattern=re.compile(
            r"^(?P<pair>USD(CNH|INR))_(?P<delta>25R|25B|10R|10B)_(?P<tenor>1M|3M|1Y)$"
        ),
        ticker_builder=_build_vol_smile_ticker,
        expected_sheet_count=24,  # 2 pairs × 4 deltas × 3 tenors
        min_rows_per_sheet=500,
        combine_existing_filter=(
            "im.instrument_type = 'fx_vol_smile' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),

    "smile_extended": SubstrateConfig(
        name="smile_extended",
        playbook_name="fx_vol_smile",  # extends fx_vol_smile.yml (new tenors 1W + 6M)
        dataset_name="fx_vol_smile",
        instrument_type="fx_vol_smile",
        # 13 pairs (6 G10 + 7 EM: MXN/BRL/ZAR/TRY/PLN/HUF/KRW; no IDR/PHP
        # because their 1W/6M smile is sparse per discovery) × 4 deltas
        # × 2 tenors = 104 sheets.
        sheet_name_pattern=re.compile(
            r"^(?P<pair>[A-Z]{6})_(?P<delta>25R|25B|10R|10B)_(?P<tenor>1W|6M)$"
        ),
        ticker_builder=_build_vol_smile_ticker,
        expected_sheet_count=104,
        min_rows_per_sheet=500,
        combine_existing_filter=(
            "im.instrument_type = 'fx_vol_smile' "
            "AND d.field_name = 'PX_LAST'"
        ),
    ),
}


# ============================================================================
# Validation (mirror Phase B EM spot strict)
# ============================================================================


class ValidationError(RuntimeError):
    """Raised when the XLSX or built parquet fails any garde-fou check."""


def _fail(msg: str) -> None:
    raise ValidationError(msg)


def _looks_like_date_str(x: object) -> bool:
    if not isinstance(x, str):
        return False
    try:
        pd.to_datetime(x)
        return True
    except (ValueError, TypeError):
        return False


def read_and_validate_xlsx(
    xlsx_path: Path,
    substrate: SubstrateConfig,
    infer_missing_first_date: bool = False,
) -> pd.DataFrame:
    """Read the XLSX, validate, return a long-format DataFrame with
    columns [trade_date, ticker, field_value]. Fail-loud on any
    discrepancy with the substrate's spec.
    """
    if not xlsx_path.exists():
        _fail(f"Input file not found: {xlsx_path}")

    sheets = pd.read_excel(xlsx_path, sheet_name=None, header=None)
    sheet_names = list(sheets.keys())

    # 1. Sheet count
    if len(sheet_names) != substrate.expected_sheet_count:
        _fail(
            f"Substrate {substrate.name!r}: expected exactly "
            f"{substrate.expected_sheet_count} sheets, got "
            f"{len(sheet_names)}. Sheet list: {sheet_names}"
        )

    # 2. Each sheet name matches the substrate's regex; build ticker map
    sheet_to_ticker: Dict[str, str] = {}
    bad_names: List[str] = []
    for sn in sheet_names:
        m = substrate.sheet_name_pattern.match(sn)
        if not m:
            bad_names.append(sn)
            continue
        sheet_to_ticker[sn] = substrate.ticker_builder(m.groupdict())
    if bad_names:
        _fail(
            f"Substrate {substrate.name!r}: {len(bad_names)} sheet name(s) "
            f"do not match pattern {substrate.sheet_name_pattern.pattern!r}: "
            f"{bad_names[:10]}"
        )

    # 3. No duplicate tickers (would mean the substrate config + sheet
    # names disagree silently)
    tickers_seen: Dict[str, str] = {}
    for sn, t in sheet_to_ticker.items():
        if t in tickers_seen:
            _fail(
                f"Substrate {substrate.name!r}: duplicate ticker {t!r} from "
                f"sheets {tickers_seen[t]!r} and {sn!r}. Sheet names probably "
                "map to the same ticker — check the ticker_builder."
            )
        tickers_seen[t] = sn

    # 4. Per-sheet read + optional first-row inference
    inferred_first_dates: List[tuple[str, pd.Timestamp, float]] = []
    long_rows: List[pd.DataFrame] = []
    for sn in sheet_names:
        raw = sheets[sn]
        if raw.shape[1] < 2:
            _fail(f"Sheet {sn!r}: expected ≥2 columns from BDH, got {raw.shape[1]}.")

        first_a = raw.iloc[0, 0]
        first_b = raw.iloc[0, 1]
        first_a_not_date = (
            pd.isna(first_a)
            or not (isinstance(first_a, (pd.Timestamp, datetime)) or _looks_like_date_str(first_a))
        )
        first_b_numeric = pd.notna(first_b) and pd.notna(pd.to_numeric(first_b, errors="coerce"))
        is_orphan_first_row = first_a_not_date and first_b_numeric

        if not is_orphan_first_row and first_a_not_date:
            # Header row (string labels) — skip it
            raw = raw.iloc[1:].reset_index(drop=True)

        df = raw.iloc[:, :2].copy()
        df.columns = ["trade_date", "field_value"]
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")

        # Handle Excel/BDH array-formula host-cell quirk
        if is_orphan_first_row:
            if not infer_missing_first_date:
                _fail(
                    f"Sheet {sn!r}: row 0 has value ({first_b}) but no "
                    "parseable date. Re-run with --infer-missing-first-date."
                )
            if len(df) < 2 or pd.isna(df.iloc[1]["trade_date"]):
                _fail(
                    f"Sheet {sn!r}: cannot infer missing first date — row 1 "
                    "has no valid date either."
                )
            second_date = pd.Timestamp(df.iloc[1]["trade_date"])
            inferred_date = second_date - pd.tseries.offsets.BDay(1)
            df.iat[0, df.columns.get_loc("trade_date")] = inferred_date
            inferred_first_dates.append((sheet_to_ticker[sn], inferred_date, float(first_b)))

        df = df.dropna(subset=["trade_date", "field_value"]).reset_index(drop=True)
        if df.empty:
            _fail(f"Sheet {sn!r}: zero usable rows after parsing dates/values.")

        df["ticker"] = sheet_to_ticker[sn]
        long_rows.append(df[["trade_date", "ticker", "field_value"]])

    if inferred_first_dates:
        print(
            f"NOTE: inferred {len(inferred_first_dates)} missing first-row date(s):"
        )
        for ticker, dt, val in inferred_first_dates[:10]:
            print(f"  - {ticker}: row 0 date set to {dt.date()} (value={val})")
        if len(inferred_first_dates) > 10:
            print(f"  ... +{len(inferred_first_dates) - 10} more")

    long_df = pd.concat(long_rows, ignore_index=True)
    _validate_long_df(long_df, substrate)
    return long_df


def _validate_long_df(long_df: pd.DataFrame, substrate: SubstrateConfig) -> None:
    """Fail-loud on duplicates and per-ticker row counts."""
    assert set(long_df.columns) == {"trade_date", "ticker", "field_value"}

    dups = long_df.duplicated(subset=["ticker", "trade_date"], keep=False)
    if dups.any():
        sample = long_df[dups].head(10)
        _fail(
            f"Found {dups.sum()} duplicate (ticker, trade_date) rows. "
            f"Sample:\n{sample.to_string(index=False)}"
        )

    short_tickers: List[str] = []
    for ticker, sub in long_df.groupby("ticker"):
        if len(sub) < substrate.min_rows_per_sheet:
            short_tickers.append(f"{ticker}={len(sub)}")
    if short_tickers:
        _fail(
            f"Per-ticker history check failed (substrate {substrate.name!r} "
            f"requires ≥{substrate.min_rows_per_sheet} rows): {short_tickers}"
        )


# ============================================================================
# Combine with existing DB rows (Codex Option A for partial extensions)
# ============================================================================


def fetch_existing_for_substrate(substrate: SubstrateConfig) -> pd.DataFrame:
    """Fetch existing rows from live DB matching the substrate's
    combine_existing_filter. Returns long-format DataFrame
    [trade_date, ticker, field_value]. Connects to localhost:5433
    (Docker tsdb). Empty DataFrame if filter is None.
    """
    if substrate.combine_existing_filter is None:
        return pd.DataFrame(columns=["trade_date", "ticker", "field_value"])

    try:
        from sqlalchemy import create_engine, text
    except ImportError as e:
        _fail(f"sqlalchemy required for combine_existing_filter: {e}")

    engine = create_engine(
        "postgresql://quantuser:myStrongPass@localhost:5433/macrodata"
    )
    sql = text(f"""
        SELECT
            d.trade_date,
            im.vendor_ticker AS ticker,
            d.field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
          ON im.instrument_id = d.instrument_id
        WHERE {substrate.combine_existing_filter}
        ORDER BY im.vendor_ticker, d.trade_date
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
        columns = list(conn.execute(sql).keys())

    if not rows:
        return pd.DataFrame(columns=["trade_date", "ticker", "field_value"])
    df = pd.DataFrame(rows, columns=columns)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    return df.reset_index(drop=True)


# ============================================================================
# Summary
# ============================================================================


def print_validation_summary(long_df: pd.DataFrame, substrate: SubstrateConfig) -> None:
    rows = []
    for ticker, sub in long_df.groupby("ticker"):
        sub = sub.sort_values("trade_date")
        rows.append({
            "ticker": ticker,
            "rows": len(sub),
            "min_date": sub["trade_date"].min().date(),
            "max_date": sub["trade_date"].max().date(),
            "first_value": float(sub["field_value"].iloc[0]),
            "last_value": float(sub["field_value"].iloc[-1]),
        })
    summary = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    print(f"\n=== Substrate '{substrate.name}' extraction summary ===")
    if len(summary) > 30:
        print(summary.head(15).to_string(index=False))
        print(f"  ... [{len(summary) - 30} more rows] ...")
        print(summary.tail(15).to_string(index=False))
    else:
        print(summary.to_string(index=False))
    print(f"\nTotal rows across {len(summary)} tickers: {len(long_df):,}")


# ============================================================================
# Parquet build
# ============================================================================


def build_parquet_dataframe(
    long_df: pd.DataFrame,
    substrate: SubstrateConfig,
) -> pd.DataFrame:
    df = long_df.copy()
    df["trade_date"] = df["trade_date"].dt.strftime("%Y-%m-%d")
    df["field_name"] = "PX_LAST"

    extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df["vendor"] = "BLOOMBERG"
    df["asset_class"] = "fx"
    df["instrument_type"] = substrate.instrument_type
    df["dataset_name"] = substrate.dataset_name
    df["playbook_name"] = substrate.playbook_name
    df["playbook_version"] = f"manual-bbg-batch-{substrate.name}"
    df["extraction_mode"] = "historical"
    df["extracted_at"] = extracted_at
    df["requested_start_date"] = substrate.requested_start_date
    df["requested_end_date"] = substrate.requested_end_date

    column_order = [
        "trade_date", "ticker", "field_name", "field_value",
        "vendor", "asset_class", "instrument_type", "dataset_name",
        "playbook_name", "playbook_version", "extraction_mode",
        "extracted_at", "requested_start_date", "requested_end_date",
    ]
    return df[column_order]


# ============================================================================
# GCS upload
# ============================================================================


GCS_BUCKET = "quantifi-fx-data-sacha"


def upload_to_gcs(parquet_path: Path, substrate: SubstrateConfig) -> str:
    try:
        from google.cloud import storage
    except ImportError as e:
        _fail(
            f"google-cloud-storage not installed in this env: {e}. "
            "Run from an env that has it."
        )
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob_name = f"data/{substrate.dataset_name}/{parquet_path.name}"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(parquet_path))
    return f"gs://{GCS_BUCKET}/{blob_name}"


# ============================================================================
# CLI
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--substrate", required=True, choices=sorted(SUBSTRATES.keys()),
        help="Substrate config to apply.",
    )
    parser.add_argument(
        "--input", required=True, type=Path,
        help="Path to the extraction XLSX (BDH formulas live).",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument(
        "--infer-missing-first-date", action="store_true",
        help="Handle Excel/BDH array-formula host-cell quirk (A1 stores "
             "the formula text not the cached first date).",
    )
    args = parser.parse_args()

    substrate = SUBSTRATES[args.substrate]
    print(f"[{substrate.name}] reading {args.input}")

    try:
        long_df = read_and_validate_xlsx(
            args.input, substrate,
            infer_missing_first_date=args.infer_missing_first_date,
        )
    except ValidationError as e:
        print(f"\nVALIDATION FAILED:\n{e}\n", file=sys.stderr)
        return 1

    # Codex Option A: combine existing DB rows for partial-extension
    # substrates (g10_forwards_long) so the sanity gate sees the full
    # universe (existing + new) and passes.
    if substrate.combine_existing_filter is not None:
        print(f"[{substrate.name}] fetching existing DB rows to combine...")
        try:
            existing_df = fetch_existing_for_substrate(substrate)
        except Exception as e:
            print(f"\nDB FETCH FAILED: {e}\n", file=sys.stderr)
            return 1
        n_new_tickers = long_df["ticker"].nunique()
        n_existing_tickers = existing_df["ticker"].nunique() if not existing_df.empty else 0
        if n_existing_tickers == 0:
            print(f"  No existing rows match filter; combine is a no-op.")
        else:
            # Defensive: if XLSX tickers overlap with existing (shouldn't
            # for g10_forwards_long since 2Y/5Y are net-new), keep XLSX
            # rows (fresher).
            existing_only = existing_df[~existing_df["ticker"].isin(long_df["ticker"])]
            long_df = pd.concat([long_df, existing_only], ignore_index=True)
            long_df = long_df.sort_values(["ticker", "trade_date"]).reset_index(drop=True)
            print(
                f"  Combined: {n_new_tickers} new tickers (from XLSX) + "
                f"{existing_only['ticker'].nunique()} existing tickers (from DB) "
                f"= {long_df['ticker'].nunique()} total."
            )

    print_validation_summary(long_df, substrate)

    if not args.yes:
        try:
            resp = input(
                f"\nProceed with parquet build"
                + (" + GCS upload" if args.upload else "")
                + "? [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 0
        if resp != "y":
            print("Aborted.")
            return 0

    parquet_df = build_parquet_dataframe(long_df, substrate)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parquet_filename = f"{substrate.dataset_name}_timeseries_{ts}.parquet"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = args.output_dir / parquet_filename
    parquet_df.to_parquet(parquet_path, engine="pyarrow", index=False)
    print(f"\nWrote local parquet: {parquet_path} ({len(parquet_df):,} rows)")

    if args.upload:
        try:
            uri = upload_to_gcs(parquet_path, substrate)
        except ValidationError as e:
            print(f"\nUPLOAD FAILED: {e}\n", file=sys.stderr)
            return 2
        print(f"Uploaded to: {uri}")
        print(
            "\nNext step (run from Mac with Docker):\n"
            "  docker compose exec rates-agent-dev micromamba run -n macro-env \\\n"
            "    env GCP_BUCKET_NAME=quantifi-fx-data-sacha \\\n"
            "    python ingestion/ingest_parquet.py"
        )
    else:
        print("\n(Local parquet only. Re-run with --upload to push to GCS.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
