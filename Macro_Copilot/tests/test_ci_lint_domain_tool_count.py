"""tests/test_ci_lint_domain_tool_count.py — PR-10 CI lint smoke.

Tests for ``scripts/ci_lint_domain_tool_count.py``.  Smoke-tests the
CLI behaviour: PASS / FAIL exit codes + the audit helper.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import List


_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_SCRIPT: Path = _REPO_ROOT / "scripts" / "ci_lint_domain_tool_count.py"


def _load_module():
    """Import the CI script as a module so we can call its functions
    directly (audit / render_report) — separates the testable logic
    from the CLI wrapper."""
    spec = importlib.util.spec_from_file_location(
        "ci_lint_domain_tool_count", _SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


class TestAuditHelper:
    def test_audit_returns_within_and_over(self, tmp_path):
        mod = _load_module()
        # Build a synthetic tree: two "domains", one with 3 tools,
        # one with 30 tools.
        small = tmp_path / "small_domain"
        small.mkdir()
        (small / "mcp_server.py").write_text(
            "\n".join([
                "@mcp.tool()",
                "def a(): pass",
                "@mcp.tool()",
                "def b(): pass",
                "@mcp.tool()",
                "def c(): pass",
            ]),
            encoding="utf-8",
        )
        big = tmp_path / "big_domain"
        big.mkdir()
        (big / "mcp_server.py").write_text(
            "\n".join(["@mcp.tool()\ndef t{}(): pass".format(i) for i in range(30)]),
            encoding="utf-8",
        )
        within, over = mod.audit(tmp_path, limit=5)
        names_over = {r.domain for r in over}
        names_within = {r.domain for r in within}
        assert "big_domain" in names_over
        assert "small_domain" in names_within

    def test_workflows_directory_excluded(self, tmp_path):
        # rates_agent/workflows is the cross-domain harness — not
        # subject to the per-domain cap.
        mod = _load_module()
        workflows = tmp_path / "workflows"
        workflows.mkdir()
        (workflows / "mcp_server.py").write_text(
            "@mcp.tool()\n" * 100,
            encoding="utf-8",
        )
        within, over = mod.audit(tmp_path, limit=5)
        assert not within and not over, (
            "workflows directory must be excluded from the per-domain "
            "audit"
        )

    def test_decorator_regex_tolerates_args(self, tmp_path):
        mod = _load_module()
        d = tmp_path / "domain_with_args"
        d.mkdir()
        (d / "mcp_server.py").write_text(
            "\n".join([
                "@mcp.tool()",
                "def a(): pass",
                "@mcp.tool(name='b')",
                "def b(): pass",
                '@server.tool(name="c")',
                "def c(): pass",
                "@app.tool ()",
                "def d(): pass",
            ]),
            encoding="utf-8",
        )
        within, over = mod.audit(tmp_path, limit=10)
        assert len(within) == 1 and within[0].tool_count == 4


class TestCLIBehaviour:
    def test_cli_pass_exit_zero(self):
        """Run against the live rates_agent/ tree with the default
        limit (25); all six domains are under the cap, exit 0."""
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--limit", "25"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert "PASS" in proc.stdout

    def test_cli_fail_exit_one_with_tight_limit(self):
        """Tight cap → FAIL exit."""
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--limit", "5"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert "FAIL" in proc.stdout
        assert "OVER" in proc.stdout

    def test_cli_reports_every_domain(self):
        """The CLI's report names every audited domain."""
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--limit", "25"],
            capture_output=True,
            text=True,
        )
        # Six rates domains.
        for domain in (
            "sovereign_bonds",
            "ois",
            "inflation_indexed_bonds",
            "inflation_swaps",
            "policy_futures",
            "bond_futures",
        ):
            assert domain in proc.stdout, (
                f"CLI report missing domain {domain!r}"
            )
