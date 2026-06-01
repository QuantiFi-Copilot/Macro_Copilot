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
    # PR-10H gap #2: prompts.py and session.py are part of the
    # registration-only-growth code surface — they render their LLM-
    # facing content from DOMAIN_SPECS at import time, so registering
    # a new domain MUST NOT mutate either file.  Adding them here
    # guarantees that contract.
    _REPO_ROOT / "orchestrator" / "prompts.py",
    _REPO_ROOT / "orchestrator" / "session.py",
]


def _file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_invariant_files_unchanged_after_dropping_new_domain_folder():
    """10 orchestrator code-surface files MUST stay byte-identical."""
    pre_hashes = {p: _file_hash(p) for p in _DOMAIN_GROWTH_INVARIANT_FILES}
    # Importing the orchestrator's registry + the templated prompt /
    # session modules should NOT mutate any tracked file — they only
    # READ the fixture folder at import time.  Per PR-10H gap #2 the
    # prompts/session modules are now part of the invariant set: their
    # import-time rendering from DOMAIN_SPECS must add the new domain
    # without an edit to either file.
    from orchestrator import domain_registry  # noqa: F401
    from orchestrator import contracts as contracts_mod  # noqa: F401
    from orchestrator import config as config_mod  # noqa: F401
    from orchestrator.open_dag import resolver_keys  # noqa: F401
    from orchestrator import prompts as prompts_mod  # noqa: F401
    from orchestrator import session as session_mod  # noqa: F401

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


def test_synthetic_domain_appears_in_llm_facing_surfaces():
    """PR-10H gap #2: the fixture's __domain_card__,
    __domain_signals__, and __domain_child_prompt__ MUST reach all
    three LLM-facing surfaces — otherwise the supervisor / child
    models never see the new domain even though the registry knows
    about it.

    Surfaces asserted:
      (a) orchestrator.prompts.SUPERVISOR_SYSTEM_PROMPT contains
          the fixture's domain_card.
      (b) orchestrator.prompts.SUPERVISOR_SYSTEM_PROMPT contains
          the fixture's domain_signals.
      (c) orchestrator.prompts has the backwards-compat
          ``<FIXTURE_DOMAIN_ID_UPPER>_SYSTEM_PROMPT`` alias and it
          equals the fixture's __domain_child_prompt__.
      (d) orchestrator.session._DOMAIN_PROMPTS[Domain(<id>)]
          equals the fixture's __domain_child_prompt__.
    """
    from orchestrator import prompts as prompts_mod
    from orchestrator import session as session_mod
    from orchestrator.contracts import Domain
    from orchestrator.domain_registry import DOMAIN_SPECS

    spec = DOMAIN_SPECS[_FIXTURE_DOMAIN_ID]

    # Stable substrings of the fixture's parsed strings (Python
    # collapses the inline `\<newline>` continuations).
    _CARD_SUBSTRING = (
        "pr10g_fixture — test-only synthetic domain that exists "
        "ONLY to prove the orchestrator picks up new "
        "rates_agent/<domain>/ folders on first import."
    )
    _SIGNALS_SUBSTRING = (
        "PR-10G fixture signals: only the test fixture invokes "
        "this domain; no production routing should ever reach it."
    )

    # Sanity: fixture's parsed constants MUST contain those
    # substrings — otherwise the fixture itself drifted.
    assert _CARD_SUBSTRING in spec.domain_card, (
        f"PR-10H gap #2: fixture's __domain_card__ no longer "
        f"contains the expected substring.  Got: {spec.domain_card!r}"
    )
    assert _SIGNALS_SUBSTRING in spec.domain_signals, (
        f"PR-10H gap #2: fixture's __domain_signals__ no longer "
        f"contains the expected substring.  Got: "
        f"{spec.domain_signals!r}"
    )

    # (a) Supervisor system prompt carries the domain card.
    assert _CARD_SUBSTRING in prompts_mod.SUPERVISOR_SYSTEM_PROMPT, (
        "PR-10H gap #2 (a): SUPERVISOR_SYSTEM_PROMPT does NOT "
        "contain the fixture domain's __domain_card__ substring. "
        "The supervisor LLM will never see the new domain in its "
        "AVAILABLE DOMAINS block."
    )

    # (b) Supervisor system prompt carries the domain signals.
    assert _SIGNALS_SUBSTRING in prompts_mod.SUPERVISOR_SYSTEM_PROMPT, (
        "PR-10H gap #2 (b): SUPERVISOR_SYSTEM_PROMPT does NOT "
        "contain the fixture domain's __domain_signals__ "
        "substring."
    )

    # (c) Backwards-compat alias exists on prompts module + equals
    # the fixture's __domain_child_prompt__.
    alias_name = f"{_FIXTURE_DOMAIN_ID.upper()}_SYSTEM_PROMPT"
    assert hasattr(prompts_mod, alias_name), (
        f"PR-10H gap #2 (c): orchestrator.prompts is missing the "
        f"backwards-compat alias {alias_name!r}.  Existing "
        f"`from orchestrator.prompts import {alias_name}` imports "
        f"would fail for the newly-registered domain."
    )
    assert getattr(prompts_mod, alias_name) == spec.child_system_prompt, (
        f"PR-10H gap #2 (c): {alias_name} on orchestrator.prompts "
        f"does NOT equal the fixture's __domain_child_prompt__."
    )

    # (d) session._DOMAIN_PROMPTS keyed by the Domain enum member.
    new_domain = Domain(_FIXTURE_DOMAIN_ID)
    assert new_domain in session_mod._DOMAIN_PROMPTS, (
        f"PR-10H gap #2 (d): orchestrator.session._DOMAIN_PROMPTS "
        f"is missing an entry for Domain({_FIXTURE_DOMAIN_ID!r}).  "
        f"Keys: "
        f"{sorted(d.value for d in session_mod._DOMAIN_PROMPTS.keys())}"
    )
    assert session_mod._DOMAIN_PROMPTS[new_domain] == spec.child_system_prompt, (
        f"PR-10H gap #2 (d): _DOMAIN_PROMPTS[Domain({_FIXTURE_DOMAIN_ID!r})] "
        f"does NOT equal the fixture's __domain_child_prompt__."
    )
