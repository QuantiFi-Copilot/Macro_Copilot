"""shared.quant — finance-blind statistical / model-fit numerics.

The statistical sibling of ``shared/analytics/`` (plan: tmp/fable_plan_2.md,
Track A).  ``shared/analytics/`` owns the finance-aware deterministic math
that primitives call (spreads, levels, curve moves, panel assembly);
``shared/quant/`` owns the **finance-blind** statistical and model-fit
numerics that the L3 operators call (OU fits, PCA decomposition, regime
HMM/GMM, descriptive GARCH, changepoint detection, statistical tests).

Boundary rules (P9 / OPR6):

  - Nothing in this package may import from ``rates_agent/`` (or any
    domain agent), name an instrument/tenor/curve/asset-class concept, or
    perform finance math.  Pure numerics over numpy / pandas inputs.
  - Functions here are PURE and deterministic: no DB, no I/O, no clock,
    no unfixed randomness (model fits that need initialisation take an
    explicit ``random_state`` / use deterministic initialisation).
  - The operator layer (``shared/operators/``) is the primary
    production consumer; operators wrap these numerics behind typed-
    artifact signatures.  The finance-aware ``shared/analytics/`` layer
    may also reuse a core here when a primitive and an operator would
    otherwise duplicate the same numeric (e.g.
    ``shared.analytics.stats.ou_half_life`` calls
    ``shared.quant.ou.fit_ou_core``) — never importing the other way
    (``shared/quant/`` must not import ``shared/analytics/``).  Per the
    plan's §4: build the model math ONCE here — never a per-instrument
    copy inside a primitive.
"""
