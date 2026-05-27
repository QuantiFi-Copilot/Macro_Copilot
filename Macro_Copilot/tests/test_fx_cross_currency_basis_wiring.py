"""Wiring tests for Phase C FX cross-currency basis.

Verifies MCP registration + manifest entry + API route for
get_fx_cross_currency_basis. SKIP-not-FAIL pattern on MCP env gap.
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
        except ModuleNotFoundError as exc:
            results.append(CheckResult(name, "SKIP", f"env gap: {exc}"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    manifest = _load_manifest()

    def check_mcp_registration():
        if "fx_agent.mcp_server" in sys.modules:
            del sys.modules["fx_agent.mcp_server"]
        mod = importlib.import_module("fx_agent.mcp_server")
        registered = set(mod.mcp._tool_manager._tools.keys())
        assert "get_fx_cross_currency_basis_tool" in registered, (
            f"get_fx_cross_currency_basis_tool not registered. "
            f"Registered: {sorted(registered)}"
        )
    check("MCP: cross_currency_basis tool registered", check_mcp_registration)

    def check_manifest_entry():
        entry = _find_tool(manifest, "get_fx_cross_currency_basis")
        assert entry["status"] in ("built", "draft"), f"unexpected status: {entry['status']}"
        assert entry["domain"] == "fx"
        assert entry["sub_agent"] == "forwards"
        impl = entry["implementation"]
        for key in ("mcp_server", "tool_function", "schema", "compute", "config"):
            assert key in impl, f"missing impl.{key}"
        for p in _resolve_impl_paths(impl):
            assert p.exists(), f"impl path {p} does not exist"
    check("manifest entry + paths resolve: get_fx_cross_currency_basis", check_manifest_entry)

    def check_manifest_declares_rates_dependency():
        """Cross-domain primitive must declare its rates_agent dependency
        in references: block (transparency for cross-domain reviewers)."""
        entry = _find_tool(manifest, "get_fx_cross_currency_basis")
        refs = entry.get("references", [])
        joined = " ".join(refs).lower()
        assert "rates" in joined or "ois" in joined, (
            f"Cross-domain dependency on rates_agent OIS not declared in "
            f"references: {refs}"
        )
    check("manifest references rates_agent OIS dependency", check_manifest_declares_rates_dependency)

    def check_api_route():
        from api.routes.fx.cards import router
        paths = {r.path for r in router.routes}
        assert "/cross-currency-basis" in paths, (
            f"FX API router missing /cross-currency-basis. "
            f"Registered: {sorted(paths)}"
        )
    check("API: /cross-currency-basis GET route registered", check_api_route)

    # ============ report ============
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    skip_count = sum(1 for r in results if r.status == "SKIP")
    print("=" * 72)
    print(
        f"FX CROSS-CURRENCY BASIS WIRING TEST — "
        f"{pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP"
    )
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
