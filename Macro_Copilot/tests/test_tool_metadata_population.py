"""
test_tool_metadata_population.py — pure-python tests for the Phase 0
``database/populate_tool_metadata.py`` SQL generator.

Validates the generator's output WITHOUT touching the live DB:

  1. **Row count.**  Every backend-shipped tool is covered: the union of
     ``_PRIMITIVE_SPECS`` + ``WORKFLOW_INCOMPATIBLE_TOOLS`` +
     manifesto-only tools.  This catches the case where a new tool
     ships in one registry but the generator misses it.

  2. **No duplicate tool_name.**  Every row's primary key is unique.

  3. **No unknown domain.**  Every row's ``domain`` is in the closed
     family the schema's CHECK constraint enforces.

  4. **Output_field_units is valid JSON object.**  Catches a stray
     "}" / typo that would crash the SQL apply.

  5. **Deterministic regeneration.**  Two consecutive runs produce
     byte-identical SQL.  Catches non-deterministic dict-ordering
     issues + the no-trailing-blank-line property the seed needs.

These checks complement (do not replace) the on-DB acceptance the
apply step gives — once the generated SQL is applied, the schema's
CHECK constraint + PRIMARY KEY enforce these invariants again at
the DB layer.

See:
  - docs_revamped/05_decisions/0015-tool-metadata-db-table.md
  - docs_revamped/03_standards/tool_lifecycle.md §3
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Imports after path bootstrap.
# pylint: disable=wrong-import-position
from database.populate_tool_metadata import (  # noqa: E402
    _TOOL_NAME_ALIASES,
    assemble_rows,
    emit_sql,
    load_manifesto_index,
)


# Mirror of the schema's CHECK constraint.  Kept here as a closed
# family — extend in lockstep with database/schema.sql when a new
# domain ships.
ALLOWED_DOMAINS: set[str] = {
    "sovereign_bonds",
    "ois",
    "inflation_indexed_bonds",
    "inflation_swaps",
    "policy_futures",
    "bond_futures",
}


class ToolMetadataPopulationTests(unittest.TestCase):
    """Validate the Phase 0 seed generator's output."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = assemble_rows()
        cls.sql = emit_sql(cls.rows)

    # ---- 1. Row count ---------------------------------------------------

    def test_row_count_matches_backend_registry_union(self) -> None:
        """Every backend tool the system knows about must appear."""
        from rates_agent.workflows import (  # noqa: E402
            WORKFLOW_INCOMPATIBLE_TOOLS,
            _PRIMITIVE_SPECS,
        )

        manifest_keys = set(load_manifesto_index().keys())
        expected = (
            set(_PRIMITIVE_SPECS.keys())
            | set(WORKFLOW_INCOMPATIBLE_TOOLS)
            | manifest_keys
        )
        actual = {row[0] for row in self.rows}
        missing = expected - actual
        extra = actual - expected
        self.assertEqual(
            missing,
            set(),
            f"generator missed {len(missing)} expected tool(s): {sorted(missing)}",
        )
        self.assertEqual(
            extra,
            set(),
            f"generator produced {len(extra)} unexpected tool(s): {sorted(extra)}",
        )

    # ---- 2. Uniqueness --------------------------------------------------

    def test_no_duplicate_tool_names(self) -> None:
        names = [row[0] for row in self.rows]
        self.assertEqual(
            len(names),
            len(set(names)),
            "duplicate tool_name in generator output (would violate PRIMARY KEY)",
        )

    # ---- 3. Domain closed-family ----------------------------------------

    def test_every_domain_is_in_allowed_family(self) -> None:
        """Mirrors the schema's CHECK constraint defensively."""
        bad = [(t, d) for t, d, _c, _u in self.rows if d not in ALLOWED_DOMAINS]
        self.assertEqual(
            bad,
            [],
            (
                f"{len(bad)} row(s) carry a domain outside the closed "
                f"family {sorted(ALLOWED_DOMAINS)}: {bad}.  Schema CHECK "
                f"constraint would reject these at apply time."
            ),
        )

    def test_no_unknown_domain(self) -> None:
        """Catches the older bug where alias-normalisation was missing."""
        unknowns = [t for t, d, _c, _u in self.rows if d == "unknown"]
        self.assertEqual(
            unknowns,
            [],
            (
                f"{len(unknowns)} row(s) have domain='unknown' — alias "
                f"normalisation may be missing.  Check "
                f"_TOOL_NAME_ALIASES for the affected tools: {unknowns}"
            ),
        )

    # ---- 4. output_field_units shape ------------------------------------

    def test_output_field_units_is_json_object_for_every_row(self) -> None:
        for tool_name, _d, _c, units in self.rows:
            self.assertIsInstance(
                units,
                dict,
                f"{tool_name}: output_field_units must be a dict (JSON object), got {type(units).__name__}",
            )
            # Round-trip through JSON to catch non-serialisable values
            # (e.g. enums, datetimes) that would crash the SQL apply.
            try:
                json.dumps(units)
            except (TypeError, ValueError) as exc:
                self.fail(
                    f"{tool_name}: output_field_units is not JSON-serialisable: {exc}"
                )

    # ---- 5. Determinism -------------------------------------------------

    def test_generator_is_deterministic(self) -> None:
        """Re-running emit_sql must produce byte-identical output."""
        first = emit_sql(assemble_rows())
        second = emit_sql(assemble_rows())
        self.assertEqual(
            first,
            second,
            "emit_sql produced different output on consecutive runs — "
            "non-determinism risks seed file churn on every regeneration.",
        )

    def test_sql_has_single_trailing_newline(self) -> None:
        """SQL file should end with exactly one '\\n' (no blank line at EOF)."""
        self.assertTrue(
            self.sql.endswith("\n"),
            "SQL must end with a single trailing newline",
        )
        self.assertFalse(
            self.sql.endswith("\n\n"),
            "SQL must NOT end with a blank line (git diff --check flags this)",
        )

    # ---- 6. SQL surface sanity ------------------------------------------

    def test_every_insert_has_on_conflict_do_update(self) -> None:
        """Idempotency guarantee — re-runs MUST upsert, not error."""
        # Count actual SQL statements (not comment-prose mentions of the
        # clause).  Statements start at column 0 with INSERT / ON CONFLICT;
        # comment lines start with "-- " so the line-anchor regex is
        # sufficient to skip them.
        insert_count = len(
            re.findall(
                r"^INSERT INTO macro_data\.tool_metadata\b", self.sql, re.MULTILINE
            )
        )
        upsert_count = len(
            re.findall(
                r"^ON CONFLICT \(tool_name\) DO UPDATE\b", self.sql, re.MULTILINE
            )
        )
        self.assertEqual(
            insert_count,
            upsert_count,
            f"every INSERT must carry ON CONFLICT DO UPDATE for idempotency "
            f"(got {insert_count} INSERTs vs {upsert_count} ON CONFLICTs)",
        )

    def test_insert_count_matches_row_count(self) -> None:
        insert_count = len(
            re.findall(
                r"^INSERT INTO macro_data\.tool_metadata\b", self.sql, re.MULTILINE
            )
        )
        self.assertEqual(
            insert_count,
            len(self.rows),
            f"SQL has {insert_count} INSERTs but generator returned "
            f"{len(self.rows)} rows — counts must match",
        )

    # ---- 7. Alias table integrity ---------------------------------------

    def test_alias_targets_are_actual_backend_tools(self) -> None:
        """Every alias target must exist in _PRIMITIVE_SPECS or WORKFLOW_INCOMPATIBLE.

        A dangling alias (target tool name doesn't exist) would silently
        misroute the manifest lookup.
        """
        from rates_agent.workflows import (  # noqa: E402
            WORKFLOW_INCOMPATIBLE_TOOLS,
            _PRIMITIVE_SPECS,
        )

        known_tools = set(_PRIMITIVE_SPECS.keys()) | set(WORKFLOW_INCOMPATIBLE_TOOLS)
        dangling = [
            (shorthand, canonical)
            for shorthand, canonical in _TOOL_NAME_ALIASES.items()
            if canonical not in known_tools
        ]
        self.assertEqual(
            dangling,
            [],
            f"{len(dangling)} alias(es) point to non-existent backend tool: {dangling}",
        )


# ---------------------------------------------------------------------------
# pytest entry point (file is also runnable directly via ``python -m
# unittest``).
# ---------------------------------------------------------------------------


def test_tool_metadata_population_via_pytest() -> None:
    """Pytest-friendly wrapper: runs every method of the unittest class."""
    suite = unittest.TestLoader().loadTestsFromTestCase(ToolMetadataPopulationTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.wasSuccessful(), (
        f"tool-metadata population checks failed: "
        f"{len(result.failures)} failure(s) + {len(result.errors)} error(s)"
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
