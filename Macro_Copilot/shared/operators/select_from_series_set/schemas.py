"""Pydantic parameter schema for select_from_series_set.

The operator exposes exactly one consequential parameter
(``series_key``); other accessor knobs (e.g. by-position selection
or wildcard prefix matching) are deferred until a real workflow
demands them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SelectFromSeriesSetParams(BaseModel):
    """Parameters for ``select_from_series_set``.

    Fields
    ------
    series_key :
        The key in the input ``SeriesSet.series_by_key`` to extract.
        Must match exactly (string equality); a ``KeyError``-class
        ``SelectFromSeriesSetError`` is raised when the key is not in
        the set, with the available keys included in the message so
        template authors can correct typos quickly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_key: str = Field(
        ...,
        min_length=1,
        description=(
            "The series_key to extract from the input SeriesSet.  "
            "Must match an entry in ``SeriesSet.series_by_key``."
        ),
    )


__all__ = ["SelectFromSeriesSetParams"]
