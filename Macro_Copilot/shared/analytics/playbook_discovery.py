"""
playbook_discovery.py — single-source-of-truth playbook scanner for
curve-family-agnostic rates primitives.
====================================================================

Round 3 Stage 2, work item A3 (PR #195) introduced multi-playbook
discovery for ``pca_yield_curve``; Stage 2 A4 (PR #196) needs the
same logic for ``classify_curve_move``.  This module is the single
canonical home for that discovery so the two primitives do NOT
duplicate it per-folder (P10 — single source of truth).

What "tenor-keyed benchmark playbook" means
-------------------------------------------
A "benchmark-curve" rates playbook (the kind PCA / classify_curve_move
can fit on) has these properties:

  - Every universe entry carries both ``curve_family`` and ``tenor``
    (rules out event playbooks, futures-strip playbooks keyed on
    ``strip_position``, meeting-calendar playbooks).
  - Every ``(curve_family, tenor)`` pair is unique in the universe
    so the SQL fetcher's ``WHERE curve_family = :cf AND tenor = ANY(:tenors)``
    returns ONE row per (date, tenor) (rules out bond_futures.yml's
    TY1/UXY1 collision per TD #11; future-proof against any other
    playbook that grows ambiguous keys).
  - No entry carries ``instrument_type = 'sovereign_cash_bond'``
    (rules out the cusip-keyed cash-bond playbook — PCA / classify
    on a cusip panel is a different code path that the cusip-
    ingestion playbook extension will unblock).

The owning playbook's ``target_metrics[0].bloomberg_field`` is the
canonical primary observation field for every curve_family it
declares.  Today that resolves to: sovereign benchmarks + linkers →
``YLD_YTM_MID``; OIS curves → ``PX_LAST``; ZCIS curves → ``PX_MID``.
A primitive that auto-discovers the field per curve_family does
NOT need to enumerate per-vendor field conventions in its own
config.

Closed-family exposure
----------------------
Public API:
  - ``PLAYBOOK_ROOT``                                 — Path constant.
  - ``tenor_to_years(t: str) -> float``               — numeric sort key.
  - ``is_tenor_keyed_playbook(raw: dict) -> bool``    — predicate.
  - ``playbook_curve_family_index()
        -> Dict[str, Dict[str, Any]]``                — cached singleton.
  - ``playbook_tenors_for_curve_family(curve_family: str)
        -> List[str]``                                — raises ValueError on unknown.
  - ``playbook_default_field_for_curve_family(curve_family: str)
        -> Optional[str]``                            — None on unknown.

The cache is process-scoped via ``functools.lru_cache``.  Tests that
mutate playbook YAML mid-process should call
``playbook_curve_family_index.cache_clear()``.

Why this lives under shared/analytics/ (not rates_agent/playbooks/)
------------------------------------------------------------------
``shared/analytics/`` already hosts ``rates_fetch.py`` — the
instrument-type-agnostic SQL helpers that operate on the same
universe these playbooks declare.  The discovery layer is the
metadata twin of the fetcher: same scope, same callers, same
``instrument-type-agnostic`` invariant.  Co-locating keeps the
read-path (fetch + metadata lookup) in one place.

This module reads ``rates_agent/playbooks/*.yml`` directly.  That
import direction — shared/ reads from agents/ — is the documented
exception for substrate-twin metadata helpers (per the
shared.analytics package contract: helpers that need agent-level
playbook content reach DOWN to the canonical YAML rather than
duplicating it).  Pure substrate code (operators, executor, bridge)
remains agent-blind per P9.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ============================================================================
# CONSTANTS
# ============================================================================

# Resolve from this file's location up to the project root, then down to
# rates_agent/playbooks/.  This works whether the project is at
# ``/.../Macro_Copilot/`` directly or inside a worktree subdirectory.
PLAYBOOK_ROOT: Path = (
    Path(__file__).resolve().parents[2] / "rates_agent" / "playbooks"
)


# ============================================================================
# TENOR SORT
# ============================================================================

def tenor_to_years(t: str) -> float:
    """Best-effort numeric tenor sort key.  Handles the canonical
    rates tenors (1Y, 2Y, 5Y, 10Y, 20Y, 30Y) and the short-end
    tenors used by OIS curves (1W, 1M, 2M, 3M, 6M, 9M).  Falls back
    to ``inf`` (push-to-end, deterministic) for unrecognised labels
    so the routine never crashes on an unexpected tenor."""
    s = t.strip().upper()
    if s.endswith("Y"):
        try:
            return float(s[:-1])
        except ValueError:
            pass
    if s.endswith("M"):
        try:
            return float(s[:-1]) / 12.0
        except ValueError:
            pass
    if s.endswith("W"):
        try:
            return float(s[:-1]) / 52.0
        except ValueError:
            pass
    return float("inf")


# ============================================================================
# DISCRIMINATOR
# ============================================================================

def is_tenor_keyed_playbook(raw: Dict[str, Any]) -> bool:
    """Return True iff the playbook's universe is a benchmark-curve
    universe — every ``(curve_family, tenor)`` pair maps to exactly
    one daily observation per the SQL fetcher's expected shape.

    See module docstring's "What 'tenor-keyed benchmark playbook' means"
    section for the precise rules.  In summary, excludes:
      * empty universes,
      * universes whose entries lack ``curve_family`` or ``tenor``,
      * cash-bond playbooks (``instrument_type='sovereign_cash_bond'``),
      * playbooks where any ``(curve_family, tenor)`` pair is not
        unique (e.g. bond_futures.yml TY1/UXY1 collision per TD #11)."""
    universe = raw.get("universe") or []
    if not universe:
        return False
    pairs_seen: Dict[tuple, int] = {}
    has_any_tenor_keyed = False
    for item in universe:
        if not isinstance(item, dict):
            continue
        if item.get("instrument_type") == "sovereign_cash_bond":
            return False
        cf = item.get("curve_family")
        tenor = item.get("tenor")
        if not cf or not tenor:
            continue
        has_any_tenor_keyed = True
        key = (str(cf), str(tenor))
        pairs_seen[key] = pairs_seen.get(key, 0) + 1
    if not has_any_tenor_keyed:
        return False
    return all(c == 1 for c in pairs_seen.values())


# ============================================================================
# INDEX BUILDER + CONSUMER HELPERS
# ============================================================================

@lru_cache(maxsize=1)
def playbook_curve_family_index() -> Dict[str, Dict[str, Any]]:
    """Scan every tenor-keyed playbook under ``PLAYBOOK_ROOT``; return
    a ``{curve_family: {tenors, playbook, default_field}}`` map.

    A tenor-keyed playbook is one whose universe entries pass
    ``is_tenor_keyed_playbook()``.  Today this covers:
      * ``sovereign_bonds.yml`` (UST / DE_BUND / IT_BTP / FR_OAT /
        ES_BONO / UK_GILT / JGB / CANADA_GOVT / AU_GOVT)
      * ``ois.yml`` (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS /
        JPY_OIS / AUD_OIS / CAD_OIS)
      * ``inflation_swaps.yml`` (USD_ZCIS / EUR_ZCIS / GBP_ZCIS)
      * ``inflation_indexed_bonds.yml`` (USD_TIPS / GBP_LINKER /
        EUR_FR_LINKER / CAD_RRB)
      * ``overnight_rfr.yml`` (single-tenor curves; downstream
        consumers gate on ``len(tenors) >= n_components``).

    The per-curve-family default Bloomberg field is the owning
    playbook's ``target_metrics[0].bloomberg_field`` — every tenor-
    keyed playbook declares one.

    Tests that mutate the playbooks mid-process should call
    ``playbook_curve_family_index.cache_clear()`` to invalidate.
    """
    index: Dict[str, Dict[str, Any]] = {}
    for path in sorted(PLAYBOOK_ROOT.glob("*.yml")):
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(raw, dict) or not is_tenor_keyed_playbook(raw):
            continue
        target_metrics = raw.get("target_metrics") or []
        default_field = None
        if target_metrics and isinstance(target_metrics[0], dict):
            default_field = target_metrics[0].get("bloomberg_field")

        per_cf: Dict[str, List[str]] = {}
        for item in raw.get("universe", []):
            if not isinstance(item, dict):
                continue
            cf = item.get("curve_family")
            tenor = item.get("tenor")
            if not cf or not tenor:
                continue
            per_cf.setdefault(str(cf), []).append(str(tenor))

        for cf, tenors in per_cf.items():
            existing = index.get(cf)
            if existing is not None and existing["playbook"] != path.name:
                # Should not happen given is_tenor_keyed_playbook's
                # uniqueness guard, but raised explicitly so a future
                # playbook-shape change surfaces as a typed error
                # rather than silently merging tenor universes from
                # multiple playbooks.
                raise ValueError(
                    f"curve_family={cf!r} is declared in both "
                    f"{existing['playbook']!r} and {path.name!r}; each "
                    f"curve_family must live in exactly one playbook "
                    f"per the playbook contract."
                )
            entry = index.setdefault(cf, {
                "playbook": path.name,
                "default_field": default_field,
                "tenors": [],
            })
            entry["tenors"].extend(tenors)

    for cf, entry in index.items():
        entry["tenors"] = sorted(
            list(dict.fromkeys(entry["tenors"])),
            key=tenor_to_years,
        )
    return index


def playbook_tenors_for_curve_family(curve_family: str) -> List[str]:
    """Return the playbook tenor universe for one rates curve_family.

    Raises ``ValueError`` (callers turn into the controlled error
    envelope) if the curve_family is not declared in any tenor-keyed
    playbook under ``PLAYBOOK_ROOT``."""
    entry = playbook_curve_family_index().get(curve_family)
    if not entry:
        known = sorted(playbook_curve_family_index().keys())
        raise ValueError(
            f"Unknown curve_family={curve_family!r}.  Tenor lookup "
            f"requires a playbook-defined tenor universe under "
            f"rates_agent/playbooks/.  Known curve_families: {known}."
        )
    return list(entry["tenors"])


def playbook_default_field_for_curve_family(
    curve_family: str,
) -> Optional[str]:
    """Return the Bloomberg field declared as the owning playbook's
    primary target metric for one rates curve_family, or ``None`` if
    the curve_family is unknown or the playbook has no
    ``target_metrics[0].bloomberg_field``."""
    entry = playbook_curve_family_index().get(curve_family)
    if not entry:
        return None
    return entry.get("default_field")


__all__ = [
    "PLAYBOOK_ROOT",
    "tenor_to_years",
    "is_tenor_keyed_playbook",
    "playbook_curve_family_index",
    "playbook_tenors_for_curve_family",
    "playbook_default_field_for_curve_family",
]
