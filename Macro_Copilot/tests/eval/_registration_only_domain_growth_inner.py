"""tests/eval/_registration_only_domain_growth_inner.py — PR-10G gap #2 inner.

The REAL assertions for the registration-only-domain-growth proof.  Run
ONLY inside the subprocess spawned by
``test_registration_only_domain_growth.py``'s outer driver.

Why a separate module?
----------------------
The outer test writes a synthetic ``rates_agent/<fixture>/`` folder to
disk BEFORE spawning this subprocess.  By the time this module's first
``import orchestrator.contracts`` runs, the registry's auto-discovery
sees the new domain folder on the FIRST PASS — matching the production
fresh-interpreter discovery path the contract guarantees.

Why NOT use ``importlib.reload`` in the outer process?
------------------------------------------------------
``importlib.reload(orchestrator.contracts)`` rebuilds the ``Domain``
enum and the ``EconomicQuantity`` Pydantic model with FRESH class
identities.  But any module that did
``from orchestrator.contracts import EconomicQuantity`` at top-of-file
(e.g. ``orchestrator/open_dag/intent_chain.py``) captured the OLD class
into its own namespace.  When a downstream test in the same process
later constructs an ``EconomicQuantity`` instance via the RELOADED
contracts module and feeds it into a Pydantic model imported from
``intent_chain``, Pydantic sees the class-identity mismatch and raises
``ValidationError``.  The dependent-module set is open-ended, so any
fix that lists modules to reload is fragile — a future ``from
orchestrator.contracts import ...`` introduces the contamination
silently.

Subprocess isolation kills the contamination structurally: the parent
process never reloads anything, and this subprocess uses a fresh
Python interpreter where the discovery sees the fixture folder on
first import.

Pytest auto-collection
----------------------
This file's underscore prefix means pytest's
``python_files = test_*.py`` discovery rule skips it on a normal
``pytest`` run.  The outer driver invokes it explicitly via
``pytest tests/eval/_registration_only_domain_growth_inner.py::<name>``,
which bypasses the auto-discovery filter.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_FIXTURE_DOMAIN_ID = "_pr10f_fixture_test_domain"


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


def test_invariant_files_unchanged_after_dropping_new_domain_folder():
    """8 orchestrator code-surface files MUST stay byte-identical."""
    pre_hashes = {p: _file_hash(p) for p in _DOMAIN_GROWTH_INVARIANT_FILES}
    # Importing the orchestrator's registry should NOT mutate any
    # tracked file — it only reads the fixture folder.
    from orchestrator import domain_registry  # noqa: F401
    from orchestrator import contracts as contracts_mod  # noqa: F401
    from orchestrator import config as config_mod  # noqa: F401
    from orchestrator.open_dag import resolver_keys  # noqa: F401

    post_hashes = {p: _file_hash(p) for p in _DOMAIN_GROWTH_INVARIANT_FILES}
    assert pre_hashes == post_hashes, (
        "PR-10G gap #2: dropping a new domain folder mutated one of "
        "the orchestrator's invariant code-surface files.  Diffs:\n"
        + "\n".join(
            f"  {p}: pre={pre_hashes[p][:12]} != post={post_hashes[p][:12]}"
            for p in pre_hashes if pre_hashes[p] != post_hashes[p]
        )
    )


def test_orchestrator_discovers_new_domain_with_zero_source_edits():
    """The orchestrator picks up the new domain through the production
    fresh-interpreter discovery path."""
    from orchestrator import domain_registry
    from orchestrator.open_dag import resolver_keys
    from orchestrator import contracts as contracts_mod
    from orchestrator import config as config_mod

    # 1. Registry sees the fixture.
    assert _FIXTURE_DOMAIN_ID in domain_registry.DOMAIN_SPECS, (
        f"PR-10G gap #2: registry did NOT discover the fixture "
        f"domain folder on first-import.  DOMAIN_SPECS keys: "
        f"{sorted(domain_registry.DOMAIN_SPECS.keys())}"
    )

    # 2. Domain enum has the new member (built via factory).
    domain_values = {d.value for d in contracts_mod.Domain}
    assert _FIXTURE_DOMAIN_ID in domain_values, (
        f"PR-10G gap #2: Domain enum did NOT pick up the fixture "
        f"domain.  Members: {sorted(domain_values)}"
    )

    # 3. KNOWN_DOMAINS includes the fixture.
    assert _FIXTURE_DOMAIN_ID in resolver_keys.KNOWN_DOMAINS, (
        f"PR-10G gap #2: KNOWN_DOMAINS did NOT pick up the fixture "
        f"domain.  Got: {sorted(resolver_keys.KNOWN_DOMAINS)}"
    )

    # 4. domain_to_resolver_key respects the bare convention.
    key = resolver_keys.domain_to_resolver_key(
        _FIXTURE_DOMAIN_ID, "some_synthetic_tool",
    )
    assert key == "some_synthetic_tool", (
        f"PR-10G gap #2: bare-convention domain produced wrong "
        f"resolver key.  Got {key!r}, expected 'some_synthetic_tool'."
    )

    # 5. DOMAIN_MCP_SERVERS gains an entry keyed on the new domain.
    new_domain = contracts_mod.Domain(_FIXTURE_DOMAIN_ID)
    assert new_domain in config_mod.DOMAIN_MCP_SERVERS, (
        f"PR-10G gap #2: DOMAIN_MCP_SERVERS did NOT pick up the "
        f"fixture domain.  Keys: "
        f"{sorted(d.value for d in config_mod.DOMAIN_MCP_SERVERS.keys())}"
    )
