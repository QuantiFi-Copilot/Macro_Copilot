"""Pydantic parameter schema for convert_units."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from shared.artifacts.units import TimeSeriesUnits


class ConvertUnitsParams(BaseModel):
    """Parameters for ``convert_units``.

    ``target_units`` is a required per-call choice (the unit to convert
    the input Series to) — there is no sensible default, so omitting
    params raises rather than guessing (OPR8).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_units: TimeSeriesUnits


__all__ = ["ConvertUnitsParams"]
