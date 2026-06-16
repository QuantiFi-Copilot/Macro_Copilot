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

The quant↔analytics boundary is documented authoritatively in
``docs_revamped/01_architecture/00_internal_architecture.md`` (the
"quant vs analytics: finance-blind numerics vs finance-aware math"
section); this package is the single home of the finance-BLIND model
numerics, ``shared/analytics/`` of the finance-AWARE deterministic math.

PUBLIC API (m28 / P3): the package re-exports every module's public
fit/test function + result dataclass below, so callers may write
``from shared.quant import fit_ou_ar1`` (previously unresolvable — only
``shared.quant.ou.fit_ou_ar1`` worked).  ``__all__`` is the closed
public surface.

Signature convention (m28 / P3): the standard call shape is
``fn(data, *, <cohesive model spec>, …)`` — the data array positional,
every model-spec knob keyword-only.  ``fit_pca`` / ``fit_gmm_em`` /
``fit_hmm_em`` take their spec count positionally today for back-compat
with their operator callers; new quant functions adopt the kw-only
convention and the existing ones migrate when their operator signatures
are next revised (a coordinated change, not done piecemeal here).
"""

from shared.quant.changepoint import ChangepointResult, binary_segmentation
from shared.quant.garch import GarchFitResult, fit_garch_11
from shared.quant.gmm import GmmFitResult, fit_gmm_em
from shared.quant.hmm import HmmFitResult, fit_hmm_em
from shared.quant.hurst import HurstResult, hurst_rs
from shared.quant.kalman import (
    DIFFUSE_PRIOR_VARIANCE,
    DlmFilterResult,
    dlm_filter,
)
from shared.quant.ou import (
    OuCoreFit,
    OuFitResult,
    fit_ou_ar1,
    fit_ou_core,
)
from shared.quant.pca import (
    PcaFitResult,
    PcaSvdCore,
    RollingPcaResult,
    fit_pca,
    pca_svd_core,
    reconstruct_residual,
    rolling_pca_scores,
)
from shared.quant.variance_ratio import (
    VarianceRatioResult,
    variance_ratio_test,
)


__all__ = [
    # --- the public fit / test / decomposition functions ---
    "binary_segmentation",      # changepoint
    "fit_garch_11",             # garch
    "fit_gmm_em",               # gmm
    "fit_hmm_em",               # hmm
    "hurst_rs",                 # hurst
    "dlm_filter",               # kalman
    "fit_ou_ar1",               # ou
    "fit_ou_core",              # ou (the finance-blind regression core)
    "fit_pca",                  # pca
    "pca_svd_core",             # pca (the finance-blind SVD core)
    "reconstruct_residual",     # pca
    "rolling_pca_scores",       # pca
    "variance_ratio_test",      # variance_ratio
    # --- the result dataclasses ---
    "ChangepointResult",
    "GarchFitResult",
    "GmmFitResult",
    "HmmFitResult",
    "HurstResult",
    "DlmFilterResult",
    "OuFitResult",
    "OuCoreFit",
    "PcaFitResult",
    "PcaSvdCore",
    "RollingPcaResult",
    "VarianceRatioResult",
    # --- module-level public constants ---
    "DIFFUSE_PRIOR_VARIANCE",
]
