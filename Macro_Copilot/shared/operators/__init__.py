"""shared.operators — central operator layer.

Per ``docs/architecture/operator_architecture.md``, central operators
are finance-domain-blind transformations over typed analytical
artifacts.  They consume / emit ``shared.artifacts.*`` types and own
structural method families (alignment, arithmetic, masking, windowing,
aggregation, ranking, mapping).

Phase 1A operators (build plan v5):

  - ``align_series``         (alignment)        — Week 1 (this PR)
  - ``series_arithmetic``    (arithmetic)       — Week 2
  - ``threshold_events``     (masking)          — Week 3
  - ``event_windows``        (windowing)        — Week 4
  - ``conditional_aggregate``(aggregation)      — Week 5

Each operator lives in its own subpackage with the same per-folder
shape primitives use::

    shared/operators/<operator_name>/
        __init__.py        re-exports public API
        schemas.py         Pydantic input/output models
        config.yaml        OperatorConfig YAML
        operator.py        the pure compute() function
"""
