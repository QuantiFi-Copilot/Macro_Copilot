"""
test_tool_config.py — Loader + Convention validation tests
============================================================

Covers ``shared/config/tool_config.py``:

  - Convention model: scalar-only, valid_range semantics, frozen
  - ToolConfig model: required blocks, extra-key rejection
  - load_tool_config: happy path, missing file, malformed YAML,
    schema-violation, empty file, non-mapping root
  - Cache behaviour: same path → same instance; clear_tool_config_cache
    drops it

The loader is exercised against ad-hoc YAML written into
``tmp_path`` so each test is self-contained.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.config.tool_config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolConfigError,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)


# ============================================================================
# Helpers
# ============================================================================

_VALID_YAML = """\
tool:
  name: dummy_tool
  domain: sovereign_bonds
  description: A test fixture.

conventions:
  z_score_window_days:
    value: 252
    source: industry_standard_1y_window
    rationale: "Bloomberg's default 1Y rolling window."
    valid_range: [60, 1260]
  default_field_name:
    value: YLD_YTM_MID
    source: bloomberg_field_convention
    rationale: "Mid-yield-to-maturity is the canonical observation."

methodology:
  what_it_does: |
    Computes a thing.
  assumptions:
    - "Yields stored as percent."
  citations: []
"""


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test gets a fresh cache so cross-test pollution isn't possible."""
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


# ============================================================================
# Convention model — scalar enforcement
# ============================================================================

class TestConventionScalar:
    def test_int_value_accepted(self):
        c = Convention(value=252, source="x", rationale="y")
        assert c.value == 252

    def test_float_value_accepted(self):
        c = Convention(value=1.5, source="x", rationale="y")
        assert c.value == 1.5

    def test_str_value_accepted(self):
        c = Convention(value="YLD_YTM_MID", source="x", rationale="y")
        assert c.value == "YLD_YTM_MID"

    def test_bool_value_accepted(self):
        c = Convention(value=True, source="x", rationale="y")
        assert c.value is True

    def test_dict_value_rejected(self):
        with pytest.raises(ValidationError, match="value must be scalar"):
            Convention(value={"a": 1}, source="x", rationale="y")

    def test_list_value_rejected(self):
        with pytest.raises(ValidationError, match="value must be scalar"):
            Convention(value=[1, 2, 3], source="x", rationale="y")

    def test_none_value_rejected(self):
        with pytest.raises(ValidationError, match="value cannot be None"):
            Convention(value=None, source="x", rationale="y")


# ============================================================================
# Convention model — required field constraints
# ============================================================================

class TestConventionRequired:
    def test_empty_source_rejected(self):
        with pytest.raises(ValidationError, match="source"):
            Convention(value=1, source="", rationale="y")

    def test_empty_rationale_rejected(self):
        with pytest.raises(ValidationError, match="rationale"):
            Convention(value=1, source="x", rationale="")

    def test_extra_keys_rejected(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            Convention(
                value=1, source="x", rationale="y", units="bps",  # noqa
            )

    def test_frozen_after_construction(self):
        c = Convention(value=1, source="x", rationale="y")
        with pytest.raises(ValidationError):
            c.value = 2  # Pydantic frozen rejects assignment


# ============================================================================
# Convention model — valid_range semantics
# ============================================================================

class TestConventionRange:
    def test_in_range_passes(self):
        c = Convention(value=252, source="x", rationale="y", valid_range=[60, 1260])
        assert c.valid_range == [60, 1260]

    def test_at_lower_bound_passes(self):
        Convention(value=60, source="x", rationale="y", valid_range=[60, 1260])

    def test_at_upper_bound_passes(self):
        Convention(value=1260, source="x", rationale="y", valid_range=[60, 1260])

    def test_below_range_rejected(self):
        with pytest.raises(ValidationError, match=r"outside valid_range"):
            Convention(value=50, source="x", rationale="y", valid_range=[60, 1260])

    def test_above_range_rejected(self):
        with pytest.raises(ValidationError, match=r"outside valid_range"):
            Convention(value=2000, source="x", rationale="y", valid_range=[60, 1260])

    def test_inverted_range_rejected(self):
        with pytest.raises(ValidationError, match=r"<="):
            Convention(value=100, source="x", rationale="y", valid_range=[1260, 60])

    def test_range_on_str_rejected(self):
        with pytest.raises(ValidationError, match=r"valid_range only applies to numeric"):
            Convention(
                value="YLD_YTM_MID", source="x", rationale="y", valid_range=[1, 10]
            )

    def test_range_on_bool_rejected(self):
        with pytest.raises(ValidationError, match=r"valid_range only applies to numeric"):
            Convention(
                value=True, source="x", rationale="y", valid_range=[0, 1]
            )

    def test_range_with_one_element_rejected(self):
        with pytest.raises(ValidationError):
            Convention(
                value=1, source="x", rationale="y", valid_range=[5],
            )

    def test_range_with_three_elements_rejected(self):
        with pytest.raises(ValidationError):
            Convention(
                value=1, source="x", rationale="y", valid_range=[1, 5, 10],
            )

    def test_no_range_is_fine(self):
        c = Convention(value="anything", source="x", rationale="y")
        assert c.valid_range is None


# ============================================================================
# ToolConfig structure
# ============================================================================

class TestToolConfigShape:
    def test_minimal_valid(self):
        cfg = ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
        )
        assert cfg.tool.name == "t"
        assert cfg.conventions == {}
        assert cfg.methodology.assumptions == []

    def test_extra_top_level_key_rejected(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            ToolConfig(
                tool=ToolMeta(name="t", domain="d", description="x"),
                methodology=MethodologyMeta(what_it_does="x"),
                bogus_field=42,  # noqa
            )

    def test_methodology_required(self):
        with pytest.raises(ValidationError, match="methodology"):
            ToolConfig(
                tool=ToolMeta(name="t", domain="d", description="x"),
            )

    def test_convention_value_lookup_helper(self):
        cfg = ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                "k": Convention(value=42, source="s", rationale="r"),
            },
        )
        assert cfg.convention_value("k") == 42

    def test_convention_value_missing_key_raises(self):
        cfg = ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
        )
        with pytest.raises(KeyError, match=r"absent"):
            cfg.convention_value("absent")


# ============================================================================
# load_tool_config — happy path
# ============================================================================

class TestLoadHappyPath:
    def test_loads_valid_yaml(self, tmp_path: Path):
        cfg_path = _write(tmp_path / "config.yaml", _VALID_YAML)
        cfg = load_tool_config(cfg_path)

        assert cfg.tool.name == "dummy_tool"
        assert cfg.tool.domain == "sovereign_bonds"

        assert "z_score_window_days" in cfg.conventions
        assert cfg.conventions["z_score_window_days"].value == 252
        assert cfg.conventions["z_score_window_days"].valid_range == [60, 1260]

        assert cfg.conventions["default_field_name"].value == "YLD_YTM_MID"
        assert cfg.conventions["default_field_name"].valid_range is None

        assert "Yields stored as percent." in cfg.methodology.assumptions

    def test_string_path_accepted(self, tmp_path: Path):
        cfg_path = _write(tmp_path / "config.yaml", _VALID_YAML)
        cfg = load_tool_config(str(cfg_path))
        assert cfg.tool.name == "dummy_tool"

    def test_relative_path_resolved(self, tmp_path: Path, monkeypatch):
        cfg_path = _write(tmp_path / "config.yaml", _VALID_YAML)
        monkeypatch.chdir(tmp_path)
        cfg = load_tool_config("config.yaml")
        assert cfg.tool.name == "dummy_tool"


# ============================================================================
# load_tool_config — error paths
# ============================================================================

class TestLoadErrorPaths:
    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(ToolConfigError, match="not found"):
            load_tool_config(tmp_path / "does_not_exist.yaml")

    def test_malformed_yaml(self, tmp_path: Path):
        bad = _write(tmp_path / "bad.yaml", "tool: : :\n  this is broken\n")
        with pytest.raises(ToolConfigError, match="YAML parse error"):
            load_tool_config(bad)

    def test_empty_file(self, tmp_path: Path):
        empty = _write(tmp_path / "empty.yaml", "")
        with pytest.raises(ToolConfigError, match="empty"):
            load_tool_config(empty)

    def test_root_must_be_mapping(self, tmp_path: Path):
        scalar = _write(tmp_path / "scalar.yaml", "just_a_string\n")
        with pytest.raises(ToolConfigError, match="must be a mapping"):
            load_tool_config(scalar)

    def test_missing_required_block(self, tmp_path: Path):
        no_methodology = _write(
            tmp_path / "incomplete.yaml",
            "tool:\n  name: t\n  domain: d\n  description: x\n",
        )
        with pytest.raises(ToolConfigError, match="methodology"):
            load_tool_config(no_methodology)

    def test_value_outside_valid_range(self, tmp_path: Path):
        bad = _write(
            tmp_path / "bad.yaml",
            """\
tool:
  name: t
  domain: d
  description: x
conventions:
  k:
    value: 5
    source: s
    rationale: r
    valid_range: [10, 20]
methodology:
  what_it_does: x
""",
        )
        with pytest.raises(ToolConfigError, match="outside valid_range"):
            load_tool_config(bad)

    def test_unknown_top_level_key(self, tmp_path: Path):
        bad = _write(
            tmp_path / "bad.yaml",
            """\
tool:
  name: t
  domain: d
  description: x
methodology:
  what_it_does: x
mystery_section:
  foo: bar
""",
        )
        with pytest.raises(ToolConfigError, match="Extra inputs"):
            load_tool_config(bad)


# ============================================================================
# Cache behaviour
# ============================================================================

class TestCache:
    def test_same_path_returns_same_instance(self, tmp_path: Path):
        cfg_path = _write(tmp_path / "config.yaml", _VALID_YAML)
        a = load_tool_config(cfg_path)
        b = load_tool_config(cfg_path)
        assert a is b, "Cache should return identical object reference"

    def test_clear_cache_returns_new_instance(self, tmp_path: Path):
        cfg_path = _write(tmp_path / "config.yaml", _VALID_YAML)
        a = load_tool_config(cfg_path)
        clear_tool_config_cache()
        b = load_tool_config(cfg_path)
        assert a is not b, "After clear, a fresh load must return a new object"
        assert a == b, "But the contents must be equal"

    def test_string_and_path_canonicalised_to_same_cache_entry(
        self, tmp_path: Path,
    ):
        cfg_path = _write(tmp_path / "config.yaml", _VALID_YAML)
        a = load_tool_config(str(cfg_path))
        b = load_tool_config(cfg_path)  # Path
        assert a is b

    def test_cache_does_not_leak_across_paths(self, tmp_path: Path):
        a_path = _write(tmp_path / "a.yaml", _VALID_YAML)
        b_path = _write(
            tmp_path / "b.yaml",
            _VALID_YAML.replace("dummy_tool", "other_tool"),
        )
        a = load_tool_config(a_path)
        b = load_tool_config(b_path)
        assert a.tool.name == "dummy_tool"
        assert b.tool.name == "other_tool"
        assert a is not b
