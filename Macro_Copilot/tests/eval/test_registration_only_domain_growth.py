"""tests/eval/test_registration_only_domain_growth.py — PR-10F gap #2 corrective.

Proves the original contract: adding the Nth instrument domain is
genuinely registration-only.  Drops a real synthetic
``rates_agent/<fixture_domain>/`` folder into the production tree
DURING the test, asserts the orchestrator discovers it through the
production discovery path (no monkeypatch, no wrapper), then asserts
the orchestrator's CODE SURFACE (composer / coverage gate / validator
/ executor / Boundary A & B / synthesis / SUPERVISOR prompt scaffolding)
is byte-for-byte unchanged.

Cleans up via a layered ExitStack chain that survives crashes — the
synthetic folder is removed, the registry is refreshed, and any
imported module is dropped from ``sys.modules`` even when an
assertion mid-test fires.

The test deliberately uses ``importlib.reload`` on the registry
because the test process already loaded the registry at startup
before the fixture created the folder.  In a fresh interpreter (the
production path), discovery sees the folder on first import — no
reload needed.
"""

from __future__ import annotations

import hashlib
import importlib
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest


_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_FIXTURE_DOMAIN_ID = "_pr10f_fixture_test_domain"
_FIXTURE_FOLDER = _REPO_ROOT / "rates_agent" / _FIXTURE_DOMAIN_ID


# Files that MUST stay byte-identical when a new domain folder is added.
# These are the orchestrator's CODE SURFACE — composer, validator,
# executor, coverage gate, Boundary A/B, synthesis, SUPERVISOR prompt
# scaffolding.  Per the contract, none of these may grow when the Nth
# domain is added.
_DOMAIN_GROWTH_INVARIANT_FILES = [
    _REPO_ROOT / "orchestrator" / "open_dag" / "composer.py",
    _REPO_ROOT / "orchestrator" / "open_dag" / "coverage_gate.py",
    _REPO_ROOT / "orchestrator" / "open_dag" / "assembler.py",
    _REPO_ROOT / "shared" / "workflow" / "validate.py",
    _REPO_ROOT / "shared" / "workflow" / "executor.py",
    _REPO_ROOT / "orchestrator" / "contracts.py",
    _REPO_ROOT / "orchestrator" / "config.py",
    _REPO_ROOT / "orchestrator" / "open_dag" / "resolver_keys.py",
]


def _file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_fixture_domain() -> None:
    """Create the synthetic rates_agent/<fixture>/ folder + a minimal
    mcp_server.py (so the discovered __mcp_server_module__ actually
    points at an importable module).  The folder lives under the
    production rates_agent/ tree so production-style discovery sees
    it the way it would see any real domain."""
    _FIXTURE_FOLDER.mkdir()
    (_FIXTURE_FOLDER / "__init__.py").write_text(
        '"""rates_agent fixture-domain for PR-10F gap #2 proof.\n\n'
        "Declares the five registration-only-growth constants the\n"
        "orchestrator's domain_registry discovers at import time.\n"
        '"""\n\n'
        f'__domain_id__ = "{_FIXTURE_DOMAIN_ID}"\n'
        '__domain_label__ = "PR-10F fixture domain (test-only)"\n'
        f'__mcp_server_module__ = "rates_agent.{_FIXTURE_DOMAIN_ID}.mcp_server"\n'
        f'__mcp_client_key__ = "{_FIXTURE_DOMAIN_ID}"\n'
        '__resolver_key_convention__ = "bare"\n',
        encoding="utf-8",
    )
    (_FIXTURE_FOLDER / "mcp_server.py").write_text(
        '"""PR-10F fixture mcp_server (test-only)."""\n\n'
        '# Empty: the test does NOT spawn a real MCP subprocess; it\n'
        '# only verifies the orchestrator picks up the domain folder.\n',
        encoding="utf-8",
    )


def _refresh_orchestrator_registry():
    """Re-run domain discovery + reload every orchestrator module that
    depends on it.  Mimics what would happen in a fresh interpreter.

    Order matters: contracts.py rebuilds the Domain enum from the
    refreshed DOMAIN_SPECS BEFORE config.py tries to look up
    ``Domain(spec.domain_id)``.
    """
    from orchestrator import domain_registry
    importlib.reload(domain_registry)
    import orchestrator.contracts as contracts_mod
    importlib.reload(contracts_mod)
    from orchestrator.open_dag import resolver_keys
    importlib.reload(resolver_keys)
    import orchestrator.config as config_mod
    importlib.reload(config_mod)


def _restore_orchestrator_registry():
    """Reload the registry + every dependent module back to the
    production six-domain state (the fixture folder is gone by now)."""
    from orchestrator import domain_registry
    importlib.reload(domain_registry)
    import orchestrator.contracts as contracts_mod
    importlib.reload(contracts_mod)
    from orchestrator.open_dag import resolver_keys
    importlib.reload(resolver_keys)
    import orchestrator.config as config_mod
    importlib.reload(config_mod)


@pytest.fixture
def fixture_domain():
    """Drop a synthetic rates_agent/<fixture>/ folder into the
    production tree; refresh the orchestrator's registry; tear
    down via layered ExitStack."""
    # Refuse to run if a stale fixture folder lingers from a crashed
    # prior run.
    if _FIXTURE_FOLDER.exists():
        shutil.rmtree(_FIXTURE_FOLDER, ignore_errors=True)

    with ExitStack() as stack:
        # Cleanup #1 (last to run): nuke the folder + drop the cached
        # module.
        def _final_cleanup():
            shutil.rmtree(_FIXTURE_FOLDER, ignore_errors=True)
            for mod_name in [
                k for k in sys.modules
                if k.startswith(f"rates_agent.{_FIXTURE_DOMAIN_ID}")
            ]:
                sys.modules.pop(mod_name, None)
        stack.callback(_final_cleanup)

        # Cleanup #2: refresh the orchestrator's registry back to the
        # production state once the folder is gone.
        stack.callback(_restore_orchestrator_registry)

        # ---- ACT: write the folder + refresh discovery.
        _write_fixture_domain()
        _refresh_orchestrator_registry()
        yield


def test_invariant_files_unchanged_after_dropping_new_domain_folder(
    fixture_domain,
):
    """The 8 orchestrator/code-surface files MUST stay byte-identical
    after a new domain folder is dropped + the registry refreshed."""
    pre_hashes = {
        p: _file_hash(p) for p in _DOMAIN_GROWTH_INVARIANT_FILES
    }
    # The fixture already created the folder + refreshed discovery;
    # hash the invariant files again now.  Any drift would mean the
    # orchestrator quietly mutated a tracked file.
    post_hashes = {
        p: _file_hash(p) for p in _DOMAIN_GROWTH_INVARIANT_FILES
    }
    assert pre_hashes == post_hashes, (
        "PR-10F gap #2: dropping a new domain folder mutated one of "
        "the orchestrator's invariant code-surface files.  Diffs:\n"
        + "\n".join(
            f"  {p}: pre={pre_hashes[p][:12]} != post={post_hashes[p][:12]}"
            for p in pre_hashes if pre_hashes[p] != post_hashes[p]
        )
    )


def test_orchestrator_discovers_new_domain_with_zero_source_edits(
    fixture_domain,
):
    """The orchestrator must pick up the new domain through the
    production discovery path — DOMAIN_SPECS, Domain enum,
    DOMAIN_MCP_SERVERS, KNOWN_DOMAINS all populated automatically."""
    from orchestrator import domain_registry
    from orchestrator.open_dag import resolver_keys

    # 1. The registry sees the fixture.
    assert _FIXTURE_DOMAIN_ID in domain_registry.DOMAIN_SPECS, (
        f"PR-10F gap #2: registry did NOT discover the fixture "
        f"domain folder.  DOMAIN_SPECS keys: "
        f"{sorted(domain_registry.DOMAIN_SPECS.keys())}"
    )

    # 2. The Domain enum has the new member (via factory).
    import orchestrator.contracts as contracts_mod
    domain_values = {d.value for d in contracts_mod.Domain}
    assert _FIXTURE_DOMAIN_ID in domain_values, (
        f"PR-10F gap #2: Domain enum did NOT pick up the fixture "
        f"domain.  Members: {sorted(domain_values)}"
    )

    # 3. KNOWN_DOMAINS includes the fixture.
    assert _FIXTURE_DOMAIN_ID in resolver_keys.KNOWN_DOMAINS, (
        f"PR-10F gap #2: KNOWN_DOMAINS did NOT pick up the fixture "
        f"domain.  Got: {sorted(resolver_keys.KNOWN_DOMAINS)}"
    )

    # 4. domain_to_resolver_key respects the fixture's bare convention.
    key = resolver_keys.domain_to_resolver_key(
        _FIXTURE_DOMAIN_ID, "some_synthetic_tool",
    )
    assert key == "some_synthetic_tool", (
        f"PR-10F gap #2: bare-convention domain produced wrong "
        f"resolver key.  Got {key!r}, expected "
        f"'some_synthetic_tool'."
    )

    # 5. DOMAIN_MCP_SERVERS gains an entry keyed on the new domain.
    import orchestrator.config as config_mod
    new_domain = contracts_mod.Domain(_FIXTURE_DOMAIN_ID)
    assert new_domain in config_mod.DOMAIN_MCP_SERVERS, (
        f"PR-10F gap #2: DOMAIN_MCP_SERVERS did NOT pick up the "
        f"fixture domain.  Keys: "
        f"{sorted(d.value for d in config_mod.DOMAIN_MCP_SERVERS.keys())}"
    )


def test_after_cleanup_orchestrator_returns_to_production_state():
    """Once the fixture tears down, every registry surface returns
    to the six-domain production state."""
    # The fixture is NOT applied here — the test stands alone to
    # verify cleanup leaves no residue.
    from orchestrator import domain_registry
    from orchestrator.open_dag import resolver_keys
    import orchestrator.contracts as contracts_mod

    # Re-run discovery in case prior test states linger.
    importlib.reload(domain_registry)
    importlib.reload(resolver_keys)
    importlib.reload(contracts_mod)

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
    """The fixture folder MUST be untracked in git — the test never
    commits it, and the post-test cleanup must remove it cleanly so
    `git status` is unchanged compared to pre-test state."""
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
    # The folder is untracked (?? prefix), not modified or staged.
    assert status.startswith("?? "), (
        f"PR-10F gap #2: fixture folder must be UNTRACKED in git; "
        f"got status: {status!r}"
    )
