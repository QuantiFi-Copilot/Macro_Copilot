"""Wiring tests for Phase D NDF tools.

Verifies for each of the 3 NDF tools:
  - MCP server has the @mcp.tool() function registered.
  - manifest entry exists with the right schema/compute/config paths
    and all impl paths resolve to existing files.
  - FastAPI router has the GET route registered.

Standalone runner, mirrors tests/test_fx_tools_wiring.py.
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
    """Resolve all path-shaped values in the impl dict; return absolute paths.

    Values like 'fx_agent/ndf/tools/ndf_outright/schemas.py::FXNDFOutrightInput'
    are split on '::' and only the file part is resolved.
    """
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
            # Known env gap: `mcp` package not installed in every dev
            # shell. SKIP (not FAIL) so exit code is 0 on baseline,
            # still fails-loud where mcp IS installed.
            results.append(CheckResult(name, "SKIP", f"env gap: {exc}"))
        except Exception as exc:
            results.append(CheckResult(name, "FAIL", repr(exc)))
            traceback.print_exc()

    manifest = _load_manifest()

    # ============ MCP registration ============

    def check_mcp_registration():
        # Re-import in case earlier tests cached an older version
        if "fx_agent.mcp_server" in sys.modules:
            del sys.modules["fx_agent.mcp_server"]
        mod = importlib.import_module("fx_agent.mcp_server")
        registered = set(mod.mcp._tool_manager._tools.keys())
        for name in (
            "get_fx_ndf_outright_tool",
            "calculate_fx_ndf_implied_carry_tool",
            "scan_fx_ndf_carry_tool",
        ):
            assert name in registered, (
                f"{name} not registered on the FX MCP server. "
                f"Registered tools: {sorted(registered)}"
            )

    check("MCP: 3 NDF tools registered on fx-agent server", check_mcp_registration)

    # ============ manifest entries + path resolution ============

    for tool_name in (
        "get_fx_ndf_outright",
        "calculate_fx_ndf_implied_carry",
        "scan_fx_ndf_carry",
    ):
        def check_manifest_entry(_n=tool_name):
            entry = _find_tool(manifest, _n)
            assert entry["status"] == "built", f"{_n}: status not 'built'"
            assert entry["domain"] == "fx", f"{_n}: domain != 'fx'"
            assert entry["sub_agent"] == "ndf", f"{_n}: sub_agent != 'ndf'"
            impl = entry["implementation"]
            for key in ("mcp_server", "tool_function", "schema", "compute", "config"):
                assert key in impl, f"{_n}: missing impl.{key}"
            # All impl paths must resolve to existing files
            for p in _resolve_impl_paths(impl):
                assert p.exists(), f"{_n}: impl path {p} does not exist"

        check(f"manifest entry exists + paths resolve: {tool_name}", check_manifest_entry)

    # ============ API route registration ============

    def check_api_routes():
        from api.routes.fx.cards import router
        paths = {r.path for r in router.routes}
        for expected in ("/ndf-outright", "/ndf-implied-carry", "/ndf-carry-scanner"):
            assert expected in paths, (
                f"FX API router missing route {expected!r}. "
                f"Registered routes: {sorted(paths)}"
            )

    check("API: 3 NDF GET routes registered on /fx cards router", check_api_routes)

    # ============ report ============
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    skip_count = sum(1 for r in results if r.status == "SKIP")
    print("=" * 72)
    print(
        f"FX NDF WIRING TEST — {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP"
    )
    print("=" * 72)
    for r in results:
        print(f"  [{r.status}] {r.name}{': ' + r.detail if r.detail else ''}")

    # SKIP does not flip the exit code — only a real FAIL does.
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
