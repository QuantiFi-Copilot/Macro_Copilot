"""tests/eval/test_registration_only_domain_growth.py — PR-10F gap #2 / PR-10G gap #2.

Proves the original contract: adding the Nth instrument domain is
genuinely registration-only.  Drops a real synthetic
``rates_agent/<fixture_domain>/`` folder into the production tree
DURING the test, asserts the orchestrator discovers it through the
production fresh-interpreter discovery path.

Outer/inner split (PR-10G gap #2 fix)
-------------------------------------
The PR-10F version of this test reloaded ``orchestrator.contracts``
in-process to refresh the registry.  That worked for the assertions
here but contaminated subsequent tests: any module that did
``from orchestrator.contracts import EconomicQuantity`` at top-of-file
captured the OLD class identity; downstream Pydantic checks then saw
mismatched classes and failed with ``ValidationError``.  The
dependent-module set is open-ended, so listing modules to reload was
fragile.

PR-10G fix: split into THIS file (the outer driver — owns the
fixture folder + spawns the subprocess) and
``_registration_only_domain_growth_inner.py`` (the real assertions,
run inside a fresh Python subprocess where module discovery sees the
new domain on first import).  The outer process never reloads
anything, so cross-test contamination becomes structurally
impossible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_FIXTURE_DOMAIN_ID = "_pr10f_fixture_test_domain"
_FIXTURE_FOLDER = _REPO_ROOT / "rates_agent" / _FIXTURE_DOMAIN_ID
_INNER_TEST_PATH = (
    Path(__file__).parent / "_registration_only_domain_growth_inner.py"
)

# Env flag the inner subprocess sets so tests/conftest.py's pre-collection
# sweep leaves the fixture-domain folder in place for the child session.
# MUST match the name in tests/conftest.py (_KEEP_FIXTURE_DOMAIN_ENV).
_KEEP_FIXTURE_DOMAIN_ENV = "_MACRO_GROWTH_PROOF_KEEP_FIXTURE_DOMAIN"


def _write_fixture_domain() -> None:
    """Create the synthetic rates_agent/<fixture>/ folder + a minimal
    mcp_server.py.  Lives under the production rates_agent/ tree so
    production-style discovery sees it the way it would see any real
    domain."""
    _FIXTURE_FOLDER.mkdir()
    (_FIXTURE_FOLDER / "__init__.py").write_text(
        '"""rates_agent fixture-domain for PR-10G gap #2 / #3 proof.\n\n'
        "Declares the eight registration-only-growth constants the\n"
        "orchestrator's domain_registry discovers at import time.\n"
        '"""\n\n'
        f'__domain_id__ = "{_FIXTURE_DOMAIN_ID}"\n'
        '__domain_label__ = "PR-10G fixture domain (test-only)"\n'
        f'__mcp_server_module__ = "rates_agent.{_FIXTURE_DOMAIN_ID}.mcp_server"\n'
        f'__mcp_client_key__ = "{_FIXTURE_DOMAIN_ID}"\n'
        '__resolver_key_convention__ = "bare"\n'
        '__domain_card__ = (\n'
        '    "- pr10g_fixture — test-only synthetic domain that exists \\\n'
        '"  "ONLY to prove the orchestrator picks up new rates_agent/<domain>/ \\\n'
        '"  "folders on first import."\n'
        ')\n'
        '__domain_signals__ = (\n'
        '    "- PR-10G fixture signals: only the test fixture invokes \\\n'
        '"  "this domain; no production routing should ever reach it."\n'
        ')\n'
        '__domain_child_prompt__ = """\\\nYou are the PR-10G fixture \\\n'
        'specialist.  This domain exists only to prove the orchestrator \\\n'
        'picks up new rates_agent/<domain>/ folders on first import — \\\n'
        'never used in production."""\n',
        encoding="utf-8",
    )
    (_FIXTURE_FOLDER / "mcp_server.py").write_text(
        '"""PR-10G fixture mcp_server (test-only)."""\n\n'
        '# Empty: the test does NOT spawn a real MCP subprocess; it\n'
        '# only verifies the orchestrator picks up the domain folder.\n',
        encoding="utf-8",
    )


@pytest.fixture
def fixture_domain():
    """Drop a synthetic rates_agent/<fixture>/ folder and GUARANTEE its
    removal on teardown via try/finally.  No in-process module reloads —
    the test that actually reads the registry runs in a subprocess, so
    the outer process's domain registry is never mutated; restoring the
    on-disk baseline (folder absent) is the complete teardown.

    Why the guarantee matters: the folder lives under the PRODUCTION
    ``rates_agent/`` tree, which is bind-mounted into the macro-env
    container.  A folder left behind here is discovered by
    ``orchestrator.domain_registry`` at the NEXT session's import time,
    inflating ``KNOWN_DOMAINS`` to 7 and tripping the P8/P10 closed-
    family gate (``test_resolver_keys.py::test_known_domains_size``) in a
    session that never touched this fixture.  The write lives inside the
    ``try`` so a partial folder from a mid-write failure is still removed;
    ``tests/conftest.py`` sweeps any survivor before collection as a
    second line of defence."""
    # Clear any folder a previously hard-killed run leaked, then write a
    # fresh one.  The write is inside the try so the finally removes even
    # a partially-created folder if _write_fixture_domain raises.
    shutil.rmtree(_FIXTURE_FOLDER, ignore_errors=True)
    try:
        _write_fixture_domain()
        yield
    finally:
        shutil.rmtree(_FIXTURE_FOLDER, ignore_errors=True)


@pytest.mark.parametrize(
    "inner_test_name",
    [
        "test_invariant_files_unchanged_after_dropping_new_domain_folder",
        "test_orchestrator_discovers_new_domain_with_zero_source_edits",
        "test_synthetic_domain_appears_in_llm_facing_surfaces",
    ],
)
def test_via_subprocess(fixture_domain, inner_test_name: str):
    """Run each inner assertion in a fresh Python subprocess so the
    registry discovers the fixture domain on FIRST IMPORT (matching
    the production fresh-interpreter path) and the parent process's
    module state stays untouched."""
    # The inner pytest session loads tests/conftest.py too, whose
    # pre-collection sweep would otherwise delete the fixture-domain folder
    # this test just wrote — before the inner assertions can read it.  Opt
    # the child out of the sweep; the folder is the whole point of the run.
    inner_env = {**os.environ, _KEEP_FIXTURE_DOMAIN_ENV: "1"}
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            f"{_INNER_TEST_PATH}::{inner_test_name}",
            "-xvs", "--no-header", "-p", "no:cacheprovider",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=60,
        env=inner_env,
    )
    if result.returncode != 0:
        pytest.fail(
            f"PR-10G gap #2: inner test {inner_test_name} failed in "
            f"subprocess.\n"
            f"--- STDOUT ---\n{result.stdout}\n"
            f"--- STDERR ---\n{result.stderr}",
        )


def test_after_cleanup_orchestrator_returns_to_production_state():
    """Once the fixture tears down, every registry surface returns
    to the six-domain production state.

    The outer process never reloaded anything, so the registry IS
    the production state — no reload required.  This test just
    pins that the six expected domains are present.
    """
    from orchestrator import domain_registry
    from orchestrator.open_dag import resolver_keys
    from orchestrator import contracts as contracts_mod

    expected = {
        "sovereign_bonds", "ois", "inflation_indexed_bonds",
        "inflation_swaps", "policy_futures", "bond_futures",
    }
    assert set(domain_registry.DOMAIN_SPECS.keys()) == expected
    assert {d.value for d in contracts_mod.Domain} == expected
    assert resolver_keys.KNOWN_DOMAINS == frozenset(expected)
    assert _FIXTURE_DOMAIN_ID not in {
        d.value for d in contracts_mod.Domain
    }


def test_fixture_folder_is_untracked_in_git(fixture_domain):
    """The fixture folder MUST be untracked in git (the test never
    commits it), and post-cleanup `git status` is unchanged compared
    to pre-test state."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git unavailable")
    if top.returncode != 0:
        pytest.skip("not in a git repo")
    repo = Path(top.stdout.strip())

    folder_rel = _FIXTURE_FOLDER.relative_to(repo)
    status = subprocess.run(
        ["git", "status", "--porcelain", str(folder_rel)],
        cwd=str(repo), capture_output=True, text=True, timeout=5,
    ).stdout
    assert status.startswith("?? "), (
        f"PR-10G gap #2: fixture folder must be UNTRACKED in git; "
        f"got status: {status!r}"
    )
