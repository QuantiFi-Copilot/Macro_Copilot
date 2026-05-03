"""Tests for shared.config.operator_config + lint operator-discovery extension.

Covers (build plan v5 / R4):

  - schema validation of operator config YAMLs
  - process-wide caching
  - lint discovery of operator configs at shared/operators/*/config.yaml
  - lint refusal on malformed operator config
  - the bundled align_series config loads cleanly
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared.config.lint import (
    discover_operator_configs,
    discover_tool_configs,
    validate_operator_configs,
)
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    clear_operator_config_cache,
    load_operator_config,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ===========================================================================
# Bundled config loads cleanly
# ===========================================================================


class TestBundledAlignSeriesConfig:
    def test_align_series_config_loads(self):
        from shared.operators.align_series import CONFIG_PATH
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "align_series"
        assert cfg.operator.method_family == "alignment"
        assert "join_policy" in cfg.defaults
        assert cfg.default_value("join_policy") == "inner"
        assert cfg.default_value("fill_policy") == "raw"
        assert cfg.default_value("fill_limit") is None

    def test_planned_extensions_populated(self):
        from shared.operators.align_series import CONFIG_PATH
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.methodology.planned_extensions, (
            "align_series methodology should declare at least one "
            "planned extension to make deferred surfaces visible."
        )


# ===========================================================================
# Schema validation
# ===========================================================================


class TestOperatorConfigSchema:
    def test_extra_top_level_keys_rejected(self, tmp_path: Path):
        bad = {
            "operator": {
                "name": "x", "method_family": "alignment",
                "version": "1.0.0", "description": "x",
            },
            "defaults": {},
            "methodology": {"what_it_does": "x"},
            "extra_block": "this should be rejected",
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(bad))
        with pytest.raises(OperatorConfigError):
            load_operator_config(p)

    def test_missing_required_block_rejected(self, tmp_path: Path):
        bad = {"operator": {
            "name": "x", "method_family": "alignment",
            "version": "1.0.0", "description": "x",
        }}
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(bad))
        with pytest.raises(OperatorConfigError):
            load_operator_config(p)

    def test_unknown_method_family_rejected(self, tmp_path: Path):
        bad = {
            "operator": {
                "name": "x",
                "method_family": "not_a_real_family",
                "version": "1.0.0", "description": "x",
            },
            "defaults": {},
            "methodology": {"what_it_does": "x"},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(bad))
        with pytest.raises(OperatorConfigError):
            load_operator_config(p)

    def test_default_value_outside_valid_values_rejected(self, tmp_path: Path):
        bad = {
            "operator": {
                "name": "x", "method_family": "alignment",
                "version": "1.0.0", "description": "x",
            },
            "defaults": {
                "join_policy": {
                    "value": "weird",
                    "source": "test",
                    "rationale": "test",
                    "valid_values": ["inner", "outer"],
                },
            },
            "methodology": {"what_it_does": "x"},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(bad))
        with pytest.raises(OperatorConfigError, match="not in valid_values"):
            load_operator_config(p)

    def test_caching(self, tmp_path: Path):
        good = {
            "operator": {
                "name": "x", "method_family": "alignment",
                "version": "1.0.0", "description": "x",
            },
            "defaults": {},
            "methodology": {"what_it_does": "x"},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(good))
        c1 = load_operator_config(p)
        c2 = load_operator_config(p)
        assert c1 is c2  # cached identity


# ===========================================================================
# Lint discovery + validation
# ===========================================================================


class TestLintOperatorDiscovery:
    def test_discovers_align_series(self):
        op_paths = discover_operator_configs(_project_root())
        names = [p.parent.name for p in op_paths]
        assert "align_series" in names

    def test_validates_clean(self):
        op_paths = discover_operator_configs(_project_root())
        # Must not raise.
        configs = validate_operator_configs(op_paths)
        assert len(configs) == len(op_paths)
        assert all(isinstance(c, OperatorConfig) for c in configs)

    def test_tool_discovery_unaffected(self):
        """Operator-config support must not break existing tool-config
        discovery."""
        tool_paths = discover_tool_configs(_project_root())
        # At least the existing 11 tool configs must still be found.
        assert len(tool_paths) >= 11
        # And NONE of the operator configs leaked into the tool-paths.
        op_paths = discover_operator_configs(_project_root())
        assert not (set(tool_paths) & set(op_paths))
