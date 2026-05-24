"""Wiring / import tests for the Phase B follow-up FX tools.

Three tools covered:
  - get_fx_returns_series
  - calculate_fx_drawdown
  - get_fx_realized_vol

Tests run without a DB connection and verify the plumbing for each:
  - Schemas import and instantiate with defaults / required fields.
  - Tool config.yaml loads via shared.config.load_tool_config and
    exposes the documented convention keys.
  - The FX MCP server registers the *_tool wrapper.
  - The FX manifest YAML enumerates the tool as 'built' with paths
    that resolve to real files on disk.
  - The FastAPI cards router exposes the matching GET endpoint
    (/returns-series, /drawdown, /realized-vol).
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

from fx_agent.spot.tools.drawdown import (  # noqa: E402
    CONFIG_PATH as DRAWDOWN_CONFIG_PATH,
    FXDrawdownInput,
)
from fx_agent.spot.tools.realized_vol import (  # noqa: E402
    CONFIG_PATH as REALIZED_VOL_CONFIG_PATH,
    FXRealizedVolInput,
)
from fx_agent.spot.tools.returns_series import (  # noqa: E402
    CONFIG_PATH as RETURNS_CONFIG_PATH,
    FXReturnsSeriesInput,
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


# ============================================================================
# Per-tool spec table — drives all 3 sets of identical checks.
# ============================================================================

_TOOLS = [
    {
        "label": "get_fx_returns_series",
        "schema_cls": FXReturnsSeriesInput,
        "config_path": RETURNS_CONFIG_PATH,
        "required_convention_keys": (
            "default_field_name",
            "ffill_limit_days",
            "horizon_daily_periods",
            "horizon_weekly_periods",
            "horizon_monthly_periods",
            "return_round_decimals",
        ),
        "default_input_kwargs": {"pair": "EURUSD"},
        "mcp_wrapper": "get_fx_returns_series_tool",
        "manifest_name": "get_fx_returns_series",
        "api_route_path": "/returns-series",
    },
    {
        "label": "calculate_fx_drawdown",
        "schema_cls": FXDrawdownInput,
        "config_path": DRAWDOWN_CONFIG_PATH,
        "required_convention_keys": (
            "default_field_name",
            "ffill_limit_days",
            "drawdown_round_decimals",
            "price_round_decimals",
        ),
        "default_input_kwargs": {"pair": "EURUSD"},
        "mcp_wrapper": "calculate_fx_drawdown_tool",
        "manifest_name": "calculate_fx_drawdown",
        "api_route_path": "/drawdown",
    },
    {
        "label": "get_fx_realized_vol",
        "schema_cls": FXRealizedVolInput,
        "config_path": REALIZED_VOL_CONFIG_PATH,
        "required_convention_keys": (
            "default_field_name",
            "ffill_limit_days",
            "annualization_trading_days_per_year",
            "std_ddof",
            "vol_round_decimals",
        ),
        "default_input_kwargs": {"pair": "EURUSD"},
        "mcp_wrapper": "get_fx_realized_vol_tool",
        "manifest_name": "get_fx_realized_vol",
        "api_route_path": "/realized-vol",
    },
]


# ============================================================================
# Per-tool checks
# ============================================================================


def check_schema(spec: dict) -> CheckResult:
    name = f"{spec['label']} — schema instantiate"
    try:
        inp = spec["schema_cls"](**spec["default_input_kwargs"])
    except Exception as exc:
        return _fail(name, repr(exc))
    if inp.pair != spec["default_input_kwargs"]["pair"]:
        return _fail(name, f"pair echo: {inp.pair!r}")
    return _pass(name, "default-arg construction OK")


def check_config(spec: dict) -> CheckResult:
    name = f"{spec['label']} — config.yaml loads"
    try:
        cfg = load_tool_config(spec["config_path"])
    except Exception as exc:
        return _fail(name, f"load failed: {exc!r}")
    missing: list[str] = []
    for k in spec["required_convention_keys"]:
        try:
            cfg.convention_value(k)
        except Exception:
            missing.append(k)
    if missing:
        return _fail(name, f"missing convention keys: {missing}")
    return _pass(name, f"all {len(spec['required_convention_keys'])} convention keys present")


def check_mcp(spec: dict) -> CheckResult:
    name = f"{spec['label']} — MCP registration"
    try:
        import fx_agent.mcp_server as mcp_module
    except Exception as exc:
        return _fail(name, f"module import: {exc!r}")
    if not hasattr(mcp_module, spec["mcp_wrapper"]):
        return _fail(
            name,
            f"fx_agent.mcp_server has no attribute {spec['mcp_wrapper']!r}",
        )
    mcp = getattr(mcp_module, "mcp", None)
    if mcp is None:
        return _fail(name, "no `mcp` instance on the module")
    try:
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    except Exception as exc:
        return _fail(name, f"could not enumerate FastMCP tools: {exc!r}")
    if spec["mcp_wrapper"] not in tool_names:
        return _fail(
            name,
            f"{spec['mcp_wrapper']!r} not in FastMCP registry; got: {tool_names}",
        )
    return _pass(name, f"{spec['mcp_wrapper']} registered ({len(tool_names)} tools total)")


def check_manifest(spec: dict) -> CheckResult:
    name = f"{spec['label']} — manifest entry"
    manifest_path = (
        PROJECT_ROOT
        / "manifesto"
        / "03_tool_manifest"
        / "fx_agent"
        / "01_fx_manifest.yml"
    )
    if not manifest_path.exists():
        return _fail(name, f"manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as h:
        manifest = yaml.safe_load(h) or {}
    tools = manifest.get("tools", []) or []
    entry = next(
        (t for t in tools if t.get("name") == spec["manifest_name"]),
        None,
    )
    if entry is None:
        return _fail(name, f"no entry named {spec['manifest_name']!r}")
    if entry.get("status") != "built":
        return _fail(name, f"status={entry.get('status')!r}, expected 'built'")
    impl = entry.get("implementation", {}) or {}
    if impl.get("tool_function") != spec["mcp_wrapper"]:
        return _fail(name, f"tool_function={impl.get('tool_function')!r}")
    paths_to_check = [
        impl.get("mcp_server"),
        (impl.get("schema") or "").split("::")[0],
        (impl.get("compute") or "").split("::")[0],
        impl.get("config"),
    ]
    missing = [p for p in paths_to_check if p and not (PROJECT_ROOT / p).exists()]
    if missing:
        return _fail(name, f"impl files missing on disk: {missing}")
    return _pass(name, "built + tool_function + 4 impl paths resolve")


def check_api_route(spec: dict) -> CheckResult:
    name = f"{spec['label']} — API route"
    try:
        from api.routes.fx.cards import router as fx_cards_router
    except Exception as exc:
        return _fail(name, f"router import failed: {exc!r}")
    matching = [
        r for r in fx_cards_router.routes
        if getattr(r, "path", "") == spec["api_route_path"]
        and "GET" in getattr(r, "methods", set())
    ]
    if not matching:
        all_paths = [(r.path, list(getattr(r, "methods", {})))
                     for r in fx_cards_router.routes]
        return _fail(
            name,
            f"GET {spec['api_route_path']} not on FX cards router. "
            f"Current routes: {all_paths}",
        )
    return _pass(name, f"GET {spec['api_route_path']} registered")


def main() -> int:
    checks: list[CheckResult] = []
    for spec in _TOOLS:
        checks.append(check_schema(spec))
        checks.append(check_config(spec))
        checks.append(check_mcp(spec))
        checks.append(check_manifest(spec))
        checks.append(check_api_route(spec))

    pass_count = sum(1 for c in checks if c.status == "PASS")
    fail_count = sum(1 for c in checks if c.status == "FAIL")
    print("=" * 78)
    print(f"FX FOLLOW-UP WIRING TEST — {pass_count} PASS / {fail_count} FAIL")
    print("=" * 78)
    for c in checks:
        print(f"  [{c.status}] {c.name}: {c.detail}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
