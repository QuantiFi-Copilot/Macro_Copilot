"""tests/test_r6_backend_changes.py — Phase R6.4 read-time synthetic-index labels.

Focused unit tests for the R6.4 backend change: ``_extract_preview``
should produce meaningful labels ("Day -5", "Summary · mean") for
Series artifacts whose synthetic index would otherwise read as
``1970-01-01`` / ``1900-01-01`` ISO dates.

R6.4 generalises the R5.1 fix:
  - R5.1 added an explicit ``payload.index_encoding`` field at WRITE
    time for conditional_aggregate outputs.  Old artifacts persisted
    before the fix didn't get the field.
  - R6.4 adds a READ-time detector that inspects ``metadata.lineage``
    so old artifacts benefit too, AND extends coverage to
    summarize_series (1900 sentinel).

These tests run the registry directly against synthetic StoredArtifact
dicts — no DB / no live operators — so they execute under the
standard pytest tree.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# R6.4 — conditional_aggregate: detect from lineage when payload encoding
# is missing (covers OLD artifacts).
# ---------------------------------------------------------------------------


class TestConditionalAggregateLineageFallback:
    """When the inline payload doesn't carry ``index_encoding`` (R5.1 path),
    R6.4's read-time detector picks up the operator from
    ``metadata.lineage`` and substitutes Day-offset labels."""

    def test_picks_event_offset_labels_from_lineage(self):
        from state.artifact_store import _extract_preview

        # An OLD artifact persisted before R5.1 — no ``index_encoding``
        # on the payload, just the raw 1970-anchor ISO dates.
        inline_payload = {
            "artifact_type": "Series",
            "metadata": {
                "lineage": {
                    "steps": [
                        {
                            "name": "conditional_aggregate",
                            "params": {
                                "offset_anchor": "1970-01-01",
                                "event_relative_offsets": [-2, -1, 0, 1, 2],
                            },
                        }
                    ]
                }
            },
            "payload": {
                "index": [
                    "1969-12-30T00:00:00",
                    "1969-12-31T00:00:00",
                    "1970-01-01T00:00:00",
                    "1970-01-02T00:00:00",
                    "1970-01-03T00:00:00",
                ],
                "values": [0.1, 0.0, -0.06, 0.02, 0.05],
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["Day -2", "Day -1", "Day 0", "Day +1", "Day +2"]
        assert vals == [0.1, 0.0, -0.06, 0.02, 0.05]

    def test_explicit_encoding_wins_over_lineage(self):
        """When ``payload.index_encoding`` IS present (R5.1 newer write),
        the explicit path wins and lineage doesn't need to fire."""
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "metadata": {
                # Lineage carries DIFFERENT offsets so we can tell which
                # path was used.  R5.1 path should win because the
                # explicit ``index_encoding`` is present.
                "lineage": {
                    "steps": [
                        {
                            "name": "conditional_aggregate",
                            "params": {
                                "offset_anchor": "1970-01-01",
                                "event_relative_offsets": [-99, -99],
                            },
                        }
                    ]
                }
            },
            "payload": {
                "index": ["1970-01-01T00:00:00", "1970-01-02T00:00:00"],
                "values": [1.0, 2.0],
                "index_encoding": {
                    "kind": "event_offset",
                    "anchor": "1970-01-01",
                    "offsets": [0, 1],
                },
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["Day 0", "Day +1"]
        assert vals == [1.0, 2.0]


# ---------------------------------------------------------------------------
# R6.4 — summarize_series: 1900-01-01 sentinel → "Summary · <statistic>".
# ---------------------------------------------------------------------------


class TestSummarizeSeriesLabel:
    """summarize_series emits a 1-row Series with a 1900-01-01 sentinel.
    The detector reads ``step_params.statistic`` and surfaces a clean
    summary label instead of the sentinel date."""

    def test_mean_label(self):
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "metadata": {
                "lineage": {
                    "steps": [
                        {
                            "name": "summarize_series",
                            "params": {
                                "statistic": "mean",
                                "sentinel_date": "1900-01-01",
                            },
                        }
                    ]
                }
            },
            "payload": {
                "index": ["1900-01-01T00:00:00"],
                "values": [0.820],
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["Summary · mean"]
        assert vals == [0.820]

    def test_median_label(self):
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "metadata": {
                "lineage": {
                    "steps": [
                        {
                            "name": "summarize_series",
                            "params": {
                                "statistic": "median",
                                "sentinel_date": "1900-01-01",
                            },
                        }
                    ]
                }
            },
            "payload": {
                "index": ["1900-01-01T00:00:00"],
                "values": [0.799],
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["Summary · median"]
        assert vals == [0.799]

    def test_no_statistic_falls_back_to_generic_label(self):
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "metadata": {
                "lineage": {
                    "steps": [
                        {"name": "summarize_series", "params": {}}
                    ]
                }
            },
            "payload": {
                "index": ["1900-01-01T00:00:00"],
                "values": [0.5],
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["Summary"]
        assert vals == [0.5]


# ---------------------------------------------------------------------------
# R6.4 — unrelated operators leave the preview path unchanged.
# ---------------------------------------------------------------------------


class TestNonSyntheticOperatorsUnchanged:
    """Series produced by operators NOT in the synthetic-index registry
    (e.g. ``rolling_zscore``, ``rolling_mean``) keep the normal ISO-date
    preview path — the detector returns ``None`` and the fallthrough
    fires."""

    def test_rolling_zscore_keeps_iso_dates(self):
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "metadata": {
                "lineage": {
                    "steps": [
                        {"name": "rolling_zscore", "params": {"window": 252}}
                    ]
                }
            },
            "payload": {
                "index": ["2024-01-02T00:00:00", "2024-01-03T00:00:00"],
                "values": [0.5, 0.6],
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["2024-01-02T00:00:00", "2024-01-03T00:00:00"]
        assert vals == [0.5, 0.6]

    def test_empty_lineage_keeps_iso_dates(self):
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "metadata": {"lineage": {"steps": []}},
            "payload": {
                "index": ["2024-01-02T00:00:00"],
                "values": [1.5],
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["2024-01-02T00:00:00"]
        assert vals == [1.5]

    def test_missing_metadata_keeps_iso_dates(self):
        """Defensive: a malformed StoredArtifact dict with no metadata
        should NOT raise — it should fall through to the ISO-date
        path."""
        from state.artifact_store import _extract_preview

        inline_payload = {
            "artifact_type": "Series",
            "payload": {
                "index": ["2024-01-02T00:00:00"],
                "values": [1.5],
            },
        }
        idx, vals = _extract_preview(inline_payload, max_points=16)
        assert idx == ["2024-01-02T00:00:00"]
        assert vals == [1.5]
