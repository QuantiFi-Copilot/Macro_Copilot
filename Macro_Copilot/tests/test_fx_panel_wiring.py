"""Wiring / import tests for the FX panel tool (Phase B).

These tests run without a DB connection and verify the contractual
plumbing for the new cross-sectional Panel primitive:

- Schema FXPanelInput / FXPanelOutput import and instantiate.
- The tool's config.yaml loads cleanly via
  ``shared.config.load_tool_config`` and exposes every documented
  convention key.
- The FX MCP server registers ``calculate_fx_panel_tool`` under the
  expected name.
- The FX manifest YAML enumerates ``calculate_fx_panel`` as ``built``
  with pointers to the right MCP function name and compute symbols
  (file paths resolve to actual files).
- The API route ``POST /panel`` is registered on the FX cards router.

If any of these fails, the primitive is reachable via Python imports
but won't surface through the Copilot stack — the orphan-compute-file
failure mode Codex flagged at the end of Phase A.

Standalone runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from fx_agent.spot.tools.fx_panel import (  # noqa: E402
    CONFIG_PATH as FX_PANEL_CONFIG_PATH,
    FXPanelInput,
    FXPanelOutput,
    calculate_fx_panel,
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
    """FXPanelInput must accept the minimum (scope + start_date)."""
    try:
        inp = FXPanelInput(market_scope="G10", start_date=date(2025, 5, 22))
    except Exception as exc:
        return _fail("schemas instantiate", repr(exc))
    if inp.market_scope != "G10":
        return _fail("schemas instantiate", f"market_scope echo: {inp.market_scope!r}")
    if inp.field_name != "PX_LAST":
        return _fail("schemas instantiate", f"default field_name: {inp.field_name!r}")
    if inp.missing_data_policy is not None:
        return _fail("schemas instantiate", f"default missing_data_policy: {inp.missing_data_policy!r}")
    return _pass("schemas instantiate", "FXPanelInput default fields OK")


def test_config_loads() -> CheckResult:
    """Tool config.yaml must load and expose every documented convention."""
    try:
        cfg = load_tool_config(FX_PANEL_CONFIG_PATH)
    except Exception as exc:
        return _fail("config loads", f"load_tool_config crashed: {exc!r}")
    required_keys = (
        "default_field_name",
        "ffill_limit_days",
        "default_missing_data_policy",
        "calendar_policy",
    )
    missing: list[str] = []
    for key in required_keys:
        try:
            cfg.convention_value(key)
        except Exception:
            missing.append(key)
    if missing:
        return _fail("config loads", f"missing convention keys: {missing}")
    # Sanity: defaults match what the smoke test relied on
    if cfg.convention_value("default_field_name") != "PX_LAST":
        return _fail("config loads", "default_field_name != PX_LAST")
    if int(cfg.convention_value("ffill_limit_days")) != 5:
        return _fail("config loads", "ffill_limit_days != 5")
    return _pass("config loads", "all 4 convention keys present + defaults OK")


def test_mcp_registration() -> CheckResult:
    """The FX MCP server module must define ``calculate_fx_panel_tool``."""
    try:
        import fx_agent.mcp_server as mcp_module
    except Exception as exc:
        return _fail("MCP module import", repr(exc))
    if not hasattr(mcp_module, "calculate_fx_panel_tool"):
        return _fail(
            "MCP registration",
            "fx_agent.mcp_server has no attribute calculate_fx_panel_tool",
        )
    # Also confirm the FastMCP instance has the tool registered.
    # FastMCP keeps tools on its ``._tool_manager._tools`` dict in v1.x.
    mcp = getattr(mcp_module, "mcp", None)
    if mcp is None:
        return _fail("MCP registration", "no `mcp` instance on the module")
    # Try the public registry, fall back to private if FastMCP changes shape.
    try:
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    except Exception as exc:
        return _fail("MCP registration", f"could not enumerate FastMCP tools: {exc!r}")
    if "calculate_fx_panel_tool" not in tool_names:
        return _fail(
            "MCP registration",
            f"calculate_fx_panel_tool not in FastMCP registry; got: {tool_names}",
        )
    return _pass("MCP registration", f"calculate_fx_panel_tool registered ({len(tool_names)} tools total)")


def test_manifest_entry() -> CheckResult:
    """Manifest must include calculate_fx_panel with resolvable paths."""
    manifest_path = (
        PROJECT_ROOT
        / "manifesto"
        / "03_tool_manifest"
        / "fx_agent"
        / "01_fx_manifest.yml"
    )
    if not manifest_path.exists():
        return _fail("manifest entry", f"manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle) or {}
    tools = manifest.get("tools", []) or []
    panel_entry = next(
        (t for t in tools if t.get("name") == "calculate_fx_panel"),
        None,
    )
    if panel_entry is None:
        return _fail("manifest entry", "no entry named calculate_fx_panel")
    if panel_entry.get("status") != "built":
        return _fail(
            "manifest entry",
            f"status is {panel_entry.get('status')!r}, expected 'built'",
        )
    impl = panel_entry.get("implementation", {}) or {}
    if impl.get("tool_function") != "calculate_fx_panel_tool":
        return _fail(
            "manifest entry",
            f"tool_function: {impl.get('tool_function')!r}",
        )
    # Verify the four file pointers actually exist on disk.
    relative_paths = [
        impl.get("mcp_server"),
        # schema and compute use the file::Symbol syntax — split on '::'
        (impl.get("schema") or "").split("::")[0],
        (impl.get("compute") or "").split("::")[0],
        impl.get("config"),
    ]
    missing: list[str] = []
    for rel in relative_paths:
        if not rel:
            continue
        full = PROJECT_ROOT / rel
        if not full.exists():
            missing.append(rel)
    if missing:
        return _fail("manifest entry", f"impl files missing on disk: {missing}")
    return _pass(
        "manifest entry",
        f"built + tool_function + 4 impl paths resolve",
    )


def test_api_route_registered() -> CheckResult:
    """The FastAPI cards router must expose POST /panel."""
    try:
        from api.routes.fx.cards import router as fx_cards_router
    except Exception as exc:
        return _fail("API route", f"router import failed: {exc!r}")
    panel_routes = [
        r for r in fx_cards_router.routes
        if getattr(r, "path", "") == "/panel"
        and "POST" in getattr(r, "methods", set())
    ]
    if not panel_routes:
        all_paths = [(r.path, list(getattr(r, "methods", {})))
                     for r in fx_cards_router.routes]
        return _fail(
            "API route",
            f"POST /panel not on FX cards router. Current routes: {all_paths}",
        )
    return _pass("API route", f"POST /panel registered on FX cards router")


def main() -> int:
    checks = [
        test_schemas_instantiate(),
        test_config_loads(),
        test_mcp_registration(),
        test_manifest_entry(),
        test_api_route_registered(),
    ]
    pass_count = sum(1 for c in checks if c.status == "PASS")
    fail_count = sum(1 for c in checks if c.status == "FAIL")
    print("=" * 72)
    print(f"FX PANEL WIRING TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 72)
    for c in checks:
        print(f"  [{c.status}] {c.name}: {c.detail}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
