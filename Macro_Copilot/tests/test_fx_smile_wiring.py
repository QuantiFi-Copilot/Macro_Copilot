"""Wiring tests for Phase E2 FX smile tools.

Verifies MCP registration + manifest entry + API route for each of:
  - get_fx_risk_reversal
  - get_fx_butterfly
  - get_fx_vol_smile

Mirror of tests/test_fx_vol_atm_wiring.py.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


_MANIFEST_PATH = PROJECT_ROOT / "manifesto" / "03_tool_manifest" / "fx_agent" / "01_fx_manifest.yml"


def _load_manifest() -> dict:
    with _MANIFEST_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_tool(manifest: dict, name: str) -> dict:
    for t in manifest["tools"]:
        if t["name"] == name:
            return t
    raise KeyError(f"tool {name!r} not in manifest")


def _resolve_impl_paths(impl: dict) -> list[Path]:
    paths: list[Path] = []
    for v in impl.values():
        if not isinstance(v, str):
            continue
        file_part = v.split("::", 1)[0]
        if not file_part.endswith((".py", ".yaml", ".yml")):
            continue
        paths.append(PROJECT_ROOT / file_part)
    return paths


def main() -> int:
    results: list[CheckResult] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            results.append(CheckResult(name, "PASS"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    manifest = _load_manifest()

    # ============ MCP registration ============

    def check_mcp_registration():
        if "fx_agent.mcp_server" in sys.modules:
            del sys.modules["fx_agent.mcp_server"]
        mod = importlib.import_module("fx_agent.mcp_server")
        registered = set(mod.mcp._tool_manager._tools.keys())
        for name in (
            "get_fx_risk_reversal_tool",
            "get_fx_butterfly_tool",
            "get_fx_vol_smile_tool",
        ):
            assert name in registered, (
                f"{name} not registered on the FX MCP server. "
                f"Registered tools: {sorted(registered)}"
            )

    check("MCP: 3 smile tools registered on fx-agent server", check_mcp_registration)

    # ============ manifest entries + path resolution ============

    for tool_name in (
        "get_fx_risk_reversal",
        "get_fx_butterfly",
        "get_fx_vol_smile",
    ):
        def check_manifest_entry(_n=tool_name):
            entry = _find_tool(manifest, _n)
            assert entry["status"] == "built", f"{_n}: status not 'built'"
            assert entry["domain"] == "fx", f"{_n}: domain != 'fx'"
            assert entry["sub_agent"] == "vol", f"{_n}: sub_agent != 'vol'"
            impl = entry["implementation"]
            for key in ("mcp_server", "tool_function", "schema", "compute", "config"):
                assert key in impl, f"{_n}: missing impl.{key}"
            for p in _resolve_impl_paths(impl):
                assert p.exists(), f"{_n}: impl path {p} does not exist"

        check(f"manifest entry + paths resolve: {tool_name}", check_manifest_entry)

    # ============ API route registration ============

    def check_api_routes():
        from api.routes.fx.cards import router
        paths = {r.path for r in router.routes}
        for expected in (
            "/risk-reversal",
            "/butterfly",
            "/vol-smile",
        ):
            assert expected in paths, (
                f"FX API router missing route {expected!r}. "
                f"Registered: {sorted(paths)}"
            )

    check("API: 3 smile GET routes registered on /fx cards router", check_api_routes)

    # ============ report ============
    print("=" * 72)
    print(f"FX SMILE WIRING TEST — {sum(1 for r in results if r.status == 'PASS')} PASS / {sum(1 for r in results if r.status == 'FAIL')} FAIL")
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
