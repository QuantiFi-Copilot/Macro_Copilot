"""shared.artifacts.registry — the canonical artifact closed-family enum.

ART2 / ART6 (``docs_revamped/02_components/artifact/README.md``) require the
artifact closed family to be declared **once** — as a single source of truth —
with every other authority *derived* from it.  This module owns that single
source.

The canonical declaration is the ``ArtifactTypeName`` ``str``-valued ``Enum``
below: one member per artifact wrapper class, whose ``value`` equals the
wrapper class ``__name__`` (e.g. ``SERIES = "Series"``).  Every downstream
site — the executor's ``_ARTIFACT_TYPE_MAP``, the validator's
``ARTIFACT_TYPE_NAMES`` tuple, the persisted-type discriminator, the store
codec, the ``WorkflowResult`` terminal-artifact union — must derive from this
enum (or the companion ``ARTIFACT_CLASS_TO_NAME`` mapping) rather than
re-declare the family.

A separate module (not ``shared/artifacts/types.py``) avoids any risk of an
import cycle: ``types.py`` declares the wrapper classes; this module imports
them and binds them into the enum / class map.  ``shared.workflow.registry``
re-exports ``ARTIFACT_TYPE_NAMES`` (derived from the enum here) so existing
``from shared.workflow.registry import ARTIFACT_TYPE_NAMES`` callers keep
working unchanged.

The companion lock-step test
(``tests/test_artifact_closed_family_lockstep.py``) asserts the canonical
enum agrees with every derived site — so a drift in either direction (a new
artifact wrapper missing from the enum, or an enum member missing from a
derived site) trips the test rather than silently desyncing at runtime.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Type

from pydantic import BaseModel

from shared.artifacts.types import (
    EventSet,
    Panel,
    ScalarMetric,
    Series,
    SeriesSet,
    WindowedPanel,
)


class ArtifactTypeName(str, Enum):
    """Canonical closed-family enum of artifact-type names.

    Each member's ``value`` equals the corresponding artifact wrapper
    class ``__name__`` (e.g. ``ArtifactTypeName.SERIES.value == "Series"``).
    Being ``str``-valued, members compare equal to and serialize as their
    string value, so existing string-keyed call sites (``"Series" in
    ARTIFACT_TYPE_NAMES``, ``output_type="Series"``) keep working
    unchanged when ``ARTIFACT_TYPE_NAMES`` is derived from this enum.

    Member ordering is the canonical ordering used by every derived site:
    Series, SeriesSet, EventSet, Panel, WindowedPanel, ScalarMetric.
    """

    SERIES = "Series"
    SERIES_SET = "SeriesSet"
    EVENT_SET = "EventSet"
    PANEL = "Panel"
    WINDOWED_PANEL = "WindowedPanel"
    SCALAR_METRIC = "ScalarMetric"


# Canonical runtime-class → enum-member mapping.  The companion to
# ``ArtifactTypeName`` keyed by the wrapper class itself, used by the
# executor's ``artifact_type_name`` for isinstance dispatch.  Declared
# here (rather than at the use site) so it stays in lockstep with the
# enum above; the lockstep test asserts every enum member has exactly
# one class entry and vice-versa.
ARTIFACT_CLASS_TO_NAME: Dict[Type[BaseModel], ArtifactTypeName] = {
    Series: ArtifactTypeName.SERIES,
    SeriesSet: ArtifactTypeName.SERIES_SET,
    EventSet: ArtifactTypeName.EVENT_SET,
    Panel: ArtifactTypeName.PANEL,
    WindowedPanel: ArtifactTypeName.WINDOWED_PANEL,
    ScalarMetric: ArtifactTypeName.SCALAR_METRIC,
}


__all__ = [
    "ArtifactTypeName",
    "ARTIFACT_CLASS_TO_NAME",
]
