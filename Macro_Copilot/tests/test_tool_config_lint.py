"""
test_tool_config_lint.py — Consistency-check tests
====================================================

Covers ``shared/config/lint.py``:

  - check_yaml_consistency: empty input, single config, multi-config
    with no overlap, multi-config with agreeing values, multi-config
    with disagreeing values, three-way disagreement, value-mismatch
    only (source/rationale mismatch alone is NOT flagged).
  - discover_tool_configs: respects the
    ``rates_agent/*/tools/*/config.yaml`` convention.
  - main() CLI: zero / clean / dirty exit codes.

All YAML files are written into ``tmp_path`` per-test for isolation.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from shared.config.lint import (
    _METHODOLOGY_SOURCE_REGISTRY,
    check_source_tags,
    check_yaml_consistency,
    discover_tool_configs,
    main as lint_main,
)
from shared.config.tool_config import clear_tool_config_cache

# The real, committed methodology-source-tag registry.  The CLI tests
# below build synthetic ``tmp_path`` roots that have no ``docs/`` tree,
# so they point ``--registry`` at the real file; the conventions they
# write use the registered ``team_judgment_pending_review`` tag so the
# PR12 source-tag pass (now part of ``main``) stays clean.
_REAL_REGISTRY = (
    Path(__file__).resolve().parent.parent / _METHODOLOGY_SOURCE_REGISTRY
)


# ============================================================================
# Helpers
# ============================================================================

def _make_config(path: Path, *, tool_name: str, conventions: dict) -> Path:
    """Write a minimal valid config.yaml with the given tool name and
    conventions dict.  Each conventions value can be a scalar or a
    full dict; scalars get a default source/rationale wrapper."""
    lines = [
        "tool:",
        f"  name: {tool_name}",
        "  domain: test_domain",
        "  description: Test fixture.",
        "methodology:",
        "  what_it_does: Test.",
    ]
    if conventions:
        lines.append("conventions:")
        for k, v in conventions.items():
            if isinstance(v, dict):
                lines.append(f"  {k}:")
                for sk, sv in v.items():
                    if isinstance(sv, list):
                        lines.append(f"    {sk}: {sv}")
                    elif isinstance(sv, str):
                        lines.append(f"    {sk}: {sv!r}")
                    else:
                        lines.append(f"    {sk}: {sv}")
            else:
                lines.append(f"  {k}:")
                lines.append(f"    value: {v}")
                # A registered tag so the PR12 source-tag pass stays
                # clean — these fixtures exercise consistency, not tags.
                lines.append("    source: team_judgment_pending_review")
                lines.append("    rationale: test rationale")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


# ============================================================================
# check_yaml_consistency — pass cases
# ============================================================================

class TestCheckPasses:
    def test_empty_input_no_issues(self):
        assert check_yaml_consistency([]) == []

    def test_single_config_no_issues(self, tmp_path: Path):
        cfg = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={"window": 252},
        )
        assert check_yaml_consistency([cfg]) == []

    def test_two_configs_no_overlapping_keys(self, tmp_path: Path):
        a = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={"window_a": 252},
        )
        b = _make_config(
            tmp_path / "b.yaml",
            tool_name="beta",
            conventions={"window_b": 100},
        )
        # Conventions never overlap by key, so no comparison fires.
        assert check_yaml_consistency([a, b]) == []

    def test_two_configs_same_key_same_value(self, tmp_path: Path):
        a = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={"window": 252},
        )
        b = _make_config(
            tmp_path / "b.yaml",
            tool_name="beta",
            conventions={"window": 252},
        )
        assert check_yaml_consistency([a, b]) == []

    def test_source_mismatch_but_value_match_is_not_flagged(self, tmp_path: Path):
        """Two tools can legitimately cite different sources for the
        same numeric default — only the *value* is part of the
        consistency contract."""
        a = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={
                "window": {
                    "value": 252,
                    "source": "industry_standard_1y_window",
                    "rationale": "rationale a",
                },
            },
        )
        b = _make_config(
            tmp_path / "b.yaml",
            tool_name="beta",
            conventions={
                "window": {
                    "value": 252,
                    "source": "team_judgment_pending_review",
                    "rationale": "rationale b",
                },
            },
        )
        assert check_yaml_consistency([a, b]) == []


# ============================================================================
# check_yaml_consistency — fail cases
# ============================================================================

class TestCheckFails:
    def test_two_configs_same_key_different_value(self, tmp_path: Path):
        a = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={"window": 252},
        )
        b = _make_config(
            tmp_path / "b.yaml",
            tool_name="beta",
            conventions={"window": 504},
        )
        issues = check_yaml_consistency([a, b])

        assert len(issues) == 1
        issue = issues[0]
        assert issue.convention_name == "window"
        assert len(issue.occurrences) == 2

        tool_names = {occ.tool_name for occ in issue.occurrences}
        values = {occ.value for occ in issue.occurrences}
        assert tool_names == {"alpha", "beta"}
        assert values == {252, 504}

    def test_three_way_disagreement(self, tmp_path: Path):
        a = _make_config(
            tmp_path / "a.yaml", tool_name="alpha", conventions={"window": 252},
        )
        b = _make_config(
            tmp_path / "b.yaml", tool_name="beta", conventions={"window": 504},
        )
        c = _make_config(
            tmp_path / "c.yaml", tool_name="gamma", conventions={"window": 252},
        )
        issues = check_yaml_consistency([a, b, c])

        assert len(issues) == 1
        # All three tools surface as occurrences (alpha + gamma agree at 252,
        # beta sits at 504 — but the issue lists every contributing tool so
        # the human reviewer sees the full picture).
        assert {occ.tool_name for occ in issues[0].occurrences} == {
            "alpha", "beta", "gamma",
        }

    def test_multiple_independent_inconsistencies(self, tmp_path: Path):
        a = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={"window": 252, "fill_limit": 5},
        )
        b = _make_config(
            tmp_path / "b.yaml",
            tool_name="beta",
            conventions={"window": 504, "fill_limit": 10},
        )
        issues = check_yaml_consistency([a, b])
        assert len(issues) == 2
        names = sorted(i.convention_name for i in issues)
        assert names == ["fill_limit", "window"]

    def test_string_values_disagreeing(self, tmp_path: Path):
        a = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={"field": "YLD_YTM_MID"},
        )
        b = _make_config(
            tmp_path / "b.yaml",
            tool_name="beta",
            conventions={"field": "PX_LAST"},
        )
        issues = check_yaml_consistency([a, b])
        assert len(issues) == 1
        assert issues[0].convention_name == "field"

    def test_format_for_human_includes_each_occurrence(self, tmp_path: Path):
        a = _make_config(
            tmp_path / "a.yaml",
            tool_name="alpha",
            conventions={"window": 252},
        )
        b = _make_config(
            tmp_path / "b.yaml",
            tool_name="beta",
            conventions={"window": 504},
        )
        issues = check_yaml_consistency([a, b])
        rendered = issues[0].format_for_human()
        assert "INCONSISTENT" in rendered
        assert "window" in rendered
        assert "alpha" in rendered
        assert "beta" in rendered
        assert "252" in rendered
        assert "504" in rendered


# ============================================================================
# check_source_tags — PR12 / OPR12 methodology-source-tag registry
# ============================================================================

class TestSourceTags:
    def test_registered_exact_tag_passes(self, tmp_path: Path):
        cfg = _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={
                "window": {
                    "value": 252,
                    "source": "industry_standard_1y_window",
                    "rationale": "r",
                },
            },
        )
        assert check_source_tags([cfg], [], _REAL_REGISTRY) == []

    def test_registered_pattern_family_tag_passes(self, tmp_path: Path):
        # ``<primitive>_primitive_v1`` is a registered open family
        # (methodology_disclosure.md §2) — not an exact header, so this
        # exercises the pattern path that admits
        # ``rolling_regression_primitive_v1`` in the live catalogue.
        cfg = _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={
                "k": {
                    "value": 1,
                    "source": "some_primitive_primitive_v1",
                    "rationale": "r",
                },
            },
        )
        assert check_source_tags([cfg], [], _REAL_REGISTRY) == []

    def test_vague_tag_rejected(self, tmp_path: Path):
        cfg = _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={
                "k": {"value": 1, "source": "tbd", "rationale": "r"},
            },
        )
        issues = check_source_tags([cfg], [], _REAL_REGISTRY)
        assert len(issues) == 1
        assert issues[0].kind == "vague"
        assert issues[0].tag == "tbd"

    def test_bloomberg_vague_rejected_even_though_field_variant_registered(
        self, tmp_path: Path,
    ):
        # The registered tag is ``bloomberg_field_convention``; a bare
        # ``bloomberg`` is the vendor name and must be auto-rejected.
        cfg = _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={
                "k": {"value": 1, "source": "bloomberg", "rationale": "r"},
            },
        )
        issues = check_source_tags([cfg], [], _REAL_REGISTRY)
        assert len(issues) == 1
        assert issues[0].kind == "vague"

    def test_unregistered_bogus_tag_rejected(self, tmp_path: Path):
        cfg = _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={
                "k": {
                    "value": 1,
                    "source": "totally_made_up_tag",
                    "rationale": "r",
                },
            },
        )
        issues = check_source_tags([cfg], [], _REAL_REGISTRY)
        assert len(issues) == 1
        assert issues[0].kind == "unregistered"
        assert issues[0].tag == "totally_made_up_tag"

    def test_missing_registry_raises(self, tmp_path: Path):
        from shared.config.lint import SourceTagRegistryError

        cfg = _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={"k": 1},
        )
        with pytest.raises(SourceTagRegistryError):
            check_source_tags([cfg], [], tmp_path / "nope.md")

    def test_cli_rejects_vague_tag_nonzero_exit(self, tmp_path: Path, capsys):
        # End-to-end: a vague tag drives ``main`` to a non-zero exit.
        _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={
                "k": {"value": 1, "source": "tbd", "rationale": "r"},
            },
        )
        rc = lint_main(
            ["--root", str(tmp_path), "--registry", str(_REAL_REGISTRY)]
        )
        assert rc == 1
        captured = capsys.readouterr()
        assert "UNREGISTERED-SOURCE" in captured.err


# ============================================================================
# discover_tool_configs
# ============================================================================

class TestDiscover:
    def test_walks_conventional_path(self, tmp_path: Path):
        # Mimic the conventional layout: rates_agent/<domain>/tools/<tool>/config.yaml
        for domain, tool in [
            ("sovereign_bonds", "alpha"),
            ("sovereign_bonds", "beta"),
            ("ois", "gamma"),
        ]:
            _make_config(
                tmp_path / f"rates_agent/{domain}/tools/{tool}/config.yaml",
                tool_name=tool,
                conventions={"window": 252},
            )

        # A misplaced config that should NOT be picked up.
        _make_config(
            tmp_path / "rates_agent/sovereign_bonds/tools/orphan.yaml",
            tool_name="orphan",
            conventions={},
        )

        # Also drop a stray YAML at the wrong nesting depth.
        (tmp_path / "rates_agent/loose.yaml").write_text("not-a-tool-config\n")

        found = discover_tool_configs(tmp_path)
        names = sorted(p.parent.name for p in found)
        assert names == ["alpha", "beta", "gamma"]

    def test_returns_sorted_paths(self, tmp_path: Path):
        for tool in ["zeta", "alpha", "mu"]:
            _make_config(
                tmp_path / f"rates_agent/d/tools/{tool}/config.yaml",
                tool_name=tool,
                conventions={"x": 1},
            )

        found = discover_tool_configs(tmp_path)
        # Sorted by path (which puts directories alphabetically).
        assert [p.parent.name for p in found] == ["alpha", "mu", "zeta"]

    def test_no_matches_returns_empty(self, tmp_path: Path):
        assert discover_tool_configs(tmp_path) == []


# ============================================================================
# CLI entry point
# ============================================================================

class TestCli:
    def test_clean_run_exits_zero(self, tmp_path: Path, capsys):
        _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={"window": 252},
        )
        rc = lint_main(
            ["--root", str(tmp_path), "--registry", str(_REAL_REGISTRY)]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "no convention drift" in captured.out.lower()

    def test_no_configs_exits_zero(self, tmp_path: Path, capsys):
        rc = lint_main(
            ["--root", str(tmp_path), "--registry", str(_REAL_REGISTRY)]
        )
        assert rc == 0

    def test_dirty_run_exits_one_and_prints_to_stderr(
        self, tmp_path: Path, capsys,
    ):
        _make_config(
            tmp_path / "rates_agent/d/tools/a/config.yaml",
            tool_name="a",
            conventions={"window": 252},
        )
        _make_config(
            tmp_path / "rates_agent/d/tools/b/config.yaml",
            tool_name="b",
            conventions={"window": 504},
        )
        rc = lint_main(
            ["--root", str(tmp_path), "--registry", str(_REAL_REGISTRY)]
        )
        assert rc == 1
        captured = capsys.readouterr()
        # Issue list goes to stderr so CI logs are clean on success.
        assert "INCONSISTENT" in captured.err
        assert "window" in captured.err

    def test_quiet_flag_suppresses_clean_output(
        self, tmp_path: Path, capsys,
    ):
        _make_config(
            tmp_path / "rates_agent/d/tools/t/config.yaml",
            tool_name="t",
            conventions={"window": 252},
        )
        rc = lint_main(
            ["--root", str(tmp_path), "--quiet", "--registry", str(_REAL_REGISTRY)]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_broken_yaml_exits_two(self, tmp_path: Path, capsys):
        bad_path = tmp_path / "rates_agent/d/tools/t/config.yaml"
        bad_path.parent.mkdir(parents=True)
        bad_path.write_text("tool: : :\n")  # malformed
        rc = lint_main(
            ["--root", str(tmp_path), "--registry", str(_REAL_REGISTRY)]
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "ERROR" in captured.err
