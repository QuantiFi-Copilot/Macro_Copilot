#!/usr/bin/env python3
# ============================================================================
# database/populate_tool_metadata.py
# ----------------------------------------------------------------------------
# Phase 0 of the `revamp` branch — generate idempotent UPSERT SQL for the
# macro_data.tool_metadata table (see ADR
# docs_revamped/05_decisions/0015-tool-metadata-db-table.md +
# docs_revamped/03_standards/tool_lifecycle.md §3).
#
# WHAT THIS DOES
# --------------
# Walks the existing tool registries (Python ``_PRIMITIVE_SPECS`` +
# YAML manifestos) and produces ``INSERT … ON CONFLICT (tool_name) DO
# UPDATE`` statements covering every backend-shipped primitive.  The
# generated SQL populates ONLY the mechanically-derivable fields:
#
#   * tool_name           — primary key (matches _PRIMITIVE_SPECS keys
#                           + manifesto `implementation.tool_function`)
#   * domain              — from manifesto `sub_agent`
#   * category            — from manifesto `category`
#   * output_field_units  — from PrimitiveSpec.output_field_units
#
# The human-curated fields stay NULL until Phase 1 per-tool work fills
# them in:
#
#   * theoretical_reference
#   * known_limitations
#   * desk_narrative
#   * source_material_verified
#
# WHY GENERATE SQL INSTEAD OF EXECUTING DIRECTLY
# ----------------------------------------------
# Matches the existing migration pattern at
# database/migrations/2026-05-20_a3_b1_schema_sync.sql — generated SQL
# is checked in, reviewable, idempotent, and applied via the same
# ``docker exec -i macro-tsdb psql …`` command as any other migration.
# No DB credentials are required by the script itself; the script is a
# pure code-generator.
#
# USAGE
# -----
#
#   # Regenerate the seed file.
#   python database/populate_tool_metadata.py \
#       --output database/migrations/2026-05-26_phase0_tool_metadata_seed.sql
#
#   # (Or pipe straight to stdout for review.)
#   python database/populate_tool_metadata.py | less
#
#   # Apply the generated seed file to the live DB.
#   docker exec -i macro-tsdb psql -U quantuser -d macrodata \
#       < database/migrations/2026-05-26_phase0_tool_metadata_seed.sql
#
# RE-RUN SAFETY
# -------------
# ``ON CONFLICT (tool_name) DO UPDATE`` updates the four mechanical
# columns on every re-run, leaving human-curated NULL / non-NULL
# values intact.  Re-running this script after Phase 1 has filled in
# theoretical_reference / known_limitations / desk_narrative for some
# tools is safe — those columns are NOT in the UPDATE clause.
# ============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

# ----------------------------------------------------------------------------
# Project-root bootstrap so we can import rates_agent.workflows directly.
# ----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Imports after sys.path manipulation.
# pylint: disable=wrong-import-position
from rates_agent.workflows import (  # noqa: E402
    _PRIMITIVE_SPECS,
    WORKFLOW_INCOMPATIBLE_TOOLS,
)


MANIFEST_DIR = PROJECT_ROOT / "manifesto" / "03_tool_manifest" / "rates_agent"


# ----------------------------------------------------------------------------
# Tool-name alias table — mirrors the frontend's normalizeToolName aliases.
# ----------------------------------------------------------------------------
#
# The manifesto YAML records ``tool_function`` using HISTORICAL shorthand
# (un-prefixed for analytical models, occasionally verb-mismatched), while
# the backend ``_PRIMITIVE_SPECS`` keys + the rest of the system speak the
# canonical name.  Frontend resolves the gap via
# ``src/lib/toolNames.ts:KNOWN_TOOL_ALIASES``.  This Python mirror keeps
# the population script aligned WITHOUT requiring a runtime cross-language
# import — same closed set, same direction (shorthand → canonical).  If the
# frontend alias table evolves, this dict must evolve in lockstep.

_TOOL_NAME_ALIASES: Dict[str, str] = {
    # Analytical-model shorthand (manifesto emits the un-prefixed form;
    # _PRIMITIVE_SPECS + the rest of the system use ``calculate_<x>_tool``).
    "rolling_regression_tool": "calculate_rolling_regression_tool",
    "beta_adjusted_spread_tool": "calculate_beta_adjusted_spread_tool",
    "half_life_tool": "calculate_half_life_tool",
    "pca_yield_curve_tool": "calculate_pca_yield_curve_tool",
    "yield_change_attribution_pca_tool": (
        "calculate_yield_change_attribution_pca_tool"
    ),
    "zscore_custom_tool": "calculate_zscore_custom_tool",
    # Verb mismatch — manifesto emits the ``calculate_`` form; backend
    # registry uses ``get_`` because the OIS rate-level tool is a
    # snapshot-only primitive (no compute).
    "calculate_ois_rate_level_tool": "get_ois_rate_level_tool",
}


def _normalize_tool_name(name: str) -> str:
    """Normalise a manifesto-shorthand tool_function to canonical form."""
    return _TOOL_NAME_ALIASES.get(name, name)


# ----------------------------------------------------------------------------
# Manifesto YAML walker.
# ----------------------------------------------------------------------------


def load_manifesto_index() -> Dict[str, Dict[str, Optional[str]]]:
    """Walk every manifesto YAML and return ``{tool_function: {domain, category}}``.

    The manifesto's ``name`` field uses the un-suffixed shorthand
    (``calculate_curve_spread``) while everything else in the system
    speaks the suffixed canonical form (``calculate_curve_spread_tool``).
    We key the index by the suffixed form via the
    ``implementation.tool_function`` field, which matches
    ``_PRIMITIVE_SPECS`` keys and ``MODULE.toolName`` in the frontend.
    """
    index: Dict[str, Dict[str, Optional[str]]] = {}
    for path in sorted(MANIFEST_DIR.glob("*.yml")):
        with open(path, "r", encoding="utf-8") as handle:
            doc = yaml.safe_load(handle) or {}
        tools = doc.get("tools", []) if isinstance(doc, dict) else []
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            tool_function = (
                (entry.get("implementation") or {}).get("tool_function")
            )
            if not tool_function:
                continue
            # Apply the same alias normalisation the frontend uses so
            # the index is keyed by backend-canonical name.  Without
            # this, the rich-model primitives (PCA, regression, etc.)
            # + the OIS rate-level tool fall through to ``domain =
            # unknown`` because the manifesto records the shorthand.
            canonical = _normalize_tool_name(tool_function)
            index[canonical] = {
                "domain": entry.get("sub_agent"),
                "category": entry.get("category"),
            }
    return index


# ----------------------------------------------------------------------------
# Row assembly.
# ----------------------------------------------------------------------------


def assemble_rows() -> list[Tuple[str, str, Optional[str], Dict[str, Any]]]:
    """Return a sorted list of ``(tool_name, domain, category, units)`` tuples.

    Walks the union of three backend registries to assemble the full
    set of tools the system knows about:

      1. ``_PRIMITIVE_SPECS`` (52 today) — tools with an executable
         PrimitiveSpec, output_field_units pulled directly.
      2. ``WORKFLOW_INCOMPATIBLE_TOOLS`` (3 today) — tools that ship
         on the backend but whose output shape can't lift to a Series
         or Panel artifact.  Units default to empty.
      3. The manifesto union ("manifest_typed_view" tools — typically 3
         today: ``calculate_butterfly_tool``, ``scan_extremes_tool``, +
         the ``classify_curve_move`` overlap with workflow_incompatible).
         These have typed-detail endpoints but no PrimitiveSpec.  Units
         default to empty — filled in per-tool Phase 1+ work.

    Domains + categories come from the manifesto via the alias-
    normalised lookup; "unknown" is the defensive fallback when a tool
    appears in PRIMITIVE_SPECS or WORKFLOW_INCOMPATIBLE_TOOLS but the
    manifesto omits it (shouldn't happen on a healthy repo; the parity
    check catches this).
    """
    manifest = load_manifesto_index()
    seen: set[str] = set()
    rows: list[Tuple[str, str, Optional[str], Dict[str, Any]]] = []

    def add(tool_name: str, units: Dict[str, Any]) -> None:
        if tool_name in seen:
            return
        seen.add(tool_name)
        meta = manifest.get(tool_name, {})
        domain = meta.get("domain") or "unknown"
        category = meta.get("category")
        rows.append((tool_name, domain, category, units))

    # 1. PrimitiveSpec entries — full units from the spec.
    for tool_name, spec in _PRIMITIVE_SPECS.items():
        add(tool_name, dict(spec.output_field_units or {}))

    # 2. workflow_incompatible entries — units empty (no PrimitiveSpec
    # to read from; output isn't a Series/Panel artifact anyway).
    for tool_name in WORKFLOW_INCOMPATIBLE_TOOLS:
        add(tool_name, {})

    # 3. Manifesto-only entries (manifest_typed_view tools — typed-
    # detail endpoints but no PrimitiveSpec).  Units empty; filled in
    # Phase 1+ when those tools get touched per the surface contract.
    for tool_name in manifest:
        add(tool_name, {})

    rows.sort(key=lambda r: r[0])
    return rows


# ----------------------------------------------------------------------------
# SQL emitter.
# ----------------------------------------------------------------------------


SQL_HEADER = """\
-- ============================================================================
-- Generated by database/populate_tool_metadata.py — DO NOT EDIT BY HAND.
-- Regenerate by re-running the script.
--
-- Phase 0 seed for macro_data.tool_metadata: populates mechanical
-- fields (tool_name, domain, category, output_field_units) for every
-- backend-shipped primitive.  Human-curated fields
-- (theoretical_reference, known_limitations, desk_narrative,
-- source_material_verified) stay NULL — they get filled per tool in
-- Phase 1.
--
-- ON CONFLICT (tool_name) DO UPDATE updates the four mechanical
-- columns + updated_at on every re-run, leaving human-curated values
-- untouched.  Safe to run any number of times.
--
-- APPLY:
--   docker exec -i macro-tsdb psql -U quantuser -d macrodata \\
--       < database/migrations/2026-05-26_phase0_tool_metadata_seed.sql
-- ============================================================================
"""


def emit_sql(
    rows: list[Tuple[str, str, Optional[str], Dict[str, Any]]],
) -> str:
    """Return the full SQL document as a string."""
    lines: list[str] = [SQL_HEADER, ""]

    for tool_name, domain, category, units in rows:
        # Conservative quoting: tool_name + domain + category come from
        # curated registries (no apostrophes expected), but we double up
        # any quotes defensively.
        tool_name_lit = _sql_string(tool_name)
        domain_lit = _sql_string(domain)
        category_lit = _sql_string(category) if category else "NULL"

        # JSONB literal: emit deterministic, sorted JSON.
        units_json = json.dumps(units, sort_keys=True, separators=(",", ":"))
        units_lit = f"{_sql_string(units_json)}::jsonb"

        lines.append(
            "INSERT INTO macro_data.tool_metadata\n"
            "    (tool_name, domain, category, output_field_units)\n"
            f"VALUES ({tool_name_lit}, {domain_lit}, {category_lit}, {units_lit})\n"
            "ON CONFLICT (tool_name) DO UPDATE SET\n"
            "    domain             = EXCLUDED.domain,\n"
            "    category           = EXCLUDED.category,\n"
            "    output_field_units = EXCLUDED.output_field_units,\n"
            "    updated_at         = NOW();\n"
        )

    # Single trailing newline (no extra blank line at EOF).
    return "\n".join(lines).rstrip("\n") + "\n"


def _sql_string(value: str) -> str:
    """SQL string literal with single-quote escaping."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


# ----------------------------------------------------------------------------
# CLI.
# ----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Write generated SQL to this file (default: stdout).  Typical "
            "value: database/migrations/2026-05-26_phase0_tool_metadata_seed.sql"
        ),
    )
    args = parser.parse_args()

    rows = assemble_rows()
    sql = emit_sql(rows)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(sql, encoding="utf-8")
        print(
            f"wrote {len(rows)} tool_metadata rows to {args.output}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(sql)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
