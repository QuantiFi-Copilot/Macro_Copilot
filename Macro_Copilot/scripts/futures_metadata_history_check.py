"""
futures_metadata_history_check.py
=================================

Operator-side Bloomberg verification for the CANDIDATE ``metadata_history:``
sections drafted into the two futures playbooks (the production playbooks
under ``Macro_Copilot/rates_agent/playbooks/`` are NOT edited until this
verification passes).

DEPLOYMENT LAYOUT — Bloomberg PC
--------------------------------
This script is designed to run on the dedicated Bloomberg terminal machine,
independent of the Macro_Copilot repo. The expected layout on that PC is:

    Macro/
        playbook_verification/
            futures_metadata_history_check.py     <-- THIS FILE
            verification_reports/                 <-- outputs land here
        playbooks/
            bond_futures.yml                      <-- CANDIDATE
            policy_futures.yml                    <-- CANDIDATE

With this layout, the script can be invoked directly with no arguments:

    cd Macro/playbook_verification
    python futures_metadata_history_check.py

The defaults resolve playbooks at ``../playbooks/`` relative to the script
and write outputs to ``./verification_reports/<TS>/`` next to the script.
Both paths can be overridden with ``--playbooks-dir`` and ``--output-dir``.

SELF-CONTAINED — no Macro_Copilot imports
-----------------------------------------
Because the Bloomberg PC does not carry the Macro_Copilot repo, the four
helpers this script depends on are inlined verbatim below. Source of truth
on the Macro_Copilot side:

  * ``_compute_effective_windows_expiry_roll`` and
    ``_extract_underlying_tickers_from_chain`` -
        Macro_Copilot/utils/historical_extractor.py
  * ``_resolve_metadata_history_section`` -
        Macro_Copilot/utils/historical_extractor.py
  * ``validate_no_overlaps`` and ``HISTORY_TYPED_COLUMNS`` -
        Macro_Copilot/ingestion/metadata_history.py

If those canonical helpers change, mirror the change into the INLINED HELPERS
section below (search for ``# === INLINED HELPERS ===``).

What this script does, end-to-end:

  PHASE 1 — Load the CANDIDATE playbooks from ``tmp/tmp_playbooks/``, extract
            the rolling-contract universe (rows with ``is_rolling_contract:
            true``), parse each playbook's CANDIDATE ``metadata_history:``
            section (chain field, roll convention, static fields).

  PHASE 2 — Per generic ticker (e.g., TY1 Comdty, SFR3 Comdty):
            2a. CHAIN ENUMERATION (bds)
                * Primary call:   bds(generic, FUT_CHAIN,
                                       INCLUDE_EXPIRED_CONTRACTS="Y")
                * Comparison:     bds(generic, FUT_CHAIN)   [no override]
                  → confirms the override actually extends the chain
                    (expired contracts included vs. current-only).
                * Pass criteria:  primary returns a non-empty DataFrame AND
                  the project's ``_extract_underlying_tickers_from_chain``
                  successfully extracts >= MIN_CHAIN_CONTRACTS underlying
                  ticker strings.

            2b. FULL-CHAIN DATE ANCHORS (bdp on EVERY contract)
                The window math (`_compute_effective_windows_expiry_roll`)
                requires the chosen ``roll_field`` and ``first_trade_field``
                on every chain member. We therefore pull date anchors for
                EVERY underlying contract (cheap — 4-6 date fields), then:
                * Build contract-record dicts for the window computer.
                * Verify date orderings per contract:
                    FUT_FIRST_TRADE_DT < LAST_TRADEABLE_DT
                    FUT_DLV_DT_FIRST   <= FUT_DLV_DT_LAST
                    LAST_TRADEABLE_DT  <= FUT_DLV_DT_LAST   (bonds)
                    FUT_NOTICE_FIRST   <= LAST_TRADEABLE_DT (bonds)
                * Count how many contracts are missing each date field —
                  drives the playbook's roll_field choice (e.g., if
                  FUT_NOTICE_FIRST is null for 50% of OAT1 chain, the
                  operator falls back to LAST_TRADEABLE_DT for that market).

            2c. SAMPLED-CONTRACT FULL MNEMONIC CHECK
                For SAMPLE_SIZE (default 5) representative contracts per chain
                (oldest, latest, and N-2 evenly-spaced middles), call:
                * batch bdp(contract, [ALL candidate fields])  — what the
                  extractor actually does
                * per-field single bdp(contract, [field])      — independent
                  verification with NO batching
                Then for each candidate static field:
                * Type-aware pass criteria:
                    - String:  non-null, non-empty, not in {"N.A.","NA","NONE"}
                    - Date:    parseable; 1990-01-01 <= value <= 2050-01-01
                    - Numeric: parseable to float; > 0
                * Batch vs single agreement check.
                * Special: TICKER field cross-check vs the bds output for
                  that contract (the contract's own ticker should round-trip).
                * --full flag exists for the paranoid case: sample = entire
                  chain (Bloomberg-bandwidth heavy).

            2d. WINDOW-VALIDATION GATE (THE MOST IMPORTANT CHECK)
                Using the FULL-CHAIN date anchors from 2b:
                * Run _compute_effective_windows_expiry_roll with the
                  CANDIDATE roll_field declared in the playbook
                  (FUT_NOTICE_FIRST for bonds, LAST_TRADEABLE_DT for STIR).
                * For bond futures only: ALSO run with LAST_TRADEABLE_DT
                  as the fallback, so the operator sees both candidates'
                  results side-by-side.
                * Wrap windows as {generic: windows} and pass to
                  ``validate_no_overlaps`` (the EXACT validator the
                  extractor and ingester run as defence-in-depth before
                  the EXCLUDE USING GIST constraint fires).
                * Pass = validate_no_overlaps returns []. Any conflict
                  means this generic CANNOT ship in production until the
                  operator either drops it from the A2 scope or picks a
                  different roll_field.

  PHASE 3 — Aggregate:
            * Per-curve_family rollup (e.g., UST_FUT: 6/6 generics clean).
            * Per-playbook rollup (which mnemonics had 100% / partial / 0%
              pass rates → operator drops the 0% ones in Phase B).
            * Recommended roll_field per playbook based on observed pass
              rates.
            * Final top-level summary the operator pastes into the PR body.

Outputs (written to ``./verification_reports/futures_metadata_history_<TS>/``):
  1. futures_metadata_history_validation_report.txt
     Human-readable narrative — what was tested, what passed, what failed,
     and (at the end) the FINAL SUMMARY block the operator pastes back.

  2. futures_metadata_history_validation_ticker_summary.csv
     One row per generic ticker. Columns include chain counts, sample sizes,
     all-fields-pass, window-validation results for primary + fallback
     roll_field, and a ticker_ready_for_playbook flag.

  3. futures_metadata_history_validation_field_level.csv
     One row per (generic × candidate field × sample contract). Captures
     per-call value, type-check pass, batch-vs-single agreement, and the
     pass criteria reason.

  4. futures_metadata_history_validation_contract_detail.csv
     One row per sampled underlying contract. Carries every candidate
     field's value side-by-side plus the per-contract date-ordering result.

  5. futures_metadata_history_validation_curve_summary.csv
     One row per (playbook, curve_family). Carries the playbook-level
     pass/fail counts and the recommended roll_field.

Usage:
  python futures_metadata_history_check.py                   # sample mode (default)
  python futures_metadata_history_check.py --full            # entire chain (slow)
  python futures_metadata_history_check.py --playbook bond_futures
  python futures_metadata_history_check.py --playbook policy_futures
  python futures_metadata_history_check.py --sample-size 8
  python futures_metadata_history_check.py --playbooks-dir /some/other/dir
  python futures_metadata_history_check.py --output-dir /shared/reports

Required Python packages on the Bloomberg PC: pandas, pyyaml, xbbg, blpapi.
"""

from __future__ import annotations

import argparse
import math
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

try:
    from xbbg import blp  # noqa: E402
except ImportError as exc:  # pragma: no cover — operator-machine dependency
    raise ImportError(
        "xbbg is not installed in this environment. This script must be run "
        "on a Bloomberg-equipped machine with xbbg + blpapi available. "
        f"Underlying: {exc}"
    ) from exc


# ===========================================================================
# === INLINED HELPERS =======================================================
#
# These four helpers are inlined verbatim from the Macro_Copilot repo so that
# this script is fully self-contained on the Bloomberg PC. If the canonical
# implementations change in the repo, mirror the change here.
#
# Canonical sources:
#   * _extract_underlying_tickers_from_chain  -> Macro_Copilot/utils/historical_extractor.py
#   * _compute_effective_windows_expiry_roll  -> Macro_Copilot/utils/historical_extractor.py
#   * _resolve_metadata_history_section       -> Macro_Copilot/utils/historical_extractor.py
#   * HISTORY_TYPED_COLUMNS, validate_no_overlaps
#                                             -> Macro_Copilot/ingestion/metadata_history.py
# ===========================================================================

HISTORY_TYPED_COLUMNS: tuple = (
    "contract_code",
    "expiry_date",
    "maturity_date",
    "security_name",
    "settlement_date",
    "accrual_start_date",
    "accrual_end_date",
    "tick_size",
    "tick_value",
    "contract_size",
    "exchange_code",
    "underlying_ticker",
)


def _extract_underlying_tickers_from_chain(
    chain_df: Any,
    fallback_column: Optional[str] = None,
) -> List[str]:
    """
    Convert ``xbbg.blp.bds(...)`` output for a chain-enumeration field
    (typically ``FUT_CHAIN``) into a flat list of underlying-contract ticker
    strings. Probes canonical column names in order, then falls back to the
    first string-valued column.
    """
    if chain_df is None:
        return []
    if not isinstance(chain_df, pd.DataFrame):
        return []
    if chain_df.empty:
        return []

    candidates = [
        "Security Description",
        "security_description",
        "Security_Description",
        "SECURITY_DES",
        "security_des",
        "value",
    ]
    if fallback_column:
        candidates.insert(0, fallback_column)

    for col in candidates:
        if col in chain_df.columns:
            tickers = [str(v).strip() for v in chain_df[col].dropna().tolist() if str(v).strip()]
            if tickers:
                return tickers

    for col in chain_df.columns:
        if chain_df[col].dtype == object:
            tickers = [str(v).strip() for v in chain_df[col].dropna().tolist() if str(v).strip()]
            if tickers:
                return tickers
    return []


def _compute_effective_windows_expiry_roll(
    contracts: List[Dict[str, Any]],
    roll_field_column: str,
    first_trade_field_column: str,
) -> List[Dict[str, Any]]:
    """
    Apply the ADR 0002 ``expiry_roll`` convention to a chain of underlying
    contracts:

      * C_1 (oldest): effective_from = C_1.first_trade_field,
                      effective_to   = C_1.roll_field
      * C_i (middle): effective_from = C_{i-1}.roll_field + 1 day,
                      effective_to   = C_i.roll_field
      * C_n (latest): effective_from = C_{n-1}.roll_field + 1 day,
                      effective_to   = NULL  (currently in effect)

    Inputs are sorted in-place by ``roll_field_column`` ascending. Contracts
    missing either anchor are dropped. Column names must be UPPERCASE
    (matching ``_normalize_bdp_output``'s output).
    """
    if not contracts:
        return []

    valid: List[Dict[str, Any]] = []
    for c in contracts:
        roll_val = c.get(roll_field_column)
        first_val = c.get(first_trade_field_column)
        if roll_val is None or first_val is None:
            continue
        try:
            roll_dt = pd.to_datetime(roll_val).date()
            first_dt = pd.to_datetime(first_val).date()
        except Exception:
            continue
        valid.append({**c, "_roll_dt": roll_dt, "_first_trade_dt": first_dt})

    if not valid:
        return []

    valid.sort(key=lambda r: r["_roll_dt"])

    windows: List[Dict[str, Any]] = []
    prev_roll: Optional[date] = None
    for i, c in enumerate(valid):
        is_last = i == len(valid) - 1
        if i == 0:
            effective_from = c["_first_trade_dt"]
        else:
            effective_from = prev_roll + timedelta(days=1)
        effective_to = None if is_last else c["_roll_dt"]
        window = {k: v for k, v in c.items() if not k.startswith("_")}
        window["effective_from"] = effective_from.isoformat()
        window["effective_to"] = effective_to.isoformat() if effective_to else None
        windows.append(window)
        prev_roll = c["_roll_dt"]
    return windows


def _resolve_metadata_history_section(
    playbook: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Return the playbook's ``metadata_history`` section if it is present and
    declares ``enabled: true``; otherwise return None. Raises ValueError if
    ``roll_convention.type`` is anything other than ``"expiry_roll"`` (the
    only value supported in PR A1).
    """
    section = playbook.get("metadata_history")
    if not isinstance(section, dict):
        return None
    if not section.get("enabled"):
        return None
    if not isinstance(section.get("static_fields"), list) or not section["static_fields"]:
        return None
    if not section.get("chain_field"):
        return None
    roll = section.get("roll_convention") or {}
    if not isinstance(roll, dict) or not roll.get("roll_field") or not roll.get("first_trade_field"):
        return None
    if roll.get("type", "expiry_roll") != "expiry_roll":
        raise ValueError(
            f"metadata_history.roll_convention.type={roll.get('type')!r} is not "
            "supported. The only value supported in PR A1 is 'expiry_roll'."
        )
    return section


def validate_no_overlaps(
    rows_by_vendor_ticker: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """
    Defence-in-depth overlap detector. For each generic ticker, walks rows
    sorted by ``effective_from`` and reports conflicts where consecutive
    windows overlap or share a boundary day (the EXCLUDE USING GIST
    constraint uses '[]' inclusive bounds).
    """
    conflicts: List[str] = []
    for vendor_ticker, rows in rows_by_vendor_ticker.items():
        if not rows:
            continue

        bad_missing_from = [r for r in rows if r.get("effective_from") in (None, "")]
        if bad_missing_from:
            conflicts.append(
                f"{vendor_ticker}: {len(bad_missing_from)} row(s) missing effective_from"
            )
            continue

        try:
            sorted_rows = sorted(
                rows,
                key=lambda r: pd.to_datetime(r["effective_from"]).date(),
            )
        except Exception as exc:
            conflicts.append(
                f"{vendor_ticker}: failed to parse effective_from on at least one row: {exc}"
            )
            continue

        n = len(sorted_rows)
        for i, curr in enumerate(sorted_rows):
            try:
                curr_from = pd.to_datetime(curr["effective_from"]).date()
            except Exception as exc:
                conflicts.append(
                    f"{vendor_ticker}: row {i} has unparseable effective_from "
                    f"({curr['effective_from']!r}): {exc}"
                )
                continue

            curr_to_raw = curr.get("effective_to")
            curr_to = None
            if curr_to_raw not in (None, ""):
                try:
                    curr_to = pd.to_datetime(curr_to_raw).date()
                except Exception as exc:
                    conflicts.append(
                        f"{vendor_ticker}: row {i} has unparseable effective_to "
                        f"({curr_to_raw!r}): {exc}"
                    )
                    continue

            if curr_to is not None and curr_to < curr_from:
                conflicts.append(
                    f"{vendor_ticker}: row {i} has effective_to ({curr_to}) "
                    f"before effective_from ({curr_from})"
                )
                continue

            is_last = i == n - 1
            if curr_to is None and not is_last:
                later = sorted_rows[i + 1]
                conflicts.append(
                    f"{vendor_ticker}: row {i} has effective_to=NULL but is not "
                    f"the latest window (next row effective_from="
                    f"{later['effective_from']})"
                )
                continue

            if is_last or curr_to is None:
                continue

            nxt = sorted_rows[i + 1]
            try:
                nxt_from = pd.to_datetime(nxt["effective_from"]).date()
            except Exception:
                continue
            if nxt_from <= curr_to:
                conflicts.append(
                    f"{vendor_ticker}: row {i} (effective_to={curr_to}) "
                    f"overlaps row {i + 1} (effective_from={nxt_from})"
                )
    return conflicts


# === END INLINED HELPERS ===================================================


# ---------------------------------------------------------------------------
# Configuration — defaults assume the Bloomberg-PC layout:
#     Macro/playbook_verification/<THIS_FILE>
#     Macro/playbooks/{bond_futures,policy_futures}.yml
# Both can be overridden via CLI (--playbooks-dir, --output-dir).
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PLAYBOOKS_DIR = (SCRIPT_DIR.parent / "playbooks").resolve()
DEFAULT_OUTPUT_DIR = (SCRIPT_DIR / "verification_reports").resolve()
BOND_PLAYBOOK_FILENAME = "bond_futures.yml"
POLICY_PLAYBOOK_FILENAME = "policy_futures.yml"

DEFAULT_SAMPLE_SIZE = 5            # contracts per chain in sampling mode
MIN_CHAIN_CONTRACTS = 4            # below this, chain enumeration FAILs
MIN_DATE = pd.Timestamp("1990-01-01")
MAX_DATE = pd.Timestamp("2050-12-31")
NULL_STRING_SENTINELS = {"", "N.A.", "NA", "NONE", "NULL", "#N/A N/A", "#N/A"}

# Bond-future fallback: if FUT_NOTICE_FIRST is missing or unreliable, the
# extractor can still produce valid windows using LAST_TRADEABLE_DT (looser
# rolling — bond actually rolls before, but the windows will still be
# non-overlapping). The script reports both side-by-side so the operator can
# choose per-playbook in Phase B.
BOND_ROLL_FALLBACK_FIELD = "LAST_TRADEABLE_DT"

# Date fields we always pull for EVERY contract in the chain (drives 2b).
# Includes both candidate roll fields and every date-typed static field so
# the per-contract date-ordering sanity check has full data.
ALWAYS_PULL_DATE_FIELDS = [
    "LAST_TRADEABLE_DT",
    "FUT_FIRST_TRADE_DT",
    "FUT_NOTICE_FIRST",
    "FUT_DLV_DT_FIRST",
    "FUT_DLV_DT_LAST",
]

# Per-field type classification — drives the type-aware pass criteria in 2c.
DATE_FIELDS = {
    "LAST_TRADEABLE_DT",
    "FUT_FIRST_TRADE_DT",
    "FUT_NOTICE_FIRST",
    "FUT_DLV_DT_FIRST",
    "FUT_DLV_DT_LAST",
}
NUMERIC_FIELDS = {
    "FUT_TICK_SIZE",
    "FUT_TICK_VAL",
    "FUT_CONT_SIZE",
}
STRING_FIELDS_NON_TICKER = {
    "SECURITY_DES",
    "FUT_EXCH_NAME_SHRT",
    "FUT_EXCH_NAME_LONG",
    "UNDL_SPOT_TICKER",
    "QUOTE_UNITS",
}
TICKER_FIELDS = {"TICKER"}


# ---------------------------------------------------------------------------
# Logger — mirror of the inflation-linker script's style.
# ---------------------------------------------------------------------------
class Logger:
    def __init__(self, path: Path):
        self.path = path
        self.lines: List[str] = []

    def log(self, msg: str = "") -> None:
        self.lines.append(msg)
        # Also stream to stdout so the operator sees progress.
        print(msg)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Cleaning helpers — re-implemented locally so the script is single-file
# runnable and doesn't fail if the project's helper rename. Behaviour matches
# `utils.historical_extractor._clean_scalar` / `_normalize_bdp_output`.
# ---------------------------------------------------------------------------
def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def normalize_bdp_output(df: Any, fallback_ticker: str) -> Dict[str, Any]:
    if df is None:
        return {}
    if isinstance(df, pd.Series):
        return {str(k).upper(): clean_scalar(v) for k, v in df.items()}
    if isinstance(df, pd.DataFrame):
        if df.empty:
            return {}
        if fallback_ticker in df.index:
            row = df.loc[fallback_ticker]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            return {str(k).upper(): clean_scalar(v) for k, v in row.items()}
        if len(df) == 1:
            row = df.iloc[0]
            return {str(k).upper(): clean_scalar(v) for k, v in row.items()}
    return {}


# ---------------------------------------------------------------------------
# Bloomberg call wrappers — every call returns (value_or_df, error_or_None)
# so exceptions never propagate out of a per-contract loop and abort the
# whole run. Same pattern as the inflation-linker script.
# ---------------------------------------------------------------------------
def safe_bds(
    ticker: str,
    field: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Optional[str]]:
    try:
        kwargs = dict(overrides or {})
        df = blp.bds(tickers=ticker, flds=field, **kwargs)
        return df, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def safe_bdp_single(
    ticker: str,
    field: str,
) -> Tuple[Any, Optional[str]]:
    try:
        df = blp.bdp(tickers=ticker, flds=[field])
        norm = normalize_bdp_output(df, fallback_ticker=ticker)
        return norm.get(field.upper()), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def safe_bdp_batch(
    ticker: str,
    fields: List[str],
) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        df = blp.bdp(tickers=ticker, flds=fields)
        norm = normalize_bdp_output(df, fallback_ticker=ticker)
        return {f.upper(): norm.get(f.upper()) for f in fields}, None
    except Exception as exc:
        return {f.upper(): None for f in fields}, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Per-value pass-criteria — type-aware.
# ---------------------------------------------------------------------------
def is_string_pass(value: Any) -> Tuple[bool, str]:
    if value is None:
        return False, "null value"
    if not isinstance(value, str):
        s = str(value).strip()
    else:
        s = value.strip()
    if s.upper() in NULL_STRING_SENTINELS:
        return False, f"sentinel-null string: {s!r}"
    if not s:
        return False, "empty string"
    return True, "non-null string"


def is_date_pass(value: Any) -> Tuple[bool, str]:
    if value is None:
        return False, "null date"
    try:
        ts = pd.to_datetime(value)
    except Exception as exc:
        return False, f"unparseable: {exc}"
    if pd.isna(ts):
        return False, "NaT"
    if ts < MIN_DATE or ts > MAX_DATE:
        return False, f"date out of plausible range: {ts.date()}"
    return True, f"valid date: {ts.date()}"


def is_numeric_pass(value: Any) -> Tuple[bool, str]:
    if value is None:
        return False, "null numeric"
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        return False, f"unparseable: {exc}"
    if math.isnan(f) or math.isinf(f):
        return False, "NaN/Inf"
    if f <= 0:
        return False, f"non-positive: {f}"
    return True, f"positive numeric: {f}"


def is_ticker_pass(value: Any, expected_ticker: Optional[str]) -> Tuple[bool, str]:
    ok, note = is_string_pass(value)
    if not ok:
        return False, note
    if expected_ticker is not None:
        v_str = str(value).strip()
        if v_str != expected_ticker.strip():
            return False, (
                f"TICKER mismatch: bdp returned {v_str!r} but bds enumerated "
                f"{expected_ticker!r} — round-trip failed"
            )
    return True, "TICKER round-trip OK"


def evaluate_field(
    bloomberg_field: str,
    value: Any,
    expected_ticker: Optional[str] = None,
) -> Tuple[bool, str]:
    f_upper = bloomberg_field.upper()
    if f_upper in TICKER_FIELDS:
        return is_ticker_pass(value, expected_ticker)
    if f_upper in DATE_FIELDS:
        return is_date_pass(value)
    if f_upper in NUMERIC_FIELDS:
        return is_numeric_pass(value)
    if f_upper in STRING_FIELDS_NON_TICKER:
        return is_string_pass(value)
    # Unknown field type — fall back to "non-null" as the loosest gate.
    if value is None:
        return False, "null value (unknown type)"
    return True, f"non-null (unknown type): {value!r}"


# ---------------------------------------------------------------------------
# Per-contract date ordering check.
# ---------------------------------------------------------------------------
def check_date_orderings(
    field_values: Dict[str, Any],
    is_bond: bool,
) -> Tuple[bool, List[str]]:
    """
    Verify the per-contract date fields satisfy the relationships every
    listed future should respect. Returns (pass_bool, list_of_failures).
    """
    failures: List[str] = []

    def _parse(key: str) -> Optional[pd.Timestamp]:
        v = field_values.get(key)
        if v is None:
            return None
        try:
            ts = pd.to_datetime(v)
            return None if pd.isna(ts) else ts
        except Exception:
            return None

    first = _parse("FUT_FIRST_TRADE_DT")
    last = _parse("LAST_TRADEABLE_DT")
    dlv_first = _parse("FUT_DLV_DT_FIRST")
    dlv_last = _parse("FUT_DLV_DT_LAST")
    notice = _parse("FUT_NOTICE_FIRST")

    if first is not None and last is not None and first >= last:
        failures.append(
            f"FUT_FIRST_TRADE_DT ({first.date()}) >= LAST_TRADEABLE_DT "
            f"({last.date()})"
        )
    if dlv_first is not None and dlv_last is not None and dlv_first > dlv_last:
        failures.append(
            f"FUT_DLV_DT_FIRST ({dlv_first.date()}) > FUT_DLV_DT_LAST "
            f"({dlv_last.date()})"
        )
    if is_bond:
        if last is not None and dlv_last is not None and last > dlv_last:
            failures.append(
                f"LAST_TRADEABLE_DT ({last.date()}) > FUT_DLV_DT_LAST "
                f"({dlv_last.date()})  [bond contract violates "
                f"trade-before-delivery]"
            )
        if notice is not None and last is not None and notice > last:
            failures.append(
                f"FUT_NOTICE_FIRST ({notice.date()}) > LAST_TRADEABLE_DT "
                f"({last.date()})  [notice must precede last-tradeable]"
            )
    return (len(failures) == 0), failures


# ---------------------------------------------------------------------------
# Sampling — pick representative contracts for the SAMPLED-CONTRACT check.
# ---------------------------------------------------------------------------
def pick_sample_indices(n: int, sample_size: int) -> List[int]:
    if n <= sample_size:
        return list(range(n))
    if sample_size <= 1:
        return [n - 1]  # only the current front
    # oldest, latest, plus evenly spaced middles
    middles = []
    if sample_size >= 3:
        step = (n - 1) / (sample_size - 1)
        for i in range(1, sample_size - 1):
            middles.append(int(round(i * step)))
    indices = [0] + middles + [n - 1]
    seen = set()
    out: List[int] = []
    for idx in indices:
        if idx not in seen and 0 <= idx < n:
            out.append(idx)
            seen.add(idx)
    return sorted(out)


# ---------------------------------------------------------------------------
# Playbook loading.
# ---------------------------------------------------------------------------
def load_playbook_with_section(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Load a candidate playbook and return ``(playbook, metadata_history_section)``.

    Uses the project's ``_resolve_metadata_history_section`` so that the
    pre-flight validation here matches what the extractor will enforce at
    runtime — if the section is rejected by the validator, the verification
    aborts cleanly with the same error the operator would see at extraction
    time.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"CANDIDATE playbook not found at {path}. Make sure the candidate "
            f"YAML lives in the playbooks directory (default: ../playbooks/ "
            f"relative to this script; override via --playbooks-dir)."
        )
    pb = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = _resolve_metadata_history_section(pb)
    if section is None:
        raise ValueError(
            f"{path.name} has no enabled metadata_history section, or the "
            f"section failed _resolve_metadata_history_section validation. "
            f"Verify chain_field / roll_convention.roll_field / "
            f"roll_convention.first_trade_field / static_fields all present."
        )
    return pb, section


# ---------------------------------------------------------------------------
# Per-generic ticker check — orchestrates 2a / 2b / 2c / 2d for one ticker.
# ---------------------------------------------------------------------------
def check_generic_ticker(
    *,
    playbook_name: str,
    universe_row: Dict[str, Any],
    section: Dict[str, Any],
    sample_size: int,
    use_full_chain_for_sample: bool,
    logger: Logger,
    field_rows: List[Dict[str, Any]],
    contract_rows: List[Dict[str, Any]],
    is_bond: bool,
) -> Dict[str, Any]:
    """
    Run all four sub-checks for a single generic ticker. Mutates
    ``field_rows`` and ``contract_rows`` with detail rows for CSV output.
    Returns a ticker-summary dict.
    """
    generic_ticker = universe_row["ticker"]
    curve_family = universe_row.get("curve_family", "UNKNOWN")
    country = universe_row.get("country", "UNKNOWN")
    currency = universe_row.get("currency", "UNKNOWN")
    contract_code = universe_row.get("contract_code", "UNKNOWN")

    chain_field = section["chain_field"]
    chain_overrides = section.get("chain_overrides") or {}
    chain_fallback_column = section.get("chain_column_name")
    roll_field = section["roll_convention"]["roll_field"].upper()
    first_trade_field = section["roll_convention"]["first_trade_field"].upper()
    static_fields_cfg = section["static_fields"]

    logger.log("-" * 160)
    logger.log(
        f"{playbook_name} | {curve_family} | {generic_ticker} "
        f"(country={country}, currency={currency}, contract_code={contract_code})"
    )
    logger.log("-" * 160)

    # ------------------------------------------------------------------
    # 2a. CHAIN ENUMERATION
    # ------------------------------------------------------------------
    logger.log("\n[2a] CHAIN ENUMERATION (bds)")
    primary_df, primary_err = safe_bds(
        ticker=generic_ticker, field=chain_field, overrides=chain_overrides,
    )
    if primary_err:
        logger.log(f"  PRIMARY bds() FAILED: {primary_err}")
        primary_count = 0
    else:
        primary_count = 0 if primary_df is None else len(primary_df)
        logger.log(
            f"  PRIMARY bds({generic_ticker}, {chain_field}, {chain_overrides}) "
            f"-> {primary_count} chain rows"
        )

    no_override_df, no_override_err = safe_bds(
        ticker=generic_ticker, field=chain_field, overrides=None,
    )
    no_override_count = 0 if (no_override_err or no_override_df is None) else len(no_override_df)
    logger.log(
        f"  NO-OVERRIDE bds({generic_ticker}, {chain_field}) "
        f"-> {no_override_count} chain rows (expect <= primary)"
    )

    chain_extension_pass = (
        primary_count > 0
        and (no_override_count == 0 or primary_count >= no_override_count)
    )
    logger.log(
        f"  chain_extension_pass={chain_extension_pass} "
        f"(override actually expands chain back to expired contracts)"
    )

    underlying_tickers: List[str] = []
    if primary_df is not None and primary_count > 0:
        underlying_tickers = _extract_underlying_tickers_from_chain(
            primary_df, fallback_column=chain_fallback_column,
        )
    logger.log(
        f"  _extract_underlying_tickers_from_chain -> {len(underlying_tickers)} ticker(s)"
    )
    if underlying_tickers:
        logger.log(f"  oldest: {underlying_tickers[0]}  ...  latest: {underlying_tickers[-1]}")

    chain_pass = len(underlying_tickers) >= MIN_CHAIN_CONTRACTS
    logger.log(
        f"  chain_pass={chain_pass} "
        f"(>= {MIN_CHAIN_CONTRACTS} underlying ticker(s) extracted)"
    )

    if not chain_pass:
        # Early exit — without a chain there is nothing else to verify.
        logger.log("  [ABORT] empty/short chain — skipping 2b/2c/2d for this ticker")
        return {
            "playbook": playbook_name,
            "curve_family": curve_family,
            "country": country,
            "currency": currency,
            "generic_ticker": generic_ticker,
            "chain_count_primary": primary_count,
            "chain_count_no_override": no_override_count,
            "chain_extension_pass": chain_extension_pass,
            "chain_pass": chain_pass,
            "underlying_count": len(underlying_tickers),
            "oldest_underlying": underlying_tickers[0] if underlying_tickers else None,
            "latest_underlying": underlying_tickers[-1] if underlying_tickers else None,
            "sampled_contracts_count": 0,
            "all_fields_pass": False,
            "dates_pass": False,
            "window_validation_primary_pass": False,
            "window_validation_fallback_pass": None,
            "window_count_primary": 0,
            "window_count_fallback": 0,
            "primary_window_conflicts": "chain enumeration failed",
            "fallback_window_conflicts": None,
            "missing_roll_field_count": None,
            "missing_first_trade_field_count": None,
            "ticker_ready_for_playbook": False,
        }

    # ------------------------------------------------------------------
    # 2b. FULL-CHAIN DATE ANCHORS (bdp on EVERY contract)
    # ------------------------------------------------------------------
    logger.log("\n[2b] FULL-CHAIN DATE ANCHORS (bdp every contract for date fields)")
    chain_records: List[Dict[str, Any]] = []  # each: {TICKER: ..., LAST_TRADEABLE_DT: ..., ...}
    missing_roll = 0
    missing_first_trade = 0
    per_contract_date_orderings_pass = 0
    per_contract_date_orderings_fail = 0
    for u_idx, u_ticker in enumerate(underlying_tickers):
        values, batch_err = safe_bdp_batch(u_ticker, ALWAYS_PULL_DATE_FIELDS)
        if batch_err:
            logger.log(f"    [WARN] bdp batch failed for {u_ticker}: {batch_err}")
        # Track which date anchors are missing for the window math.
        if values.get(roll_field) is None:
            missing_roll += 1
        if values.get(first_trade_field) is None:
            missing_first_trade += 1
        record = {
            "TICKER": u_ticker,
            **{k: clean_scalar(values.get(k)) for k in ALWAYS_PULL_DATE_FIELDS},
        }
        chain_records.append(record)
        pass_orderings, _failures = check_date_orderings(
            field_values=values, is_bond=is_bond,
        )
        if pass_orderings:
            per_contract_date_orderings_pass += 1
        else:
            per_contract_date_orderings_fail += 1
    logger.log(
        f"  Pulled date anchors for {len(chain_records)} contract(s). "
        f"Missing {roll_field}: {missing_roll} | "
        f"Missing {first_trade_field}: {missing_first_trade}"
    )
    logger.log(
        f"  Per-contract date ordering: pass={per_contract_date_orderings_pass} "
        f"| fail={per_contract_date_orderings_fail}"
    )
    dates_pass = (
        missing_roll < len(chain_records) * 0.10  # tolerate <10% missing
        and missing_first_trade < len(chain_records) * 0.10
        and per_contract_date_orderings_fail == 0
    )
    logger.log(
        f"  dates_pass={dates_pass} (missing-anchor rate < 10% AND no ordering failures)"
    )

    # ------------------------------------------------------------------
    # 2c. SAMPLED-CONTRACT FULL MNEMONIC CHECK
    # ------------------------------------------------------------------
    logger.log("\n[2c] SAMPLED-CONTRACT FULL MNEMONIC CHECK")
    candidate_bloomberg_fields: List[str] = []
    seen_fields: set = set()
    field_to_column: Dict[str, str] = {}
    for entry in static_fields_cfg:
        if not isinstance(entry, dict):
            continue
        col = entry.get("column_name")
        fld = entry.get("bloomberg_field")
        if not (col and fld):
            continue
        f_upper = str(fld).upper()
        field_to_column[f_upper] = str(col)
        if f_upper not in seen_fields:
            candidate_bloomberg_fields.append(f_upper)
            seen_fields.add(f_upper)
    # Always include FUT_NOTICE_FIRST for bonds (for fallback eval) and the
    # FUT_EXCH_NAME_LONG fallback for exchange-name verification.
    for extra in ("FUT_EXCH_NAME_LONG",):
        if extra not in seen_fields:
            candidate_bloomberg_fields.append(extra)
            seen_fields.add(extra)

    if use_full_chain_for_sample:
        sample_indices = list(range(len(underlying_tickers)))
    else:
        sample_indices = pick_sample_indices(len(underlying_tickers), sample_size)
    sampled_pairs = [(i, underlying_tickers[i]) for i in sample_indices]
    logger.log(
        f"  Sample indices: {sample_indices} (of 0..{len(underlying_tickers) - 1})"
    )
    logger.log(f"  Sampled contracts: {[t for _, t in sampled_pairs]}")
    logger.log(f"  Candidate bdp fields tested: {candidate_bloomberg_fields}")

    # Per-field per-contract verification.
    per_field_pass_count: Dict[str, int] = {f: 0 for f in candidate_bloomberg_fields}
    per_field_attempt_count: Dict[str, int] = {f: 0 for f in candidate_bloomberg_fields}
    per_field_batch_single_disagree: Dict[str, int] = {f: 0 for f in candidate_bloomberg_fields}

    for s_idx, u_ticker in sampled_pairs:
        logger.log(
            f"\n  -- Sampled contract {s_idx+1}/{len(underlying_tickers)}: {u_ticker}"
        )
        batch_values, batch_err = safe_bdp_batch(u_ticker, candidate_bloomberg_fields)
        if batch_err:
            logger.log(f"    [WARN] batch bdp failed: {batch_err}")

        # Per-field single + comparison
        per_field_single_values: Dict[str, Any] = {}
        for fld in candidate_bloomberg_fields:
            single_val, single_err = safe_bdp_single(u_ticker, fld)
            per_field_single_values[fld] = single_val
            per_field_attempt_count[fld] += 1

            batch_val = batch_values.get(fld)
            batch_single_agree = (
                _values_equal(batch_val, single_val)
                if batch_err is None
                else None
            )
            if batch_single_agree is False:
                per_field_batch_single_disagree[fld] += 1

            # Use single value as primary if available; fall back to batch.
            evaluated_value = single_val if single_err is None else batch_val
            expected_ticker_for_check = u_ticker if fld == "TICKER" else None
            passed, reason = evaluate_field(
                bloomberg_field=fld,
                value=evaluated_value,
                expected_ticker=expected_ticker_for_check,
            )
            if passed:
                per_field_pass_count[fld] += 1

            field_rows.append({
                "playbook": playbook_name,
                "curve_family": curve_family,
                "generic_ticker": generic_ticker,
                "underlying_ticker": u_ticker,
                "sample_position_index": s_idx,
                "bloomberg_field": fld,
                "candidate_column_name": field_to_column.get(fld, "<not-mapped>"),
                "single_value": clean_scalar(single_val),
                "single_error": single_err,
                "batch_value": clean_scalar(batch_val),
                "batch_error": batch_err,
                "batch_single_agree": batch_single_agree,
                "evaluated_value": clean_scalar(evaluated_value),
                "field_pass": passed,
                "pass_reason": reason,
            })

            logger.log(
                f"      {fld:<22} | column={field_to_column.get(fld, '<none>'):<20} | "
                f"single={repr(single_val)[:60]:<62} | batch_eq={batch_single_agree} | "
                f"pass={passed} ({reason})"
            )

        # Per-sampled-contract aggregate row.
        date_orderings_pass, ordering_failures = check_date_orderings(
            field_values={**batch_values, **per_field_single_values},
            is_bond=is_bond,
        )
        contract_rows.append({
            "playbook": playbook_name,
            "curve_family": curve_family,
            "generic_ticker": generic_ticker,
            "underlying_ticker": u_ticker,
            "sample_position_index": s_idx,
            "ticker_field": per_field_single_values.get("TICKER"),
            "security_des": per_field_single_values.get("SECURITY_DES"),
            "last_tradeable_dt": clean_scalar(per_field_single_values.get("LAST_TRADEABLE_DT")),
            "fut_first_trade_dt": clean_scalar(per_field_single_values.get("FUT_FIRST_TRADE_DT")),
            "fut_notice_first": clean_scalar(per_field_single_values.get("FUT_NOTICE_FIRST")),
            "fut_dlv_dt_first": clean_scalar(per_field_single_values.get("FUT_DLV_DT_FIRST")),
            "fut_dlv_dt_last": clean_scalar(per_field_single_values.get("FUT_DLV_DT_LAST")),
            "fut_tick_size": per_field_single_values.get("FUT_TICK_SIZE"),
            "fut_tick_val": per_field_single_values.get("FUT_TICK_VAL"),
            "fut_cont_size": per_field_single_values.get("FUT_CONT_SIZE"),
            "fut_exch_name_shrt": per_field_single_values.get("FUT_EXCH_NAME_SHRT"),
            "fut_exch_name_long": per_field_single_values.get("FUT_EXCH_NAME_LONG"),
            "undl_spot_ticker": per_field_single_values.get("UNDL_SPOT_TICKER"),
            "date_ordering_pass": date_orderings_pass,
            "date_ordering_failures": " | ".join(ordering_failures) if ordering_failures else "",
        })

    # Per-field rollup logging.
    logger.log("\n  PER-FIELD ROLLUP (across sampled contracts):")
    field_rollup: Dict[str, Dict[str, Any]] = {}
    for fld in candidate_bloomberg_fields:
        attempts = per_field_attempt_count[fld]
        passes = per_field_pass_count[fld]
        rate = (passes / attempts) if attempts else 0.0
        field_rollup[fld] = {
            "attempts": attempts, "passes": passes, "pass_rate": rate,
            "batch_single_disagreements": per_field_batch_single_disagree[fld],
        }
        logger.log(
            f"    {fld:<22} | column={field_to_column.get(fld, '<none>'):<20} | "
            f"pass {passes}/{attempts} ({rate:.0%}) | "
            f"batch/single disagree: {per_field_batch_single_disagree[fld]}"
        )

    # Only the playbook-declared fields count toward all_fields_pass — extras
    # like FUT_EXCH_NAME_LONG are tested for operator insight but don't gate.
    playbook_declared = [
        f.upper() for f in field_to_column.keys()
    ]
    all_fields_pass = all(
        field_rollup[f]["pass_rate"] == 1.0
        for f in playbook_declared
        if f in field_rollup
    )
    logger.log(
        f"  all_fields_pass={all_fields_pass} (every playbook-declared field "
        f"100% across sampled contracts)"
    )

    # ------------------------------------------------------------------
    # 2d. WINDOW-VALIDATION GATE
    # ------------------------------------------------------------------
    logger.log("\n[2d] WINDOW-VALIDATION GATE (the most important check)")

    # Primary roll_field windows
    primary_windows = _compute_effective_windows_expiry_roll(
        contracts=chain_records,
        roll_field_column=roll_field,
        first_trade_field_column=first_trade_field,
    )
    primary_conflicts = validate_no_overlaps({generic_ticker: primary_windows})
    primary_pass = (len(primary_windows) > 0) and (len(primary_conflicts) == 0)
    logger.log(
        f"  PRIMARY roll_field={roll_field} -> {len(primary_windows)} windows, "
        f"{len(primary_conflicts)} conflicts -> PASS={primary_pass}"
    )
    if primary_windows:
        logger.log(
            f"    earliest effective_from = {primary_windows[0]['effective_from']}, "
            f"latest effective_to        = {primary_windows[-1].get('effective_to')}"
        )
    if primary_conflicts:
        for c in primary_conflicts[:5]:
            logger.log(f"    CONFLICT: {c}")
        if len(primary_conflicts) > 5:
            logger.log(f"    ... and {len(primary_conflicts) - 5} more")

    fallback_pass: Optional[bool] = None
    fallback_windows: List[Dict[str, Any]] = []
    fallback_conflicts: List[str] = []
    if is_bond and roll_field != BOND_ROLL_FALLBACK_FIELD:
        fallback_windows = _compute_effective_windows_expiry_roll(
            contracts=chain_records,
            roll_field_column=BOND_ROLL_FALLBACK_FIELD,
            first_trade_field_column=first_trade_field,
        )
        fallback_conflicts = validate_no_overlaps(
            {generic_ticker: fallback_windows}
        )
        fallback_pass = (
            len(fallback_windows) > 0 and len(fallback_conflicts) == 0
        )
        logger.log(
            f"  FALLBACK roll_field={BOND_ROLL_FALLBACK_FIELD} -> "
            f"{len(fallback_windows)} windows, {len(fallback_conflicts)} "
            f"conflicts -> PASS={fallback_pass}"
        )

    ticker_ready = bool(
        chain_pass and dates_pass and all_fields_pass and primary_pass
    )
    logger.log(
        f"\n  TICKER SUMMARY: chain_pass={chain_pass} | dates_pass={dates_pass} | "
        f"all_fields_pass={all_fields_pass} | primary_window_pass={primary_pass} "
        f"| fallback_window_pass={fallback_pass} -> ready_for_playbook={ticker_ready}"
    )

    return {
        "playbook": playbook_name,
        "curve_family": curve_family,
        "country": country,
        "currency": currency,
        "generic_ticker": generic_ticker,
        "chain_count_primary": primary_count,
        "chain_count_no_override": no_override_count,
        "chain_extension_pass": chain_extension_pass,
        "chain_pass": chain_pass,
        "underlying_count": len(underlying_tickers),
        "oldest_underlying": underlying_tickers[0] if underlying_tickers else None,
        "latest_underlying": underlying_tickers[-1] if underlying_tickers else None,
        "sampled_contracts_count": len(sampled_pairs),
        "missing_roll_field_count": missing_roll,
        "missing_first_trade_field_count": missing_first_trade,
        "per_contract_date_ordering_pass_count": per_contract_date_orderings_pass,
        "per_contract_date_ordering_fail_count": per_contract_date_orderings_fail,
        "dates_pass": dates_pass,
        "all_fields_pass": all_fields_pass,
        "field_rollup": field_rollup,
        "window_validation_primary_pass": primary_pass,
        "window_validation_fallback_pass": fallback_pass,
        "window_count_primary": len(primary_windows),
        "window_count_fallback": len(fallback_windows),
        "primary_window_conflicts": " | ".join(primary_conflicts) if primary_conflicts else "",
        "fallback_window_conflicts": (
            " | ".join(fallback_conflicts) if fallback_conflicts else ""
        ) if fallback_pass is not None else None,
        "earliest_effective_from": (
            primary_windows[0]["effective_from"] if primary_windows else None
        ),
        "latest_effective_to": (
            primary_windows[-1].get("effective_to") if primary_windows else None
        ),
        "ticker_ready_for_playbook": ticker_ready,
    }


def _values_equal(a: Any, b: Any) -> bool:
    """Loose equality used to confirm batch vs single bdp results agree."""
    if a is None and b is None:
        return True
    try:
        if pd.isna(a) and pd.isna(b):
            return True
    except (TypeError, ValueError):
        pass
    # Try date comparison first.
    try:
        ta = pd.to_datetime(a)
        tb = pd.to_datetime(b)
        if not (pd.isna(ta) or pd.isna(tb)):
            return ta == tb
    except (TypeError, ValueError):
        pass
    # Try numeric.
    try:
        fa = float(a)
        fb = float(b)
        if math.isnan(fa) or math.isnan(fb):
            return False
        return abs(fa - fb) < 1e-9
    except (TypeError, ValueError):
        pass
    return str(a).strip() == str(b).strip()


# ---------------------------------------------------------------------------
# Per-playbook orchestration.
# ---------------------------------------------------------------------------
def run_playbook(
    *,
    playbook_path: Path,
    is_bond: bool,
    sample_size: int,
    use_full_chain_for_sample: bool,
    logger: Logger,
    field_rows: List[Dict[str, Any]],
    contract_rows: List[Dict[str, Any]],
    ticker_rows: List[Dict[str, Any]],
    curve_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    pb, section = load_playbook_with_section(playbook_path)
    playbook_name = pb.get("playbook_name", playbook_path.stem)
    universe = [
        item for item in (pb.get("universe") or [])
        if isinstance(item, dict)
        and item.get("ticker")
        and item.get("is_rolling_contract") is True
    ]

    logger.log("#" * 160)
    logger.log(f"{playbook_name} | playbook_version={pb.get('playbook_version')} | "
               f"rolling tickers={len(universe)}")
    logger.log("#" * 160)
    logger.log(f"chain_field           : {section['chain_field']}")
    logger.log(f"chain_overrides       : {section.get('chain_overrides')}")
    logger.log(f"roll_convention.type  : {section['roll_convention']['type']}")
    logger.log(f"roll_convention.roll_field        : {section['roll_convention']['roll_field']}")
    logger.log(f"roll_convention.first_trade_field : {section['roll_convention']['first_trade_field']}")
    logger.log(
        f"static_fields ({len(section['static_fields'])}): "
        + ", ".join(
            f"{e['column_name']}<-{e['bloomberg_field']}"
            for e in section['static_fields']
            if isinstance(e, dict)
        )
    )

    # Per-curve aggregation buckets
    by_curve: Dict[str, List[Dict[str, Any]]] = {}
    for row in universe:
        by_curve.setdefault(row.get("curve_family", "UNKNOWN"), []).append(row)

    for cf, rows in by_curve.items():
        logger.log("\n" + "=" * 160)
        logger.log(f"CURVE FAMILY: {cf}  ({len(rows)} generic ticker(s))")
        logger.log("=" * 160)

        per_curve_ticker_summaries: List[Dict[str, Any]] = []
        for u_row in rows:
            summary = check_generic_ticker(
                playbook_name=playbook_name,
                universe_row=u_row,
                section=section,
                sample_size=sample_size,
                use_full_chain_for_sample=use_full_chain_for_sample,
                logger=logger,
                field_rows=field_rows,
                contract_rows=contract_rows,
                is_bond=is_bond,
            )
            ticker_rows.append(summary)
            per_curve_ticker_summaries.append(summary)

        # Per-curve rollup
        ready_tickers = [s for s in per_curve_ticker_summaries if s["ticker_ready_for_playbook"]]
        not_ready_tickers = [s for s in per_curve_ticker_summaries if not s["ticker_ready_for_playbook"]]
        primary_clean = [s for s in per_curve_ticker_summaries if s["window_validation_primary_pass"]]
        fallback_clean = [
            s for s in per_curve_ticker_summaries
            if s.get("window_validation_fallback_pass") is True
        ]
        recommended_roll_field = (
            section["roll_convention"]["roll_field"]
            if len(primary_clean) >= len(fallback_clean)
            else BOND_ROLL_FALLBACK_FIELD
        )

        curve_rows.append({
            "playbook": playbook_name,
            "curve_family": cf,
            "country": rows[0].get("country") if rows else None,
            "currency": rows[0].get("currency") if rows else None,
            "total_generics": len(per_curve_ticker_summaries),
            "generics_with_valid_chain": sum(
                1 for s in per_curve_ticker_summaries if s["chain_pass"]
            ),
            "generics_with_clean_dates": sum(
                1 for s in per_curve_ticker_summaries if s["dates_pass"]
            ),
            "generics_with_all_fields_pass": sum(
                1 for s in per_curve_ticker_summaries if s["all_fields_pass"]
            ),
            "generics_with_clean_windows_primary": len(primary_clean),
            "generics_with_clean_windows_fallback": (
                len(fallback_clean)
                if any(s.get("window_validation_fallback_pass") is not None
                       for s in per_curve_ticker_summaries)
                else None
            ),
            "ready_tickers_count": len(ready_tickers),
            "not_ready_tickers_count": len(not_ready_tickers),
            "ready_tickers": " | ".join(s["generic_ticker"] for s in ready_tickers),
            "not_ready_tickers": " | ".join(s["generic_ticker"] for s in not_ready_tickers),
            "recommended_roll_field": recommended_roll_field,
        })

        logger.log("\nCURVE FAMILY ROLLUP")
        logger.log(f"  ready_tickers     : {len(ready_tickers)}/{len(per_curve_ticker_summaries)}")
        logger.log(f"  not_ready_tickers : {len(not_ready_tickers)}/{len(per_curve_ticker_summaries)}")
        logger.log(f"  recommended_roll_field: {recommended_roll_field}")

    # Per-playbook rollup
    playbook_ticker_summaries = [s for s in ticker_rows if s["playbook"] == playbook_name]

    # Aggregate mnemonic pass rates across the whole playbook.
    field_attempts: Dict[str, int] = {}
    field_passes: Dict[str, int] = {}
    for s in playbook_ticker_summaries:
        for f, stats in (s.get("field_rollup") or {}).items():
            field_attempts[f] = field_attempts.get(f, 0) + stats["attempts"]
            field_passes[f] = field_passes.get(f, 0) + stats["passes"]
    full_pass_fields = [
        f for f, a in field_attempts.items()
        if a > 0 and field_passes.get(f, 0) == a
    ]
    partial_pass_fields = [
        f for f, a in field_attempts.items()
        if 0 < field_passes.get(f, 0) < a
    ]
    zero_pass_fields = [
        f for f, a in field_attempts.items()
        if a > 0 and field_passes.get(f, 0) == 0
    ]

    logger.log("\n" + "=" * 160)
    logger.log(f"PLAYBOOK ROLLUP — {playbook_name}")
    logger.log("=" * 160)
    logger.log(
        f"  tickers ready for playbook : "
        f"{sum(1 for s in playbook_ticker_summaries if s['ticker_ready_for_playbook'])}/"
        f"{len(playbook_ticker_summaries)}"
    )
    logger.log(f"  mnemonics 100% pass        : {full_pass_fields}")
    logger.log(f"  mnemonics partial pass     : {[(f, f'{field_passes[f]}/{field_attempts[f]}') for f in partial_pass_fields]}")
    logger.log(f"  mnemonics 0% pass (REJECT) : {zero_pass_fields}")

    return {
        "playbook": playbook_name,
        "rolling_universe_size": len(universe),
        "tickers_summaries": playbook_ticker_summaries,
        "field_attempts": field_attempts,
        "field_passes": field_passes,
        "full_pass_fields": full_pass_fields,
        "partial_pass_fields": partial_pass_fields,
        "zero_pass_fields": zero_pass_fields,
    }


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bloomberg-side verification for the candidate metadata_history "
            "sections drafted into tmp/tmp_playbooks/."
        )
    )
    parser.add_argument(
        "--playbook",
        choices=("bond_futures", "policy_futures", "both"),
        default="both",
        help="Which candidate playbook to verify. Default: both.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=(
            f"Number of underlying contracts to fully verify per generic "
            f"(default: {DEFAULT_SAMPLE_SIZE}). Ignored when --full is set."
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "Run the FULL mnemonic check on EVERY underlying contract (not "
            "just a sample). Bloomberg-bandwidth heavy; use only if sampling "
            "raised concerns."
        ),
    )
    parser.add_argument(
        "--playbooks-dir",
        type=Path,
        default=DEFAULT_PLAYBOOKS_DIR,
        help=(
            f"Directory containing the CANDIDATE playbook YAMLs "
            f"({BOND_PLAYBOOK_FILENAME}, {POLICY_PLAYBOOK_FILENAME}). "
            f"Default: {DEFAULT_PLAYBOOKS_DIR}"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            f"Where to write the report + CSVs. Default: {DEFAULT_OUTPUT_DIR} "
            f"(a per-run sub-directory named "
            f"`futures_metadata_history_<TIMESTAMP>` is created inside)."
        ),
    )
    args = parser.parse_args()

    playbooks_dir = args.playbooks_dir.resolve()
    bond_playbook_path = playbooks_dir / BOND_PLAYBOOK_FILENAME
    policy_playbook_path = playbooks_dir / POLICY_PLAYBOOK_FILENAME

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.output_dir / f"futures_metadata_history_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    txt_path = run_dir / "futures_metadata_history_validation_report.txt"
    ticker_csv_path = run_dir / "futures_metadata_history_validation_ticker_summary.csv"
    field_csv_path = run_dir / "futures_metadata_history_validation_field_level.csv"
    contract_csv_path = run_dir / "futures_metadata_history_validation_contract_detail.csv"
    curve_csv_path = run_dir / "futures_metadata_history_validation_curve_summary.csv"

    logger = Logger(txt_path)
    field_rows: List[Dict[str, Any]] = []
    contract_rows: List[Dict[str, Any]] = []
    ticker_rows: List[Dict[str, Any]] = []
    curve_rows: List[Dict[str, Any]] = []
    playbook_summaries: List[Dict[str, Any]] = []

    logger.log("=" * 160)
    logger.log("FUTURES METADATA-HISTORY BLOOMBERG MNEMONIC + WINDOW VERIFICATION")
    logger.log("=" * 160)
    logger.log(f"Timestamp           : {timestamp}")
    logger.log(f"Mode                : {'FULL chain per generic' if args.full else f'SAMPLE ({args.sample_size} contracts per chain)'}")
    logger.log(f"Script directory    : {SCRIPT_DIR}")
    logger.log(f"Playbooks directory : {playbooks_dir}")
    logger.log(f"Output directory    : {run_dir}")
    logger.log(f"Typed cols on history table : {HISTORY_TYPED_COLUMNS}")
    logger.log()

    targets: List[Tuple[Path, bool]] = []
    if args.playbook in ("bond_futures", "both"):
        targets.append((bond_playbook_path, True))
    if args.playbook in ("policy_futures", "both"):
        targets.append((policy_playbook_path, False))

    for pb_path, is_bond in targets:
        try:
            summary = run_playbook(
                playbook_path=pb_path,
                is_bond=is_bond,
                sample_size=args.sample_size,
                use_full_chain_for_sample=args.full,
                logger=logger,
                field_rows=field_rows,
                contract_rows=contract_rows,
                ticker_rows=ticker_rows,
                curve_rows=curve_rows,
            )
            playbook_summaries.append(summary)
        except Exception as exc:
            logger.log(f"\n[FATAL] Failed to process {pb_path.name}: {exc}")
            logger.log(traceback.format_exc())

    # ------------------------------------------------------------------
    # FINAL SUMMARY — pasteable into the PR body.
    # ------------------------------------------------------------------
    logger.log("\n" + "=" * 160)
    logger.log("FINAL SUMMARY  (paste this into the PR body)")
    logger.log("=" * 160)
    total_generics_tested = len(ticker_rows)
    total_generics_chain_pass = sum(1 for s in ticker_rows if s["chain_pass"])
    total_generics_clean_windows = sum(
        1 for s in ticker_rows if s["window_validation_primary_pass"]
    )
    total_generics_ready = sum(
        1 for s in ticker_rows if s["ticker_ready_for_playbook"]
    )
    logger.log(f"Generic tickers tested              : {total_generics_tested}")
    logger.log(f"Generic tickers with valid chain    : {total_generics_chain_pass} / {total_generics_tested}")
    logger.log(f"Generic tickers with clean windows  : {total_generics_clean_windows} / {total_generics_tested}")
    logger.log(f"Generic tickers ready for playbook  : {total_generics_ready} / {total_generics_tested}")
    logger.log()

    for s in playbook_summaries:
        logger.log(f"  Playbook: {s['playbook']}")
        logger.log(f"    universe size            : {s['rolling_universe_size']}")
        logger.log(f"    mnemonics 100% pass      : {s['full_pass_fields']}")
        partial_pass_details = [
            (f, f"{s['field_passes'][f]}/{s['field_attempts'][f]}")
            for f in s["partial_pass_fields"]
        ]
        logger.log(f"    mnemonics partial pass   : {partial_pass_details}")
        logger.log(f"    mnemonics 0% pass        : {s['zero_pass_fields']}")
        logger.log()

    not_ready = [s for s in ticker_rows if not s["ticker_ready_for_playbook"]]
    if not_ready:
        logger.log("Tickers NOT ready (must be dropped from PR A2 scope OR have the")
        logger.log("underlying mnemonic/window issue fixed before they can ship):")
        for s in not_ready:
            issues = []
            if not s["chain_pass"]:
                issues.append("chain")
            if not s["dates_pass"]:
                issues.append("dates")
            if not s["all_fields_pass"]:
                issues.append("fields")
            if not s["window_validation_primary_pass"]:
                issues.append("windows")
            logger.log(
                f"  - {s['playbook']}/{s['curve_family']}/{s['generic_ticker']}: "
                f"FAILED checks: {','.join(issues)}"
            )
    else:
        logger.log("All tickers READY for playbook — every check passed.")

    logger.log()
    logger.log("Recommended roll_field by playbook (highest clean-window count):")
    for cr in curve_rows:
        logger.log(
            f"  {cr['playbook']:<16} | {cr['curve_family']:<22} | "
            f"recommended_roll_field={cr['recommended_roll_field']}"
        )

    # ------------------------------------------------------------------
    # CSV outputs
    # ------------------------------------------------------------------
    pd.DataFrame(field_rows).to_csv(field_csv_path, index=False)
    pd.DataFrame(contract_rows).to_csv(contract_csv_path, index=False)
    # Strip the (unhashable) nested field_rollup dict before writing the
    # ticker CSV so pandas-to_csv handles every column natively.
    ticker_csv_rows = [
        {k: v for k, v in row.items() if k != "field_rollup"}
        for row in ticker_rows
    ]
    pd.DataFrame(ticker_csv_rows).to_csv(ticker_csv_path, index=False)
    pd.DataFrame(curve_rows).to_csv(curve_csv_path, index=False)

    logger.log()
    logger.log(f"Text report     : {txt_path}")
    logger.log(f"Ticker CSV      : {ticker_csv_path}")
    logger.log(f"Field CSV       : {field_csv_path}")
    logger.log(f"Contract CSV    : {contract_csv_path}")
    logger.log(f"Curve CSV       : {curve_csv_path}")
    logger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Always leave a record on disk, even on fatal error.
        crash_dir = DEFAULT_OUTPUT_DIR
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_path = crash_dir / (
            f"futures_metadata_history_FATAL_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        crash_path.write_text(
            "Fatal error while running futures metadata-history verification.\n\n"
            + traceback.format_exc(),
            encoding="utf-8",
        )
        raise
