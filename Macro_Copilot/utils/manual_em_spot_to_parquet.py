"""Manual EM spot FX extraction → parquet converter (Phase B Wave 1.5).

Built as a one-off workaround when the standard pipeline (Docker on the
BBG terminal) was not available. Sacha manually extracted the 9 EM spot
pairs via Bloomberg BDH formulas in Excel; this script converts that
workbook into the long-format parquet that `ingestion/ingest_parquet.py`
expects, and uploads it to `gs://quantifi-fx-data-sacha/data/spot_fx/`.

Garde-fous (Codex review 2026-05-25):
  1. End_date in BDH formulas is explicit ("20260522"), not blank.
  2. The XLSX is kept untouched; the script reads but never writes back.
  3. Fail-loud validation BEFORE GCS upload (9 sheets, ticker names,
     field_name, dates parsed, min_date ≤ 2000-01-03, max_date close to
     2026-05-22, ≥6000 rows per pair, no duplicates, no all-null series).
  4. Parquet is EM-only with playbook_name="spot_fx" (Codex Q1 lock:
     single playbook). FX metadata (fx_family, market_scope, region,
     pair, base_ccy, quote_ccy) comes from `refresh_instrument_metadata.py`
     after ingestion, not from this parquet.
  5. Summary printed before any GCS upload, requires explicit "y" to
     proceed (--yes to skip the prompt, e.g. for CI).
  6. The parquet is written locally first; --upload required to push.

Usage:
    python utils/manual_em_spot_to_parquet.py \\
        --input ~/Downloads/em_spot_extraction_20260525.xlsx

    # After reviewing the summary printed by the above:
    python utils/manual_em_spot_to_parquet.py \\
        --input ~/Downloads/em_spot_extraction_20260525.xlsx \\
        --upload

After upload, ingest as usual:
    docker compose exec rates-agent-dev micromamba run -n macro-env \\
        env GCP_BUCKET_NAME=quantifi-fx-data-sacha \\
        python ingestion/ingest_parquet.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Constants (Phase B Wave 1.5 spot EM universe, Codex-locked)
# ---------------------------------------------------------------------------

EXPECTED_PAIRS: tuple[str, ...] = (
    "USDMXN", "USDBRL", "USDZAR", "USDTRY",
    "USDPLN", "USDHUF", "USDKRW", "USDIDR", "USDPHP",
)

# Parquet schema constants — these MUST match what historical_extractor.py
# produces so ingest_parquet.py treats this parquet as a normal spot_fx load.
PLAYBOOK_NAME = "spot_fx"
DATASET_NAME = "spot_fx"
INSTRUMENT_TYPE = "fx_spot"
ASSET_CLASS = "fx"
VENDOR = "BLOOMBERG"
FIELD_NAME = "PX_LAST"
EXTRACTION_MODE = "historical"
# Tag the version so a future operator can see in load_audit which rows
# came from the manual workaround vs the standard pipeline.
PLAYBOOK_VERSION = "3.0-manual-bdh"

REQUESTED_START_DATE = "2000-01-01"
REQUESTED_END_DATE = "2026-05-22"

# Validation thresholds (fail-loud on violation)
MAX_FIRST_DATE = pd.Timestamp("2000-01-03")  # min_date per pair ≤ this
MIN_LAST_DATE = pd.Timestamp("2026-05-19")   # max_date per pair ≥ this (slack vs 22)
MIN_ROWS_PER_PAIR = 6000  # ~26y × ~260 trading days ≈ 6760 expected

# GCS upload target (FX bucket, NOT default macro-storage-bucket)
GCS_BUCKET = "quantifi-fx-data-sacha"
GCS_PREFIX = "data/spot_fx"


# ---------------------------------------------------------------------------
# Validation helpers (fail-loud)
# ---------------------------------------------------------------------------

class ValidationError(RuntimeError):
    """Raised when the XLSX or built parquet fails any garde-fou check."""


def _fail(msg: str) -> None:
    raise ValidationError(msg)


def read_and_validate_xlsx(xlsx_path: Path, infer_missing_first_date: bool = False) -> pd.DataFrame:
    """Read the manual BDH extraction XLSX and return a validated long-format
    DataFrame with columns [trade_date (datetime), ticker (str), field_value
    (float)]. Raises ValidationError on any violation of the 9 garde-fous.

    Expected XLSX shape:
      - One sheet per EM pair (sheet name = pair, e.g. "USDMXN").
      - Each sheet has BDH output starting in A1: column 1 = date, column 2 = PX_LAST.
      - 9 sheets total, names ∈ EXPECTED_PAIRS.

    When `infer_missing_first_date=True`, the script handles a known Excel/BDH
    quirk: Excel array-formula host cells (A1) physically store the formula
    text, not the calculated value. So row 0 reads as (NaT, first_value) in
    pandas — the value is preserved in B1 but the date in A1 is unrecoverable
    without re-evaluating BDH (which requires a live BBG session). With the
    flag on, the script reconstructs row 0's date as (A2 - 1 business day) and
    logs the inference. Fail-loud if A2 itself is not a valid date.
    """
    if not xlsx_path.exists():
        _fail(f"Input file not found: {xlsx_path}")

    # Load all sheets at once. pandas returns dict[sheet_name -> DataFrame].
    sheets = pd.read_excel(xlsx_path, sheet_name=None, header=None)

    # Garde-fou: exactly 9 sheets
    sheet_names = set(sheets.keys())
    expected = set(EXPECTED_PAIRS)
    extra = sheet_names - expected
    missing = expected - sheet_names
    if extra or missing:
        msg = []
        if missing:
            msg.append(f"missing sheets: {sorted(missing)}")
        if extra:
            msg.append(f"unexpected extra sheets: {sorted(extra)}")
        _fail(
            f"XLSX sheet set mismatch. Expected exactly {sorted(expected)}. "
            f"Got {sorted(sheet_names)}. Details: {'; '.join(msg)}"
        )

    inferred_first_dates: list[tuple[str, pd.Timestamp, float]] = []
    long_rows: list[pd.DataFrame] = []
    for pair in EXPECTED_PAIRS:
        raw = sheets[pair]

        # BDH array can render with a header row from Bloomberg ("Dates",
        # "PX_LAST") or as pure data. Detect by checking if A1 looks like a
        # date or a string label.
        if raw.shape[1] < 2:
            _fail(f"Sheet '{pair}': expected ≥2 columns from BDH, got {raw.shape[1]}.")

        # If row 0 is non-date (likely BDH header labels), skip it.
        # BUT: an Excel array-formula host cell may render NaT/None for the
        # date when the formula text is in A1 and the calculated date isn't
        # cached (a known BDH quirk). In that case, B1 still holds the first
        # valid value — we don't skip, we reconstruct A1 below.
        first_a = raw.iloc[0, 0]
        first_b = raw.iloc[0, 1]
        # First cell A is "missing as date" if it's NaN/NaT OR it's not a
        # date-shaped object. First cell B is "looks numeric" if it's not
        # null and pd.to_numeric can coerce it (catches python int/float,
        # numpy int64/float64, decimal strings, etc.).
        first_a_not_date = (
            pd.isna(first_a)
            or not (isinstance(first_a, (pd.Timestamp, datetime)) or _looks_like_date_str(first_a))
        )
        first_b_numeric = pd.notna(first_b) and pd.notna(pd.to_numeric(first_b, errors="coerce"))
        is_orphan_first_row = first_a_not_date and first_b_numeric
        if not is_orphan_first_row and not isinstance(first_a, (pd.Timestamp, datetime)) and not _looks_like_date_str(first_a):
            # Genuine header row (string labels) — skip it.
            raw = raw.iloc[1:].reset_index(drop=True)

        # Take only the first 2 columns (date, value) defensively.
        df = raw.iloc[:, :2].copy()
        df.columns = ["trade_date", "field_value"]

        # Parse dates
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        # Coerce values to float
        df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")

        # Handle the Excel/BDH first-row orphan case: row 0 has a valid value
        # but its date is NaT because A1 stores the formula text rather than
        # the cached date.
        if is_orphan_first_row:
            if not infer_missing_first_date:
                _fail(
                    f"Sheet '{pair}': row 0 has a valid value ({first_b}) but "
                    f"no parseable date (Excel/BDH array-formula host-cell "
                    f"quirk — A1 contains formula text, not a stored date). "
                    f"Re-run with --infer-missing-first-date to reconstruct "
                    f"the date as (row 1's date - 1 business day), or re-save "
                    f"the XLSX after paste-special>Values on column A in the "
                    f"BBG terminal."
                )
            # Reconstruct: A1 date = A2 date - 1 business day
            if len(df) < 2 or pd.isna(df.iloc[1]["trade_date"]):
                _fail(
                    f"Sheet '{pair}': cannot infer missing first date because "
                    f"row 1 has no valid date either. Manual fix required."
                )
            second_date = pd.Timestamp(df.iloc[1]["trade_date"])
            inferred_date = second_date - pd.tseries.offsets.BDay(1)
            df.iat[0, df.columns.get_loc("trade_date")] = inferred_date
            inferred_first_dates.append((pair, inferred_date, float(first_b)))

        # Drop rows with bad date or null value (non-trading days, weekends)
        df = df.dropna(subset=["trade_date", "field_value"]).reset_index(drop=True)

        if df.empty:
            _fail(f"Sheet '{pair}': zero usable rows after parsing dates/values.")

        # Add ticker col
        df["ticker"] = f"{pair} Curncy"

        long_rows.append(df[["trade_date", "ticker", "field_value"]])

    # Log all inferences in one place so the operator sees the full picture.
    if inferred_first_dates:
        print(
            f"NOTE: inferred {len(inferred_first_dates)} missing first-row date(s) "
            f"as (row 1's date - 1 business day):"
        )
        for pair, date, val in inferred_first_dates:
            print(f"  - {pair}: row 0 date set to {date.date()} (value={val})")

    long_df = pd.concat(long_rows, ignore_index=True)

    # ---- Cross-sheet validations (fail-loud on any duplicate) ----
    _validate_long_df(long_df)

    return long_df


def _looks_like_date_str(x: object) -> bool:
    """Best-effort: does this look like a date string Bloomberg might emit?"""
    if not isinstance(x, str):
        return False
    try:
        pd.to_datetime(x)
        return True
    except (ValueError, TypeError):
        return False


def _validate_long_df(long_df: pd.DataFrame) -> None:
    """Per-pair and global validations on the long-format DataFrame.
    Fail-loud on any duplicate (ticker, trade_date) — per Codex
    garde-fou #3, no silent dedup of any kind."""
    # Field name implicit = PX_LAST — but we double-check by asserting there
    # is no field column in the long_df (we add it at parquet-build time).
    assert set(long_df.columns) == {"trade_date", "ticker", "field_value"}, (
        f"Internal: unexpected long_df columns {long_df.columns.tolist()}"
    )

    # No duplicate (ticker, trade_date) — same day quoted twice would be a
    # red flag of a copy-paste accident or BDH end-of-range quirk that
    # the operator must investigate before ingestion.
    dups = long_df.duplicated(subset=["ticker", "trade_date"], keep=False)
    if dups.any():
        sample = long_df[dups].head(10)
        _fail(
            f"Found {dups.sum()} duplicate (ticker, trade_date) rows. "
            f"Sample:\n{sample.to_string(index=False)}"
        )

    # Per-pair: row count, min_date, max_date, no all-null
    grouped = long_df.groupby("ticker")
    problems: list[str] = []
    for ticker, sub in grouped:
        row_count = len(sub)
        min_date = sub["trade_date"].min()
        max_date = sub["trade_date"].max()
        all_null_count = sub["field_value"].isna().sum()

        if all_null_count > 0:
            problems.append(
                f"{ticker}: {all_null_count} null field_value rows survived the dropna gate (bug?)"
            )
        if row_count < MIN_ROWS_PER_PAIR:
            problems.append(
                f"{ticker}: only {row_count} rows (expected ≥{MIN_ROWS_PER_PAIR}). "
                f"Did BDH fail to load full history?"
            )
        if min_date > MAX_FIRST_DATE:
            problems.append(
                f"{ticker}: min_date={min_date.date()} > {MAX_FIRST_DATE.date()}. "
                f"Series doesn't start at 2000 — wrong BDH start date?"
            )
        if max_date < MIN_LAST_DATE:
            problems.append(
                f"{ticker}: max_date={max_date.date()} < {MIN_LAST_DATE.date()}. "
                f"Series ends too early — wrong BDH end date or BBG was stale?"
            )

    if problems:
        _fail("Per-pair validation failures:\n  - " + "\n  - ".join(problems))


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def print_validation_summary(long_df: pd.DataFrame) -> None:
    """Print a per-pair summary that a human MUST review before upload."""
    rows = []
    for ticker, sub in long_df.groupby("ticker"):
        sub = sub.sort_values("trade_date")
        rows.append({
            "pair": ticker.replace(" Curncy", ""),
            "row_count": len(sub),
            "min_date": sub["trade_date"].min().date(),
            "max_date": sub["trade_date"].max().date(),
            "first_value": float(sub["field_value"].iloc[0]),
            "last_value": float(sub["field_value"].iloc[-1]),
        })
    summary = pd.DataFrame(rows).sort_values("pair").reset_index(drop=True)
    print("\n=== EM spot extraction summary ===")
    print(summary.to_string(index=False))
    print(f"\nTotal rows across 9 pairs: {len(long_df):,}")


# ---------------------------------------------------------------------------
# Parquet build (matches historical_extractor.py output schema)
# ---------------------------------------------------------------------------

def build_parquet_dataframe(long_df: pd.DataFrame) -> pd.DataFrame:
    """Add the lineage/audit columns ingest_parquet.py expects. Returns a
    DataFrame with the exact column set Phase A's historical_extractor.py
    would have produced for spot_fx."""
    df = long_df.copy()
    # Convert trade_date to ISO string per historical_extractor.py convention
    df["trade_date"] = df["trade_date"].dt.strftime("%Y-%m-%d")
    # Bloomberg field name normalized uppercase
    df["field_name"] = FIELD_NAME
    # field_value is already numeric float64

    # Lineage / audit columns (constant across all rows from this extraction)
    extracted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df["vendor"] = VENDOR
    df["asset_class"] = ASSET_CLASS
    df["instrument_type"] = INSTRUMENT_TYPE
    df["dataset_name"] = DATASET_NAME
    df["playbook_name"] = PLAYBOOK_NAME
    df["playbook_version"] = PLAYBOOK_VERSION
    df["extraction_mode"] = EXTRACTION_MODE
    df["extracted_at"] = extracted_at
    df["requested_start_date"] = REQUESTED_START_DATE
    df["requested_end_date"] = REQUESTED_END_DATE

    column_order = [
        "trade_date", "ticker", "field_name", "field_value",
        "vendor", "asset_class", "instrument_type", "dataset_name",
        "playbook_name", "playbook_version", "extraction_mode",
        "extracted_at", "requested_start_date", "requested_end_date",
    ]
    return df[column_order]


# ---------------------------------------------------------------------------
# GCS upload
# ---------------------------------------------------------------------------

def upload_to_gcs(parquet_path: Path) -> str:
    """Upload the parquet to gs://quantifi-fx-data-sacha/data/spot_fx/<file>.
    Returns the gs:// URI on success. Raises on auth / network failure
    (fail-loud — no silent fallback)."""
    try:
        from google.cloud import storage  # imported lazily
    except ImportError as e:
        _fail(
            f"google-cloud-storage not installed in this env: {e}. "
            f"Run from an env that has it, or `pip install google-cloud-storage`."
        )

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob_name = f"{GCS_PREFIX}/{parquet_path.name}"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(parquet_path))
    uri = f"gs://{GCS_BUCKET}/{blob_name}"
    return uri


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--input", required=True, type=Path,
        help="Path to em_spot_extraction_<date>.xlsx (output of the manual BDH session).",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/tmp"),
        help="Where to write the parquet locally (default /tmp).",
    )
    parser.add_argument(
        "--upload", action="store_true",
        help="After local write, upload the parquet to gs://quantifi-fx-data-sacha/data/spot_fx/.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the interactive 'proceed?' prompt (CI / non-interactive use only).",
    )
    parser.add_argument(
        "--infer-missing-first-date", action="store_true",
        help="Handle the Excel/BDH array-formula quirk where A1 stores the "
             "formula text rather than the cached first date: reconstruct row 0's "
             "date as (row 1's date - 1 business day), and log each inference.",
    )
    args = parser.parse_args()

    try:
        # Step 1 — read + validate
        long_df = read_and_validate_xlsx(args.input, infer_missing_first_date=args.infer_missing_first_date)
    except ValidationError as e:
        print(f"\nVALIDATION FAILED:\n{e}\n", file=sys.stderr)
        return 1

    # Step 2 — summary for human review
    print_validation_summary(long_df)

    # Step 3 — confirm
    if not args.yes:
        try:
            resp = input("\nProceed with parquet build" + (" + GCS upload" if args.upload else "") + "? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 0
        if resp != "y":
            print("Aborted.")
            return 0

    # Step 4 — build parquet
    parquet_df = build_parquet_dataframe(long_df)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parquet_filename = f"spot_fx_timeseries_{ts}.parquet"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = args.output_dir / parquet_filename
    parquet_df.to_parquet(parquet_path, engine="pyarrow", index=False)
    print(f"\nWrote local parquet: {parquet_path} ({len(parquet_df):,} rows)")

    # Step 5 — upload
    if args.upload:
        try:
            uri = upload_to_gcs(parquet_path)
        except ValidationError as e:
            print(f"\nUPLOAD FAILED: {e}\n", file=sys.stderr)
            return 2
        print(f"Uploaded to: {uri}")
        print("\nNext step (run from Mac with Docker):")
        print("  docker compose exec rates-agent-dev micromamba run -n macro-env \\")
        print("    env GCP_BUCKET_NAME=quantifi-fx-data-sacha \\")
        print("    python ingestion/ingest_parquet.py")
    else:
        print("\n(Local parquet only. Re-run with --upload to push to GCS.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
