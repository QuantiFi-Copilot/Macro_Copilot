"""Refresh ``instrument_master`` rows from a playbook ``universe`` section.

Use case
--------
When a playbook YAML gains new metadata fields (e.g. ``market_scope``,
``fx_family``, ``region``) after the data was last extracted, the rows
already stored in ``macro_data.instrument_master`` keep their stale
``attributes`` JSONB and miss the new typed-column values. The full
ingestion pipeline (``incremental_extractor`` → ``ingest_parquet``) is
the canonical path to re-sync, but it requires re-pulling Bloomberg
history — expensive and unnecessary when prices are already correct.

This utility is the metadata-only sibling of ``ingest_parquet.py``:
it reads the local playbook, merges declared fields into the existing
``instrument_master`` row, and touches nothing else (no Bloomberg, no
``market_data_daily``, no ``load_audit``).

Conventions
-----------
* Same ``--playbook`` flag as ``utils/incremental_extractor.py`` —
  accepts a stem (``fx_forwards``) or filename (``fx_forwards.yml``).
* Same ``*_agent/playbooks/`` discovery as ``utils/push_playbooks.py``.
* Dry-run by default; ``--apply`` writes.
* Surgical update — only the columns declared in the YAML universe
  entry are touched. ``attributes`` is merged (not replaced) so
  provenance stamped at ingestion time (e.g. ``source_file``) survives.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

# --- DYNAMIC PATH RESOLUTION ---
# Assumes this script lives under <project_root>/utils/
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from sqlalchemy import text  # noqa: E402

from database.database import get_db_engine  # noqa: E402


DEFAULT_VENDOR = "BLOOMBERG"

# Typed columns on ``instrument_master`` that may be sourced from a
# playbook universe entry. Anything not in this set is folded into the
# JSONB ``attributes`` payload.
TYPED_COLUMNS = {
    "instrument_type",
    "curve_family",
    "country",
    "currency",
    "tenor",
    "underlying_index",
    "contract_code",
    "cusip",
    "isin",
    "expiry_date",
    "maturity_date",
    "is_rolling_contract",
    "is_active",
}

# Keys that identify the row or duplicate top-level playbook context —
# never end up in either typed columns or attributes.
EXCLUDED_KEYS = {"ticker", "vendor_ticker", "vendor", "asset_class"}


def _discover_playbook(project_root: Path, selector: str) -> Path:
    """Resolve a ``--playbook`` selector to a single file under ``*_agent/playbooks/``."""
    stem = selector.rsplit(".yml", 1)[0].rsplit(".yaml", 1)[0]
    candidates: List[Path] = []
    for playbooks_dir in sorted(project_root.glob("*_agent/playbooks")):
        for ext in ("yml", "yaml"):
            path = playbooks_dir / f"{stem}.{ext}"
            if path.exists():
                candidates.append(path)

    if not candidates:
        raise FileNotFoundError(
            f"Could not find a playbook matching {selector!r} under "
            f"{project_root}/*_agent/playbooks/"
        )
    if len(candidates) > 1:
        locations = ", ".join(str(p.relative_to(project_root)) for p in candidates)
        raise RuntimeError(
            f"Ambiguous playbook selector {selector!r}: {locations}. "
            f"Filenames are globally unique by convention — fix the duplicate."
        )
    return candidates[0]


def _split_typed_and_attributes(
    item: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split a universe entry into (typed-column updates, attributes merge)."""
    typed: Dict[str, Any] = {}
    attrs: Dict[str, Any] = {}
    for key, value in item.items():
        if key in EXCLUDED_KEYS:
            continue
        if value is None:
            continue
        if key in TYPED_COLUMNS:
            typed[key] = value
        else:
            attrs[key] = value
    return typed, attrs


def _diff_typed(current: Mapping[str, Any], desired: Mapping[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    """Return only the typed columns whose value would change."""
    changed: Dict[str, Tuple[Any, Any]] = {}
    for key, new_value in desired.items():
        old_value = current.get(key)
        if old_value != new_value:
            changed[key] = (old_value, new_value)
    return changed


def _diff_attrs(current: Mapping[str, Any], desired: Mapping[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    """Return only the attribute keys whose value would change after a merge."""
    changed: Dict[str, Tuple[Any, Any]] = {}
    current = current or {}
    for key, new_value in desired.items():
        old_value = current.get(key)
        if old_value != new_value:
            changed[key] = (old_value, new_value)
    return changed


def _discover_instrument_master_columns(conn) -> set[str]:
    """Return the set of columns that actually exist on ``instrument_master``.

    The application code (``database/database.py``) tracks the most
    recent schema, but a given DB may lag behind (e.g. ADR-0003 typed
    columns ``cusip`` / ``isin`` / ``expiry_date`` / ``maturity_date``
    may not yet be applied locally). Selecting only existing columns
    keeps this utility robust to schema drift.
    """
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'macro_data' AND table_name = 'instrument_master'
            """
        )
    ).all()
    return {r[0] for r in rows}


def refresh_from_playbook(playbook_path: Path, *, apply: bool, vendor: str) -> int:
    """Plan or execute the refresh. Returns process exit code."""
    with playbook_path.open("r", encoding="utf-8") as handle:
        playbook = yaml.safe_load(handle) or {}

    universe = playbook.get("universe") or []
    if not universe:
        print(f"[ERROR] Playbook {playbook_path.name} has no 'universe' section.")
        return 1

    asset_class = playbook.get("asset_class")
    if not asset_class:
        print(f"[ERROR] Playbook {playbook_path.name} is missing top-level 'asset_class'.")
        return 1

    print(f"Playbook   : {playbook_path.relative_to(project_root)}")
    print(f"asset_class: {asset_class}")
    print(f"vendor     : {vendor}")
    print(f"entries    : {len(universe)}")
    print(f"mode       : {'APPLY' if apply else 'DRY-RUN (no writes)'}")
    print()

    engine = get_db_engine()

    with engine.connect() as introspect_conn:
        existing_cols = _discover_instrument_master_columns(introspect_conn)
    if "attributes" not in existing_cols:
        print("[ERROR] instrument_master.attributes does not exist — wrong DB?")
        return 1

    selectable_typed = sorted(TYPED_COLUMNS & existing_cols)
    select_columns = selectable_typed + ["attributes"]
    skipped_typed = sorted(TYPED_COLUMNS - existing_cols)
    if skipped_typed:
        print(f"[INFO] Skipping typed columns not in DB: {skipped_typed}")
        print()

    select_stmt = text(
        f"SELECT {', '.join(select_columns)} "
        "FROM macro_data.instrument_master "
        "WHERE vendor = :vendor AND vendor_ticker = :ticker"
    )

    update_stmts: List[Tuple[str, Dict[str, Any]]] = []
    n_unchanged = 0
    n_missing = 0
    n_planned = 0

    with engine.connect() as conn:
        for item in universe:
            ticker = item.get("ticker") or item.get("vendor_ticker")
            if not ticker:
                print("[WARN] Universe entry missing 'ticker'; skipping.")
                continue

            row = conn.execute(select_stmt, {"vendor": vendor, "ticker": ticker}).mappings().first()
            if row is None:
                print(f"[WARN] {ticker}: no row in instrument_master — skipping (use the full ingestion path for new instruments).")
                n_missing += 1
                continue

            desired_typed, desired_attrs = _split_typed_and_attributes(item)
            # Restrict to columns the local DB actually has, so a playbook
            # field whose column hasn't been migrated locally lands in
            # attributes (its safe fallback) instead of crashing the UPDATE.
            for col in list(desired_typed.keys()):
                if col not in existing_cols:
                    desired_attrs.setdefault(col, desired_typed.pop(col))
            typed_changes = _diff_typed(row, desired_typed)
            attrs_current = row.get("attributes") or {}
            attrs_changes = _diff_attrs(attrs_current, desired_attrs)

            if not typed_changes and not attrs_changes:
                n_unchanged += 1
                continue

            n_planned += 1
            print(f"[PLAN] {ticker}")
            for col, (old, new) in typed_changes.items():
                print(f"    typed.{col}: {old!r} -> {new!r}")
            for key, (old, new) in attrs_changes.items():
                print(f"    attrs.{key}: {old!r} -> {new!r}")

            set_clauses = [f"{col} = :{col}" for col in typed_changes.keys()]
            params: Dict[str, Any] = {col: new for col, (_, new) in typed_changes.items()}
            if attrs_changes:
                set_clauses.append(
                    "attributes = COALESCE(attributes, '{}'::jsonb) || CAST(:_attrs_merge AS jsonb)"
                )
                params["_attrs_merge"] = json.dumps(desired_attrs)
            params["vendor"] = vendor
            params["ticker"] = ticker

            update_sql = (
                "UPDATE macro_data.instrument_master "
                f"SET {', '.join(set_clauses)}, updated_at = now() "
                "WHERE vendor = :vendor AND vendor_ticker = :ticker"
            )
            update_stmts.append((update_sql, params))

    print()
    print(f"Summary    : {n_planned} to update, {n_unchanged} already in sync, {n_missing} missing.")

    if not apply:
        print()
        print("Dry-run only — re-run with --apply to commit.")
        return 0

    if not update_stmts:
        print("Nothing to apply.")
        return 0

    print()
    print(f"[DB] Applying {len(update_stmts)} update(s) in a single transaction...")
    with engine.begin() as conn:
        for sql, params in update_stmts:
            conn.execute(text(sql), params)
    print("[DB] Done.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh instrument_master typed columns and attributes JSONB "
            "from a playbook's universe section, without re-running "
            "Bloomberg extraction or touching market_data_daily."
        )
    )
    parser.add_argument(
        "--playbook",
        required=True,
        help="Playbook filename or stem (e.g. 'fx_forwards' or 'fx_forwards.yml').",
    )
    parser.add_argument(
        "--vendor",
        default=DEFAULT_VENDOR,
        help=f"Vendor value used to look up rows (default: {DEFAULT_VENDOR}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the updates. Omit for a dry-run.",
    )
    args = parser.parse_args()

    try:
        playbook_path = _discover_playbook(project_root, args.playbook)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        return 1

    return refresh_from_playbook(playbook_path, apply=args.apply, vendor=args.vendor)


if __name__ == "__main__":
    sys.exit(main())
