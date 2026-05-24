"""Wiring / import tests for the FX forwards tools (Phase A step 7b).

These tests run without a DB connection and verify the contractual
plumbing Codex's step-7 review required:

- Schemas import and instantiate with defaults.
- Each tool's config.yaml loads cleanly via
  ``shared.config.load_tool_config``.
- The FX MCP server registers both forwards tools (calculate_fx_carry
  + get_fx_forward_curve) under the expected names.
- The FX manifest YAML enumerates both tools as ``built`` with
  pointers to the right MCP function names and compute symbols.

If any of these fails, the tools are reachable in compute.py but
won't actually surface through the Copilot stack — which is the
"orphan compute file" failure mode Codex flagged.

Standalone runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from fx_agent.forwards.tools.forward_curve import (  # noqa: E402
    CONFIG_PATH as FORWARD_CURVE_CONFIG_PATH,
    FXForwardCurveInput,
    FXForwardCurveOutput,
    FXForwardCurveRow,
    get_fx_forward_curve,
)
from fx_agent.forwards.tools.fx_carry import (  # noqa: E402
    CONFIG_PATH as FX_CARRY_CONFIG_PATH,
    FXCarryInput,
    FXCarryOutput,
    FXCarryRow,
    get_fx_carry,
)
from shared.config import load_tool_config  # noqa: E402


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


def _pass(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name=name, status="PASS", detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="FAIL", detail=detail)


def test_schemas_instantiate() -> CheckResult:
    """Default-constructed schemas must hold the documented defaults."""
    carry_in = FXCarryInput()
    if carry_in.tenor != "1M":
        return _fail(
            "schemas_instantiate",
            f"FXCarryInput default tenor != '1M', got {carry_in.tenor!r}",
        )
    if carry_in.rank_by != "carry_signed":
        return _fail(
            "schemas_instantiate",
            f"FXCarryInput default rank_by != 'carry_signed', got {carry_in.rank_by!r}",
        )
    if carry_in.top_n is not None:
        return _fail(
            "schemas_instantiate",
            f"FXCarryInput default top_n != None, got {carry_in.top_n!r}",
        )

    curve_in = FXForwardCurveInput(pair="EURUSD")
    if curve_in.pair != "EURUSD" or curve_in.lookback_days != 365:
        return _fail(
            "schemas_instantiate",
            f"FXForwardCurveInput defaults wrong: pair={curve_in.pair!r}, "
            f"lookback_days={curve_in.lookback_days}",
        )

    # Output / Row classes import cleanly.
    _ = (FXCarryOutput, FXCarryRow, FXForwardCurveOutput, FXForwardCurveRow)
    return _pass(
        "schemas_instantiate",
        "FXCarryInput + FXForwardCurveInput defaults match docstrings",
    )


def test_tool_callables_imported() -> CheckResult:
    """get_fx_carry and get_fx_forward_curve must import and be callable."""
    if not callable(get_fx_carry):
        return _fail("callables", "get_fx_carry is not callable")
    if not callable(get_fx_forward_curve):
        return _fail("callables", "get_fx_forward_curve is not callable")
    return _pass(
        "callables",
        "get_fx_carry + get_fx_forward_curve are callable",
    )


def test_configs_load() -> CheckResult:
    """Both tool configs must load cleanly and expose the expected
    convention keys.

    The two tools share most of the surface (tenor days, divisors,
    rolling-stat conventions, rounding), but only fx_carry has
    ``default_tenor`` because get_fx_forward_curve takes ``pair`` as
    a required input (no "default pair" makes sense for a curve).
    """
    shared_keys = {
        "default_fx_spot_field",
        "default_fx_forward_field",
        "tenor_1w_days",
        "tenor_1m_days",
        "tenor_3m_days",
        "tenor_6m_days",
        "tenor_12m_days",
        "annualization_days",
        "jpy_forward_points_divisor",
        "default_forward_points_divisor",
        "z_score_window_days",
        "z_score_min_periods",
        "z_score_ddof",
        "trailing_range_window_days",
    }
    fx_carry_only_keys = {"default_tenor"}

    carry_cfg = load_tool_config(FX_CARRY_CONFIG_PATH)
    required_carry = shared_keys | fx_carry_only_keys
    missing_carry: list[str] = []
    for k in required_carry:
        try:
            carry_cfg.convention_value(k)
        except KeyError:
            missing_carry.append(k)
    if missing_carry:
        return _fail(
            "configs_load",
            f"fx_carry config missing keys: {sorted(missing_carry)}",
        )

    curve_cfg = load_tool_config(FORWARD_CURVE_CONFIG_PATH)
    missing_curve: list[str] = []
    for k in shared_keys:
        try:
            curve_cfg.convention_value(k)
        except KeyError:
            missing_curve.append(k)
    if missing_curve:
        return _fail(
            "configs_load",
            f"forward_curve config missing keys: {sorted(missing_curve)}",
        )

    return _pass(
        "configs_load",
        f"both configs load; {len(shared_keys)} shared keys + "
        f"{len(fx_carry_only_keys)} fx_carry-only present",
    )


def test_mcp_server_registers_both_tools() -> CheckResult:
    """The FX MCP server must register both forwards tools under the
    canonical function names referenced from the manifest."""
    import fx_agent.mcp_server as mcp_mod

    tool_manager = mcp_mod.mcp._tool_manager
    registered = set(tool_manager._tools.keys())
    expected = {
        "get_fx_spot_level_tool",
        "scan_fx_spot_tool",
        "calculate_fx_carry_tool",
        "get_fx_forward_curve_tool",
    }
    missing = expected - registered
    if missing:
        return _fail(
            "mcp_registration",
            f"MCP server missing tools: {missing} "
            f"(registered: {sorted(registered)})",
        )
    return _pass(
        "mcp_registration",
        f"all 4 expected FX MCP tools registered: {sorted(expected)}",
    )


def test_manifest_lists_both_forwards_tools() -> CheckResult:
    """The fx_agent manifest must list both forwards tools as ``built``
    with the right MCP function names + compute symbols."""
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = (
        repo_root
        / "manifesto"
        / "03_tool_manifest"
        / "fx_agent"
        / "01_fx_manifest.yml"
    )
    if not manifest_path.exists():
        return _fail("manifest_present", f"missing: {manifest_path}")

    manifest = yaml.safe_load(manifest_path.read_text())
    tools = {t["name"]: t for t in manifest.get("tools", [])}

    expected = {
        "calculate_fx_carry": {
            "mcp_function": "calculate_fx_carry_tool",
            "compute_module": (
                "fx_agent/forwards/tools/fx_carry/compute.py::get_fx_carry"
            ),
        },
        "get_fx_forward_curve": {
            "mcp_function": "get_fx_forward_curve_tool",
            "compute_module": (
                "fx_agent/forwards/tools/forward_curve/compute.py::"
                "get_fx_forward_curve"
            ),
        },
    }
    for tool_name, want in expected.items():
        entry = tools.get(tool_name)
        if entry is None:
            return _fail(
                "manifest_entries",
                f"manifest missing tool entry: {tool_name}",
            )
        if entry.get("status") != "built":
            return _fail(
                "manifest_entries",
                f"{tool_name}: status={entry.get('status')!r} (expected 'built')",
            )
        impl = entry.get("implementation", {})
        if impl.get("tool_function") != want["mcp_function"]:
            return _fail(
                "manifest_entries",
                f"{tool_name}: tool_function={impl.get('tool_function')!r}, "
                f"expected {want['mcp_function']!r}",
            )
        if impl.get("compute") != want["compute_module"]:
            return _fail(
                "manifest_entries",
                f"{tool_name}: compute={impl.get('compute')!r}, "
                f"expected {want['compute_module']!r}",
            )

    return _pass(
        "manifest_entries",
        f"both forwards tools present as 'built' with correct MCP / compute pointers",
    )


# ============================================================================
# Runner
# ============================================================================


def main() -> int:
    print("=" * 80)
    print("FX TOOLS WIRING — IMPORT / CONFIG / MCP / MANIFEST")
    print("=" * 80)

    results: list[CheckResult] = []
    for test_fn in (
        test_schemas_instantiate,
        test_tool_callables_imported,
        test_configs_load,
        test_mcp_server_registers_both_tools,
        test_manifest_lists_both_forwards_tools,
    ):
        try:
            results.append(test_fn())
        except Exception as exc:  # noqa: BLE001
            results.append(
                _fail(
                    test_fn.__name__,
                    f"unexpected exception: {type(exc).__name__}: {exc}\n"
                    + traceback.format_exc(),
                )
            )

    print()
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  [{marker}] {r.name}: {r.detail}")

    failures = [r for r in results if r.status == "FAIL"]
    print()
    print(f"Summary: {len(results) - len(failures)} pass, {len(failures)} fail")

    if failures:
        print("\nFAILED")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
