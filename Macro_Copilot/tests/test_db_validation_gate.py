"""tests/test_db_validation_gate.py — the DB-validation GATE (M12).

Fable Plan 2 §10 / PR16 triplet 3rd file ("Layer-B DB-backed read-only SQL
validation") + the §12 Definition-of-Done bullet that "the triplet passes
against the real DB".

THE PROBLEM THIS CLOSES (review finding M12 / R13 MAJOR)
--------------------------------------------------------
The per-tool ``tests/test_<tool>_sql_validation.py`` scripts that independently
reproduce a primitive's core computation in SQL against the real ``macro-tsdb``
are standalone ``__main__`` runners (argparse + ``sys.exit(1)``-on-mismatch),
explicitly excluded from default pytest collection (``tests/conftest.py``'s
``collect_ignore``).  So ``pytest tests/`` validated the DB-parity layer of
ZERO of them — the §12 "triplet passes against the real DB" claim rested on
un-automated, invisible manual script runs.  PR16 *permits* the standalone
form, but the §12 attestation could not be backed by a green gate.

WHAT THIS GATE DOES
-------------------
A SINGLE pytest-collectable, ``@pytest.mark.db_validation``-marked test,
parametrized over the Track-A validators, that RUNS each standalone script as a
subprocess against the live ``macro-tsdb`` and asserts its honest exit code is
``0`` (the scripts already ``sys.exit(1)`` on any tool-vs-SQL mismatch, so exit
0 == "every case PASSED").  Running each script as its own process respects
each validator's heterogeneous ``main()`` (random case selection, argparse,
data-relative windows) without re-implementing any of it.

  * Gate-checkable: ``pytest -m db_validation`` runs the validators and is
    GREEN iff the DB parity holds.
  * Honest skip: if ``macro-tsdb`` is unreachable (CI without the DB up), the
    whole module skips cleanly — mirroring the C2.1 north-star DB-guard and
    the existing ``test_financing_rate_sql_validation`` skipif pattern.  A
    skip never masquerades as a pass.

SCOPE
-----
``_TRACK_A_VALIDATORS`` is the 7 Track-A B2/1B primitive validators (the new
builds this plan registered).  ``_ALL_SQL_VALIDATORS`` is the broader set of
58 ``*_sql_validation.py`` scripts; the gate is parametrized over Track-A by
default (the new contract surface) and exposes the full list so the §12
attestation can enumerate the rest.  Run the full set with
``DB_VALIDATION_SCOPE=all pytest -m db_validation``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# The Track-A validators (the 7 primitives this plan built — R13 names them
# explicitly: the registered-but-not-canonical B2/1B builds).
# ---------------------------------------------------------------------------
_TRACK_A_VALIDATORS = [
    "test_curve_fair_value_sql_validation.py",
    "test_implied_forward_curve_sql_validation.py",
    "test_ois_policy_path_regime_sql_validation.py",
    "test_pca_neutral_butterfly_weights_sql_validation.py",
    "test_rates_vol_regime_sql_validation.py",
    "test_sovereign_curve_regime_sql_validation.py",
    "test_swap_carry_and_roll_sql_validation.py",
]


def _all_sql_validators() -> list[str]:
    """Every ``*_sql_validation.py`` script in tests/ (the broader 58)."""
    return sorted(p.name for p in TESTS_DIR.glob("test_*_sql_validation.py"))


# Which set the gate runs.  Default = the Track-A contract surface; set
# ``DB_VALIDATION_SCOPE=all`` to run every sql_validation script.
def _selected_validators() -> list[str]:
    if os.getenv("DB_VALIDATION_SCOPE", "track_a").lower() == "all":
        return _all_sql_validators()
    return list(_TRACK_A_VALIDATORS)


# ---------------------------------------------------------------------------
# DB-reachability guard (honest skip when macro-tsdb is down).
# ---------------------------------------------------------------------------
def _db_reachable() -> bool:
    try:
        from sqlalchemy import text  # local import: optional dep at collect

        from database.database import get_db_engine

        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


_DB_AVAILABLE = _db_reachable()

pytestmark = [
    pytest.mark.db_validation,
    pytest.mark.skipif(
        not _DB_AVAILABLE,
        reason=(
            "macro-tsdb is not reachable — the DB-validation gate skips "
            "cleanly (a skip is NOT a pass).  Bring the DB up and re-run "
            "`pytest -m db_validation`."
        ),
    ),
]


def _run_validator(script_name: str) -> subprocess.CompletedProcess:
    """Run one standalone sql_validation script as a subprocess with the
    project root on PYTHONPATH (the scripts import ``database`` /
    ``rates_agent`` as top-level packages)."""
    script_path = TESTS_DIR / script_name
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(
            os.pathsep
        )
    )
    return subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_track_a_validator_files_exist():
    """The 7 Track-A validator scripts the gate names must all exist (a typo'd
    name would otherwise silently parametrize to nothing)."""
    missing = [
        name for name in _TRACK_A_VALIDATORS
        if not (TESTS_DIR / name).is_file()
    ]
    assert not missing, f"Track-A validator scripts missing: {missing}"


@pytest.mark.parametrize("script_name", _selected_validators())
def test_sql_validation_passes_against_real_db(script_name):
    """Run the standalone Layer-B SQL validator against the live macro-tsdb
    and assert it PASSES (exit 0).  The script ``sys.exit(1)`` on any
    tool-vs-SQL mismatch, so exit 0 is the honest "DB parity holds" signal.

    This is the gate-checkable replacement for the invisible manual run:
    ``pytest -m db_validation`` is now the proof that the triplet's 3rd file
    passes against the real DB (Fable Plan 2 §10 / §12 / PR16)."""
    result = _run_validator(script_name)
    if result.returncode != 0:
        # Surface the script's own diagnostic tail so a failure is debuggable.
        tail = "\n".join(
            (result.stdout + "\n" + result.stderr).strip().splitlines()[-25:]
        )
        pytest.fail(
            f"{script_name} FAILED against macro-tsdb "
            f"(exit {result.returncode}) — DB parity NOT proven.\n"
            f"--- last 25 lines ---\n{tail}"
        )
