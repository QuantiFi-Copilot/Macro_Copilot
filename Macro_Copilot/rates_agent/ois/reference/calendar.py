"""
calendar.py — Central-bank meeting calendar abstraction
=========================================================

Provides a ``CalendarProvider`` protocol and a YAML-backed default
implementation used by ``ois_meeting_pricing`` to look up FOMC / ECB /
BoE / BoJ / RBA / BoC meeting dates.

Why an abstraction rather than a direct YAML import?
The meeting-pricing tool should not be coupled to a specific data
source.  A static YAML is fine for prototyping, but we expect to later
swap in:

  - a DB-backed provider (once calendars live in a reference table)
  - an API-backed provider (for real-time unscheduled meetings)
  - a composite provider (merging scheduled + emergency meetings)

By routing every lookup through ``CalendarProvider.get_meetings(...)``,
none of those migrations requires touching the pricing math.

Central-bank identifiers (ISO-style strings) are stable across providers.
The YAML's top-level keys use these exact strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Protocol

import yaml


# ============================================================================
# CENTRAL BANK → DEFAULT OIS CURVE MAPPING
# ============================================================================
# Used by the meeting-pricing tool to pick a default curve when the user
# names a central bank but not a specific curve_family.  Keeps the LLM
# from having to reason about the SOFR↔FED, ESTR↔ECB correspondence.

CENTRAL_BANK_TO_OIS_CURVE: dict[str, str] = {
    "FED":   "USD_SOFR_OIS",
    "FOMC":  "USD_SOFR_OIS",  # user-facing alias
    "ECB":   "EUR_ESTR_OIS",
    "BOE":   "GBP_SONIA_OIS",
    "BOJ":   "JPY_OIS",
    "RBA":   "AUD_OIS",
    "BOC":   "CAD_OIS",
}


def normalise_central_bank(name: str) -> str:
    """Canonicalise a user-supplied central bank identifier.

    Accepts common variants (``"Fed"``, ``"FOMC"``, ``"fed"``, etc.)
    and returns the uppercase canonical key used in the YAML and in
    ``CENTRAL_BANK_TO_OIS_CURVE``.
    """
    return name.strip().upper()


# ============================================================================
# MEETING RECORD
# ============================================================================

@dataclass(frozen=True)
class Meeting:
    """One scheduled monetary-policy meeting."""

    central_bank: str          # Canonical uppercase name (e.g. "FED")
    meeting_date: date         # ISO date of the meeting (or statement day)
    label: str                 # Human-readable tag, e.g. "FOMC Jun 2026"


# ============================================================================
# PROVIDER PROTOCOL
# ============================================================================

class CalendarProvider(Protocol):
    """A source of scheduled central-bank meeting dates.

    Any compliant implementation must expose ``get_meetings`` with the
    same signature as ``YamlCalendarProvider`` below.  This lets the
    meeting-pricing tool swap data sources without touching math.
    """

    def get_meetings(
        self,
        central_bank: str,
        *,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> list[Meeting]:
        ...


# ============================================================================
# YAML-BACKED PROVIDER
# ============================================================================

class YamlCalendarProvider:
    """Loads meetings from a YAML file laid out as::

        FED:
          - date: 2026-06-17
            label: "FOMC Jun 2026"
          - date: 2026-07-29
            label: "FOMC Jul 2026"
        ECB:
          - date: 2026-06-04
            label: "ECB Jun 2026"
        ...

    The file is read once per process (lazy on first call), then cached.
    """

    def __init__(self, yaml_path: Optional[Path] = None):
        if yaml_path is None:
            yaml_path = Path(__file__).parent / "meetings.yml"
        self._yaml_path = yaml_path
        self._cache: Optional[dict[str, list[Meeting]]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_meetings(
        self,
        central_bank: str,
        *,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> list[Meeting]:
        """Return all known meetings for ``central_bank`` within the
        ``[from_date, to_date]`` window (inclusive).

        If either bound is None, it is treated as unbounded on that
        side.  Returns an empty list if the central bank is unknown.
        """
        bank = normalise_central_bank(central_bank)
        self._ensure_loaded()
        assert self._cache is not None
        meetings = self._cache.get(bank, [])
        filtered = []
        for m in meetings:
            if from_date and m.meeting_date < from_date:
                continue
            if to_date and m.meeting_date > to_date:
                continue
            filtered.append(m)
        return filtered

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._cache is not None:
            return
        raw = self._read_yaml()
        cache: dict[str, list[Meeting]] = {}
        for bank, entries in raw.items():
            bank_key = normalise_central_bank(bank)
            parsed_meetings: list[Meeting] = []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw_date = entry.get("date")
                if raw_date is None:
                    continue
                meeting_date = _coerce_date(raw_date)
                if meeting_date is None:
                    continue
                label = str(entry.get("label") or f"{bank_key} {meeting_date.isoformat()}")
                parsed_meetings.append(
                    Meeting(central_bank=bank_key, meeting_date=meeting_date, label=label)
                )
            parsed_meetings.sort(key=lambda m: m.meeting_date)
            cache[bank_key] = parsed_meetings
        self._cache = cache

    def _read_yaml(self) -> dict:
        if not self._yaml_path.exists():
            return {}
        with open(self._yaml_path, "r") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}


# ============================================================================
# HELPERS
# ============================================================================

def _coerce_date(value) -> Optional[date]:
    """Accept either a ``date``/``datetime`` (PyYAML parses ISO dates
    automatically) or a string in YYYY-MM-DD form."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


# ============================================================================
# MODULE-LEVEL DEFAULT PROVIDER
# ============================================================================
# Meeting pricing imports this default; injecting a different provider
# at call-time is possible for tests or alternative data sources.

DEFAULT_CALENDAR = YamlCalendarProvider()


def select_meetings(
    provider: CalendarProvider,
    central_bank: str,
    starting_from: date,
    count: int,
) -> list[Meeting]:
    """Return the next ``count`` meetings for ``central_bank`` whose
    date is on or after ``starting_from``.  Uses the provider's own
    filtering plus a trailing slice.

    Returns [] if no meetings match — callers should surface a clean
    error on empty output rather than silently proceeding.
    """
    all_meetings = provider.get_meetings(
        central_bank, from_date=starting_from,
    )
    return all_meetings[:max(count, 0)]


def resolve_meeting_reference(
    provider: CalendarProvider,
    central_bank: str,
    reference: str,
    *,
    today: Optional[date] = None,
) -> Iterable[Meeting]:
    """Convert a user-friendly ``reference`` into the meetings the
    pricing tool should surface.

    Supported syntaxes:
      - ``"next"``          — the next scheduled meeting only
      - ``"next:N"``        — the next N meetings
      - ``"+N"``            — the Nth meeting from today (N >= 1)
      - ``"YYYY-MM-DD"``    — the meeting on that exact date (or closest
                              in the future if there is no exact match)
      - ``"all"``            — every upcoming meeting the provider knows

    Unknown inputs raise ``ValueError`` so the tool can surface a clean
    error.  Never returns a raw generator — materialises a list.
    """
    today = today or date.today()
    ref = reference.strip().lower()

    if ref == "all":
        return provider.get_meetings(central_bank, from_date=today)

    if ref == "next":
        meetings = provider.get_meetings(central_bank, from_date=today)
        return meetings[:1]

    if ref.startswith("next:"):
        try:
            n = int(ref.split(":", 1)[1])
        except ValueError:
            raise ValueError(f"Could not parse meeting count in {reference!r}")
        if n <= 0:
            raise ValueError(f"Meeting count must be positive, got {n}")
        meetings = provider.get_meetings(central_bank, from_date=today)
        return meetings[:n]

    if ref.startswith("+"):
        try:
            n = int(ref[1:])
        except ValueError:
            raise ValueError(f"Could not parse offset in {reference!r}")
        if n <= 0:
            raise ValueError(f"Offset must be positive, got {n}")
        meetings = provider.get_meetings(central_bank, from_date=today)
        if n > len(meetings):
            raise ValueError(
                f"Only {len(meetings)} upcoming meetings known for "
                f"{central_bank}; requested +{n}."
            )
        return [meetings[n - 1]]

    # Exact date or nearest-future fallback.
    exact = _coerce_date(reference)
    if exact is None:
        raise ValueError(
            f"Could not interpret meeting reference {reference!r}.  "
            "Try 'next', 'next:N', '+N', or YYYY-MM-DD."
        )
    meetings = provider.get_meetings(central_bank, from_date=today)
    for m in meetings:
        if m.meeting_date == exact:
            return [m]
        if m.meeting_date > exact:
            return [m]
    raise ValueError(
        f"No scheduled {central_bank} meeting on or after "
        f"{exact.isoformat()}."
    )
