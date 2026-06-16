"""
tests/conftest.py — pytest configuration
==========================================

Two responsibilities:

1.  Project-root ``sys.path`` setup so test modules can import
    ``rates_agent``, ``shared``, ``orchestrator`` etc. without each
    file manipulating ``sys.path`` itself.

2.  Excluding the pre-existing CLI smoke-test scripts from collection.
    Those files (``test_ws_chat.py``, the ``*_sql_validation.py``
    runners) are standalone scripts with their own
    ``if __name__ == "__main__":`` entry points and argparse
    surfaces; they aren't pytest tests and shouldn't be collected.

    Some are still imported by the multi-agent gauntlet
    (``tests/test_multi_agent_prompt_gauntlet.py``) as helper
    modules — that import path is fine because pytest collects the
    gauntlet itself, not its helpers.

Historical note: an earlier version of this list included a set of
``test_*_direct.py`` files that pre-dated pytest adoption and had
stale imports.  Those files have since been deleted from the tree
or replaced by ``*_sql_validation.py`` equivalents; the ignore
list below is now narrowed to the entries that genuinely exist.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Pre-collection sweep of the registration-only-domain-growth fixture folder
# ---------------------------------------------------------------------------
#
# ``tests/eval/test_registration_only_domain_growth.py`` drops a synthetic
# ``rates_agent/_pr10f_fixture_test_domain/`` folder into the PRODUCTION
# tree during its run and removes it on teardown.  Because the test tree is
# bind-mounted into the macro-env container (``.:/app``), a folder that
# survives a hard-killed run (SIGKILL / CI timeout) lingers on disk and
# poisons EVERY later pytest session: ``orchestrator.domain_registry``
# discovers it at import time, ``KNOWN_DOMAINS`` grows to 7, and the P8/P10
# closed-family gate
# (``tests/orchestrator/open_dag/test_resolver_keys.py::
# TestKnownDomainsClosedFamily::test_known_domains_size``) fails with a leak
# it never caused — even in sessions that don't run the growth test at all.
#
# The registry is discovered at *collection* time (when test modules import
# it), which is AFTER ``pytest_configure`` but BEFORE any fixture runs.  So
# the only place a sweep can restore the 6-domain baseline for the current
# session is a pre-collection hook here, not a fixture.  This is the second
# line of defence; the growth fixture's own try/finally teardown is the
# first.
_LEAKABLE_FIXTURE_DOMAIN = (
    PROJECT_ROOT / "rates_agent" / "_pr10f_fixture_test_domain"
)

# The growth proof spawns an INNER pytest subprocess that legitimately needs
# the fixture-domain folder its parent just wrote (it reads the freshly
# discovered domain).  That subprocess loads this same conftest, so it sets
# this env var to opt OUT of the sweep — otherwise the sweep would delete the
# folder out from under the very assertions the subprocess exists to run.
_KEEP_FIXTURE_DOMAIN_ENV = "_MACRO_GROWTH_PROOF_KEEP_FIXTURE_DOMAIN"


def _sweep_leaked_fixture_domain() -> None:
    """Remove any leftover synthetic fixture-domain folder.  Idempotent and
    crash-safe (``ignore_errors=True``)."""
    if _LEAKABLE_FIXTURE_DOMAIN.exists():
        shutil.rmtree(_LEAKABLE_FIXTURE_DOMAIN, ignore_errors=True)


def pytest_configure(config) -> None:
    # --- marker registration -------------------------------------------------
    # ``db_validation``: the Layer-B DB-parity gate (Fable Plan 2 §10 / PR16
    # triplet 3rd file).  The ``*_sql_validation.py`` scripts are standalone
    # ``__main__`` runners (excluded from default collection via
    # ``collect_ignore`` below), so ``pytest tests/`` proves the DB-parity
    # layer of NONE of them.  ``tests/test_db_validation_gate.py`` carries
    # this marker and drives the Track-A validators against the real
    # ``macro-tsdb`` as a gate-checkable suite; it skips cleanly when the DB
    # is unreachable.  Run it explicitly with ``pytest -m db_validation``.
    config.addinivalue_line(
        "markers",
        "db_validation: Layer-B DB-parity validation against the real "
        "macro-tsdb (Fable Plan 2 §10 / PR16); skipped when the DB is "
        "unreachable.  Select with `pytest -m db_validation`.",
    )

    # --- leaked-fixture sweep ------------------------------------------------
    # Runs once per session, before collection imports
    # orchestrator.domain_registry, so a survivor folder from a prior
    # hard-killed run can't inflate this session's closed-domain family.
    # The growth proof's inner subprocess opts out (it needs the folder).
    if os.environ.get(_KEEP_FIXTURE_DOMAIN_ENV) == "1":
        return
    _sweep_leaked_fixture_domain()


# Files in tests/ that pytest should not try to import or collect.
# Listed by basename; pytest matches against the filename within this
# conftest's directory.  Every entry below corresponds to a file that
# actually exists in tests/ — keep it that way.
collect_ignore = [
    "test_breakeven_butterfly_sql_validation.py",
    "test_breakeven_curve_spread_sql_validation.py",
    "test_breakeven_inflation_simple_sql_validation.py",
    "test_build_linker_panel_sql_validation.py",
    "test_build_policy_futures_strip_panel_sql_validation.py",
    "test_build_zcis_panel_sql_validation.py",
    "test_butterfly_sql_validation.py",
    "test_calculate_ois_butterfly_sql_validation.py",
    "test_cpi_surprise_sql_validation.py",
    "test_cross_country_breakeven_spread_simple_sql_validation.py",
    "test_cross_country_real_yield_spread_simple_sql_validation.py",
    "test_cross_market_inflation_swap_spread_sql_validation.py",
    "test_cross_market_sql_validation.py",
    "test_curve_move_classifier_sql_validation.py",
    "test_curve_spread_sql_validation.py",
    "test_forward_breakeven_simple_sql_validation.py",
    "test_futures_price_level_sql_validation.py",
    "test_futures_strip_snapshot_sql_validation.py",
    "test_futures_volume_oi_sql_validation.py",
    "test_get_otr_history_sql_validation.py",
    "test_inflation_swap_butterfly_sql_validation.py",
    "test_inflation_swap_curve_spread_sql_validation.py",
    "test_inflation_swap_forward_sql_validation.py",
    "test_inflation_swap_rate_level_sql_validation.py",
    "test_nfp_surprise_sql_validation.py",
    "test_ois_cross_market_spread_sql_validation.py",
    "test_ois_curve_spread_sql_validation.py",
    "test_ois_forward_rate_sql_validation.py",
    "test_otr_ofr_spread_sql_validation.py",
    "test_sovereign_curve_regime_sql_validation.py",
    "test_curve_fair_value_sql_validation.py",
    "test_rates_vol_regime_sql_validation.py",
    "test_ois_policy_path_regime_sql_validation.py",
    "test_swap_carry_and_roll_sql_validation.py",
    "test_implied_forward_curve_sql_validation.py",
    "test_pca_neutral_butterfly_weights_sql_validation.py",
    "test_policy_futures_futures_butterfly_simple_sql_validation.py",
    "test_policy_futures_futures_calendar_spread_sql_validation.py",
    "test_policy_futures_futures_cross_market_spread_sql_validation.py",
    "test_policy_futures_futures_pack_average_simple_sql_validation.py",
    "test_policy_futures_futures_price_level_sql_validation.py",
    "test_policy_futures_volume_open_interest_snapshot_sql_validation.py",
    "test_real_yield_butterfly_sql_validation.py",
    "test_real_yield_curve_spread_sql_validation.py",
    "test_real_yield_level_sql_validation.py",
    "test_scan_bond_futures_extremes_sql_validation.py",
    "test_scan_inflation_linkers_extremes_sql_validation.py",
    "test_scan_inflation_swaps_extremes_sql_validation.py",
    "test_scan_policy_futures_extremes_sql_validation.py",
    "test_scanner_sql_validation.py",
    "test_swap_breakeven_basis_simple_sql_validation.py",
    "test_swap_spread_sql_validation.py",
    "test_wirp_meeting_pricing_sql_validation.py",
    "test_ws_chat.py",
    "test_yield_levels_sql_validation.py",
]
