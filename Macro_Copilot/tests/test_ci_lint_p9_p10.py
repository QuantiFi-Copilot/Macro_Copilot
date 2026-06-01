"""tests/test_ci_lint_p9_p10.py — PR-10A F7 CI lint smoke.

Tests for ``scripts/ci_lint_p9_no_rates_imports.py`` and
``scripts/ci_lint_p10_no_card_duplication.py``.  Smoke-tests the
detection logic + CLI behaviour (PASS / FAIL exit codes).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_P9_SCRIPT: Path = _REPO_ROOT / "scripts" / "ci_lint_p9_no_rates_imports.py"
_P10_SCRIPT: Path = _REPO_ROOT / "scripts" / "ci_lint_p10_no_card_duplication.py"


def _load(p: Path):
    spec = importlib.util.spec_from_file_location(p.stem, p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


# ============================================================================
# P9 — no rates_agent imports under shared/operators/
# ============================================================================


class TestP9Lint:
    def test_no_violations_on_live_tree(self):
        """Live shared/operators/ tree must be clean."""
        mod = _load(_P9_SCRIPT)
        root = _REPO_ROOT / "shared" / "operators"
        violations = mod.scan(root)
        assert violations == [], (
            f"P9 violations found on live tree: "
            f"{[(v.file.name, v.line_number, v.snippet) for v in violations]}"
        )

    def test_detects_from_import(self, tmp_path):
        mod = _load(_P9_SCRIPT)
        bad = tmp_path / "offender.py"
        bad.write_text(
            "from rates_agent.sovereign_bonds import something\n"
            "x = 1\n",
            encoding="utf-8",
        )
        violations = mod.scan(tmp_path)
        assert len(violations) == 1
        assert "rates_agent" in violations[0].snippet

    def test_detects_bare_import(self, tmp_path):
        mod = _load(_P9_SCRIPT)
        bad = tmp_path / "offender.py"
        bad.write_text("import rates_agent.workflows\n", encoding="utf-8")
        violations = mod.scan(tmp_path)
        assert len(violations) == 1

    def test_ignores_safe_imports(self, tmp_path):
        mod = _load(_P9_SCRIPT)
        ok = tmp_path / "ok.py"
        ok.write_text(
            "from shared.workflow.registry import OperatorSpec\n"
            "import shared.artifacts\n"
            "# Documentation: see rates_agent for downstream usage\n",
            encoding="utf-8",
        )
        # Comments + non-rates_agent imports → 0 violations.
        violations = mod.scan(tmp_path)
        assert violations == []

    def test_cli_pass_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, str(_P9_SCRIPT)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert "PASS" in proc.stdout

    def test_cli_fail_exit_one_on_synthetic_violation(self, tmp_path):
        bad_dir = tmp_path / "fake_operators"
        bad_dir.mkdir()
        (bad_dir / "fake_op.py").write_text(
            "from rates_agent.sovereign_bonds.tools.x import y\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(_P9_SCRIPT), "--root", str(bad_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 1
        assert "FAIL" in proc.stdout


# ============================================================================
# P10 — no operator card content duplicated as Python string
# ============================================================================


class TestP10Lint:
    def test_no_violations_on_live_tree(self):
        mod = _load(_P10_SCRIPT)
        violations = mod.scan(
            _REPO_ROOT / "shared" / "operators",
            _REPO_ROOT / "shared" / "workflow",
        )
        assert violations == [], (
            f"P10 violations on live tree: "
            f"{[(v.operator, v.snippet, v.found_in.name) for v in violations]}"
        )

    def test_detects_card_text_duplicated_in_python(self, tmp_path):
        mod = _load(_P10_SCRIPT)
        # Build a synthetic operator dir with a YAML + a Python source
        # that duplicates the one_line text.
        op_root = tmp_path / "ops"
        wf_root = tmp_path / "wf"
        op_root.mkdir()
        wf_root.mkdir()

        op = op_root / "synthetic"
        op.mkdir()
        (op / "config.yaml").write_text(
            "card:\n"
            "  one_line: >-\n"
            "    Distinctive_snippet_that_must_not_appear_as_python_string"
            " and is over sixty chars long for the lint floor.\n"
            "  when_to_use:\n"
            "    - placeholder\n"
            "  when_not_to_use:\n"
            "    - placeholder\n",
            encoding="utf-8",
        )
        # Duplicate the one_line content in a Python file.
        (op / "operator.py").write_text(
            'DOC = "Distinctive_snippet_that_must_not_appear_as_python_string'
            ' and is over sixty chars long for the lint floor."\n',
            encoding="utf-8",
        )
        violations = mod.scan(op_root, wf_root)
        assert any(
            v.operator == "synthetic" for v in violations
        ), (
            f"P10 lint missed the duplication; violations={violations!r}"
        )

    def test_short_snippets_are_not_flagged(self, tmp_path):
        # Strings shorter than MIN_SNIPPET_LEN are ignored to avoid
        # false positives on legitimate substrate vocabulary.
        mod = _load(_P10_SCRIPT)
        op_root = tmp_path / "ops"
        wf_root = tmp_path / "wf"
        op_root.mkdir()
        wf_root.mkdir()
        op = op_root / "tiny"
        op.mkdir()
        (op / "config.yaml").write_text(
            "card:\n"
            "  one_line: short_card\n"
            "  when_to_use:\n"
            "    - small bullet\n",
            encoding="utf-8",
        )
        (op / "operator.py").write_text('DOC = "short_card"\n', encoding="utf-8")
        violations = mod.scan(op_root, wf_root)
        assert violations == []

    def test_cli_pass_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, str(_P10_SCRIPT)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert "PASS" in proc.stdout


# ============================================================================
# GitHub Actions workflow file present
# ============================================================================


class TestCIWorkflowYAML:
    def test_workflow_yaml_exists(self):
        wf = _REPO_ROOT / ".github" / "workflows" / "open_dag_lints.yml"
        assert wf.is_file(), (
            "PR-10A Codex F7: .github/workflows/open_dag_lints.yml must "
            "exist to wire the three CI lints into CI"
        )

    def test_workflow_yaml_includes_all_three_lints(self):
        wf = _REPO_ROOT / ".github" / "workflows" / "open_dag_lints.yml"
        text = wf.read_text(encoding="utf-8")
        # All three lint commands referenced.
        assert "ci_lint_domain_tool_count.py" in text
        assert "ci_lint_p9_no_rates_imports.py" in text
        assert "ci_lint_p10_no_card_duplication.py" in text
        # Triggered on push + pull_request.
        assert "pull_request" in text
        assert "push" in text
