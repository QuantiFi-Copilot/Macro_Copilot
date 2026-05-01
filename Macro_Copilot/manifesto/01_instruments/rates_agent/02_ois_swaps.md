# Overnight Index Swaps (OIS)

**Status:** v2 — fact-checked against external review on (a) RFR transition dates and discount-curve framing, (b) OIS conventions per playbook with reconciliation notes against ISDA / OpenGamma / Bloomberg primary sources, (c) risk-neutral language for forward rates and per-meeting pricing, (d) bucket reclassification (`zero_curve_bootstrap` to 1B; `cointegration_test` and `half_life_ou` to Bucket 2; `curve_regime` reframed as 1B-with-fixed-defaults), (e) data-lineage flags for transition-regime histories. To be further iterated against feedback from Brevan and advisory contacts.
**Scope:** SOFR (USD), ESTR (EUR), SONIA (GBP), TONA (JPY), AONIA (AUD), CORRA (CAD) OIS curves. These are the six curves currently in `/playbooks/ois.yml`.
**Cross-references:** Sovereign-to-OIS spreads (asset-swap, swap spreads) are jointly defined with `01_sovereign_bonds.md`. Forward decompositions of central-bank meeting probabilities are jointly relevant to `03_money_markets_and_cb_pricing.md` (when written). Swaption surfaces and rates volatility are out of scope here and live in `06_swaptions_and_rates_vol.md`.

---

## 1. What it is

An overnight index swap (OIS) is a fixed-for-floating interest rate swap in which the floating coupon over each accrual period is computed by compounding daily overnight risk-free rate (RFR) fixings in arrears, and the fixed leg pays a fixed rate (the "par OIS rate") negotiated at trade date. The par OIS rate is the fixed rate that sets the swap's present value to zero at inception under the applicable collateral, discounting, calendar, day-count, compounding, and payment-lag conventions. Economically, the par OIS rate at tenor T is the market's traded *risk-neutral* pricing of the average overnight RFR over the next T years, plus OIS term, liquidity, and technical premia.

The risk-neutral qualifier matters. Translating OIS rates and forwards into real-world expectations of central-bank policy requires explicit assumptions about term premia, meeting stubs, turn-of-year/quarter effects, and the wedge between the published overnight fixing and the central bank's policy target. The product surfaces these explicitly rather than collapsing them into a "this is what the market expects" claim.

In scope for this module: USD SOFR OIS, EUR ESTR OIS, GBP SONIA OIS, JPY TONA OIS, AUD AONIA OIS, CAD CORRA OIS. Each curve has 13 tenors in the playbook universe: 1W, 1M, 2M, 3M, 6M, 9M, 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y. ISDA-aligned RFR floating-rate options for these six rates are well-defined post-2021 [ISDA Supplements 64, 74, 76, 80, 89; ARRC; ECB Working Group on Euro Risk-Free Rates]. Note that "fixed-rate" does not mean "single payment": short-tenor OIS often has a single payment at maturity (zero-coupon structure), while longer-tenor OIS typically has annual or semi-annual fixed-leg payments depending on currency and convention.

## 2. Why it exists / role in the system

OIS swaps serve four overlapping functions, each with a different audience.

**Discount curve for collateralized derivatives.** Since the post-2008 collateralization shift, OIS curves have been the standard discounting framework for collateralized derivatives — predating the LIBOR transition. The RFR transition then changed the relevant overnight benchmark inside those OIS curves. Specifically, EUR cleared swaps moved from EONIA to ESTR discounting and Price Alignment Interest (PAI/PAA) on 27 July 2020, and USD cleared swaps moved from Effective Fed Funds (EFFR) to SOFR discounting/PAI in October 2020 — both LCH and CME centered on the close of business 16 October 2020 transition cycle, with associated cash compensation and a basis-swap auction (CME's basis-swap auction on Monday 19 October being the operational follow-on, not a separate discounting transition date) [LCH; CME *SOFR Discounting Transition Process for Cleared Swaps*; ARRC Paced Transition Plan; Risk.net]. ESTR has been published since 2 October 2019; EONIA was discontinued 3 January 2022. SONIA reform completed 2018; SOFR official publication began April 2018; TONA was selected as JPY RFR by the Cross-Industry Committee on Japanese Yen Interest Rate Benchmarks; CORRA replaced CDOR for derivatives by end-June 2024 (CDOR ceased after final publication on 28 June 2024) [Bank of Canada]; AONIA is the RBA cash rate. The distinction between "OIS discounting" (post-2008) and "SOFR/ESTR discounting" (2020 RFR transition) matters: they are not the same event. For our purposes this means: the OIS curve is the cleanest traded layer for risk-neutral pricing of central-bank policy paths.

**Macro / central bank pricing.** OIS forwards are the cleanest traded read on risk-neutral central-bank path pricing. The forward OIS rate for any window (e.g., the 1M-spot rate, the rate from the day before to the day after a specific meeting, the 2Y forward 1Y) is the market's risk-neutral pricing of the average overnight RFR over that window. They are not literal real-world probabilities unless one assumes away term premia, liquidity premia, meeting-stub effects, turns, and the wedge between overnight fixings and policy target rates. The product should display per-meeting "market-implied pricing" and surface these assumptions, not collapse them into a single "expected" number. With that caveat: 1Y1Y SOFR is a useful read on the Fed's near-cycle terminal trajectory; 5Y5Y SOFR is a useful long-run nominal policy / neutral-rate proxy that embeds risk-neutral expected short rates plus OIS term premium and technical factors [Fed FEDS Notes on shadow-rate models; Neuberger Berman]; meeting-dated OIS strips give per-meeting risk-neutral implied policy pricing once meeting calendars, stub conventions, and turn-of-year adjustments are made explicit.

**Interest rate hedging vehicle.** Banks, dealers, asset managers, and macro hedge funds use OIS to express directional, curve, and cross-market views on rates without cash-bond financing, repo specialness, or physical-bond settlement. For cleared swaps, leverage and balance-sheet economics can be attractive relative to cash bonds, but the true cost depends on margin model, clearing fees, funding cost, bid/ask, and CSA terms — "OIS is cheaper" is trader shorthand, not a universal accounting truth. Almost all swap-based macro RV trades (steepeners, flatteners, cross-currency rate divergence trades) are now OIS-based rather than the LIBOR-IRS structures that dominated pre-2022.

**Cross-currency basis component.** Cross-currency basis swaps use the two currencies' RFR/OIS curves as core inputs, but the quoted basis is *not* a simple OIS-rate differential. It is the spread required to clear a collateralized exchange of cashflows and principals across currencies under FX forwards, collateral, funding, and CIP-deviation conditions [BIS, *Covered interest parity lost*, 2016]. The XCCY basis (most actively traded as 3M USD/JPY, USD/EUR, USD/GBP) is the price of dollar funding to non-USD investors, and one of the canonical macro plumbing signals — it widens whenever offshore demand for USD funding outstrips supply. Owned canonically by the cross-currency basis module (`07_cross_currency_basis.md`); the OIS curves on each side are inputs.

## 3. What its movements signal (macro context)

This is the most important section for building macro intuition around OIS.

### Decomposition

The par OIS rate at tenor T can be approximately decomposed as:

```
par_OIS_rate(T) ≈ risk-neutral expected average overnight RFR over [0, T]
                + OIS term / risk premium
                + liquidity / convexity / technical premia
                + risk-neutral-vs-real-world wedge
```

OIS curves remove the sovereign-specific fiscal, supply, collateral, and credit/redenomination premia that show up in sovereign yields, which is why OIS is the cleaner traded layer for policy-path inference. They do *not* remove term premia, liquidity premia, convexity effects, or the risk-neutral-vs-real-world wedge. Fed research on intermediate-term policy expectations explicitly models OIS-rate term premia precisely because they are non-trivial even at horizons under five years [Federal Reserve, *A Shadow Rate Model of Intermediate-Term Policy Rate Expectations*, 2017]. For very short horizons (out to ~6M-1Y), the term-premium component is empirically small and OIS-implied policy pricing is close to expected-rate pricing in normal conditions; from 2Y onward, term premium grows; at 5Y-30Y the decomposition starts to look more like a sovereign yield decomposition, just without the fiscal/supply/credit confound.

### Curve shape

OIS curve slope usually reads more cleanly than sovereign curve slope because it removes sovereign supply, collateral, and credit premia. But it is not pure expected-policy: OIS curves still embed term premium, liquidity, convexity, convention, and risk-neutral-vs-real-world effects. A steep OIS curve can mean the market expects the central bank to hike, the OIS term premium has expanded, or technical/scarcity effects are at play. A flat or inverted OIS curve can mean the market expects rate cuts, OIS term premium has compressed, or QE/scarcity effects are suppressing the long end. OIS inversion is therefore a cleaner "cuts/pricing" signal than sovereign inversion, but not a mechanical recession or policy-probability oracle.

The ratio between OIS curve moves and sovereign curve moves is itself a useful informal signal. If 10Y UST sells off while 10Y SOFR OIS is flat, the move is likely outside the expected overnight-rate path — possibly Treasury term premium, supply/fiscal premium, liquidity, collateral, swap-spread dynamics, or balance-sheet effects. If both rise together, the move is more likely policy-expectation-driven. This is an informal decomposition, not a complete term-premium model — Bucket 2 tools (`term_premium_ois_acm`, `pca_ois_curve`) try to formalize it. The sovereign-vs-OIS comparison is what's available *now* without fitting a model.

### Forward rates and central bank pricing

OIS forwards encode the entire risk-neutral expected policy path. The most-watched forwards on a macro desk are:

- **1M forward starting 1M ahead** — what the market is pricing for the central bank rate after the next meeting, in risk-neutral terms.
- **Meeting-dated OIS** — using central-bank meeting calendars, effective-date and stub conventions, and overnight-fixing assumptions, decompose the front-end OIS strip into per-meeting risk-neutral implied policy changes. Critical for any "what's priced into next FOMC" analysis.
- **1Y1Y, 2Y1Y** — the 1-year forward 1-year-after, and 2-year forward 1-year-after; both are short-cycle terminal-rate proxies.
- **5Y5Y** — the 5-year forward 5-year-after; a long-run nominal policy-rate / neutral-rate proxy. Useful but not a pure r-star measure — it embeds risk-neutral expected short rates plus OIS term premium plus technical factors. Note that central banks themselves typically discuss *5Y5Y inflation forwards* as a more canonical inflation-expectations metric; the OIS-based 5Y5Y is the rates-side analogue.
- **3M3M, 6M6M** — short-window forwards used for tactical CB-pivot trades.

A note on calculation: the par OIS rate is not itself a zero rate, so a precise forward-rate computation should come from discount factors built by bootstrapping the OIS curve under conventions, calendars, payment lags, and interpolation choices [BTRM, *SOFR OIS Pricing and Riskless USD Curve Construction*, 2020]. The "two par rates" approximation is acceptable for an MVP at standard pillar tenors but does not handle stub periods, off-pillar windows, or convention mismatches cleanly. The product distinguishes between an approximate par-rate forward (built tool, 1A) and a bootstrapped-curve forward (planned 1B), with the difference exposed.

### Cross-market spreads

OIS-vs-OIS cross-currency spreads at fixed tenors (e.g., SOFR-ESTR 2Y, SOFR-SONIA 2Y, SOFR-TONA 2Y) read as the policy-divergence signal between two central banks. They are *cleaner* than sovereign-vs-sovereign equivalents because they strip out fiscal/supply/credit noise — but not pure: they can still reflect term premium differentials, liquidity, convention, collateral, and market-segmentation effects. SOFR-ESTR 2Y was historically near zero in the post-LIBOR-but-pre-2022 era when the Fed and ECB ran similar policy stances, then blew out to 200bp+ in 2022-23 as the Fed front-ran the ECB on hiking, and has been compressing since. The 2Y horizon is a particularly common reference because it covers the immediate policy cycle without much term-premium noise.

### Specific historical episodes worth knowing

These are the events you reach for when explaining what an OIS number means.

**LIBOR-OIS spread blowout, 2007-2008.** The 3M USD LIBOR-OIS spread is the canonical funding-stress indicator: in normal times it sat around 10bp; it peaked at 364bp on 10 October 2008 [BIS Quarterly Review, December 2008]. The spread is not pure interbank credit risk — it reflects bank credit, liquidity, term-funding, and money-market-dysfunction premia compounded together. The modern analogue post-LIBOR is some combination of Term-RFR-vs-OIS (where Term SOFR / Term SONIA are liquid enough), FRA-OIS, repo-vs-OIS, bank CP/CD spreads, and XCCY basis. No single replacement metric carries the same information; they decompose it across funding-channel-specific spreads.

**ESTR transition, October 2019 / July 2020.** ESTR began publication 2 October 2019 with EONIA recalibrated as ESTR + 8.5bp until its discontinuation on 3 January 2022. CCPs switched discounting from EONIA to ESTR on 27 July 2020 in a coordinated "big bang" with cash compensation [ECB; Eurex Clearing; Bianchetti & Scaringi 2025]. *Data-lineage implication:* our EUR_ESTR_OIS Bloomberg history extends back to 2005 per the playbook, but pre-October 2019 entries are vendor-proxied or EONIA-equivalent rather than live ESTR-OIS quotes. Treat pre-October 2019 as a transition/proxy regime; verify Bloomberg ticker lineage if pre-transition data is used in any model.

**SOFR transition, October 2020.** Both LCH and CME centered the USD CCP discounting transition on close of business 16 October 2020, with CME's basis-swap auction on Monday 19 October as the operational follow-on [LCH; CME *SOFR Discounting Transition Process*]. The SOFR-Fed-Funds basis moved with the run-up to the auction (peaking near 9bp at the long end on 29 September 2020) [Risk.net]. *Data-lineage implication:* SOFR was officially published from April 2018, but pre-2020 USD SOFR OIS history may be sparse, vendor-proxied, or backfilled from Fed-Funds OIS depending on Bloomberg's handling. Treat pre-October 2020 USD SOFR OIS as a transition/proxy regime; verify Bloomberg ticker lineage. Pre-April 2018 there is no SOFR — published proxy series exist [Federal Reserve, *Historical Proxies for the Secured Overnight Financing Rate*, 2019] but they are model-derived, not market-traded.

**2022 Fed hiking cycle.** The 2Y SOFR OIS rate moved from sub-1% in late 2021 to roughly 5% by mid-2023, the fastest repricing of the front end on modern record. The SOFR-ESTR 2Y differential blew out to ~200bp+ at peak. This is the canonical "expected-path-of-policy" repricing and the reference case for any forward-rate-decomposition model.

**Bank of Japan YCC exit, March 2024.** The BoJ introduced QQE with Yield Curve Control in September 2016, targeting 10Y JGB yields around zero, and formally exited YCC and the negative-interest-rate policy in March 2024 [BoJ]. YCC directly targeted the JGB curve and indirectly compressed JPY rate expectations and OIS term structure — the BoJ did not literally peg TONA OIS, but the policy regime anchored the entire yen rates complex. JPY OIS in the 2Y-10Y region behaved very differently pre- vs post-March 2024. Treat pre-2024 TONA OIS as a BoJ-controlled regime; any analysis spanning the YCC exit must treat it as a regime change.

**CORRA replaces CDOR for derivatives, June 2024.** CDOR ceased after final publication on 28 June 2024. Fallback Rate CORRA / CORRA-based products became the replacement framework for many CDOR-linked derivatives [Bank of Canada / CARR]. *Data-lineage implication:* CAD CORRA OIS history before CDOR cessation should be treated as a lower-liquidity / transition-regime dataset. Pre-2024 CAD swap liquidity was heavily CDOR/BA-linked; CORRA OIS existed but was not the central pricing layer. Confirm Bloomberg ticker lineage, underlying index, bid/ask history, and whether pre-2024 curves are true CORRA OIS quotes or vendor-constructed proxies before using them in models.

## 4. Market microstructure (just enough)

**Quoting and conventions.** OIS rates quote in percent, basis-point precision (e.g., "SOFR 2Y 4.27%" = 4.27 percentage points). The conventions below are *what is encoded in our current playbook* (`/playbooks/ois.yml`), reflecting Bloomberg ticker reference data. They are not statements of universal market law — ISDA Floating Rate Options, OpenGamma Strata, and CCP house conventions can differ in specific fields (notably payment delays and observation-shift mechanics). Any production tool consuming these fields should reconcile against ISDA RFR Conventions tables, the relevant CCP rulebook, and Bloomberg security metadata at the per-ticker level rather than hard-coding from prose.

Per-currency convention summary as encoded in the playbook (first-pillar entries shown; check the playbook for tenor-specific overrides, especially CAD which moves from Annual to SemiAnnual at ≥2Y):

- **USD SOFR OIS** — both legs ACT/360, annual payments at >1Y, T+2 settlement, 2 business day fixed/float pay delay, modified following business day adjustment, 0-day fixing lag, 1-day rate cutoff, in-arrears reset.
- **EUR ESTR OIS** — both legs ACT/360, annual payments at >1Y, T+2 settlement, **1 business day fixed/float pay delay** *as encoded in the playbook* (note: OpenGamma Strata's reference convention shows 2-day pay delay for the EUR_FIXED_1Y_ESTR_OIS template — this discrepancy should be reconciled against ISDA / Bloomberg primary source before using pay-delay-sensitive analytics).
- **GBP SONIA OIS** — both legs ACT/365 (fixed), annual payments at >1Y, T+0 settlement, 0 business day fixed/float pay delay (consistent with OpenGamma Strata and Clarus practitioner notes that say SONIA OIS does not have a payment delay).
- **JPY TONA OIS** — both legs ACT/365 (fixed), annual payments at >1Y, T+2 settlement, 2 business day fixed/float pay delay *as encoded in the playbook* (note: this matches OpenGamma Strata's JPY_FIXED_1Y_TONAR_OIS reference; an earlier draft's "1-day pay delay" was incorrect).
- **AUD AONIA OIS** — both legs ACT/365 (fixed), annual frequency at ≤3Y and SemiAnnual at ≥3Y per playbook conventions, T+1 settlement, 2 business day pay delay. Note that AUD entries in the playbook are schema-only without Bloomberg tickers as of v1.1 — coverage is forward-looking pending ticker resolution.
- **CAD CORRA OIS** — both legs ACT/365 (fixed), Annual frequency at ≤1Y and SemiAnnual at ≥2Y, T+1 settlement, 1 business day pay delay.

The day-count and payment-frequency differences across currencies are real and material: a 10Y SOFR rate is *not* directly comparable to a 10Y SONIA rate without a convention adjustment of typically a few bps, because they're computed under different ACT/X conventions and possibly different compounding frequencies on the fixed leg.

**Settlement and clearing.** Most institutional OIS trades are cleared at LCH SwapClear, CME, or Eurex (jurisdiction-dependent). Cleared trades benefit from CCP netting, which compresses gross notional and operationally simplifies risk management. Bilateral OIS still exists for bespoke structures but has been a shrinking share since 2010.

**Liquidity profile by tenor.** OIS liquidity is highest at the front end (3M-2Y, where central-bank-pricing trades concentrate) and at the canonical curve points (5Y, 10Y, 30Y, where bond-vs-swap and curve trades concentrate). Off-the-run tenors (e.g., 7Y, 12Y, 15Y, 25Y) trade with wider bid-asks and are typically interpolated rather than directly quoted. Our playbook covers 13 standard pillar tenors — sufficient for full curve construction via interpolation.

**Compounding mechanics on the floating leg.** The underlying RFRs (SOFR, ESTR, SONIA, TONA, AONIA, CORRA) are overnight fixings published once per business day by their respective administrators. In vanilla OIS, the floating coupon over an accrual period is computed by compounding those daily overnight fixings in arrears — meaning the rate for the period is only known at or near the end of the period — subject to currency-specific observation, lookback / lockout / observation-shift, rate-cutoff, and payment-delay conventions [BTRM SOFR construction notes; Clarus; ISDA Compounding/Averaging Supplement, 2021]. The exact mechanics (lookback vs observation-shift vs lockout) vary by jurisdiction and product type and should not be hard-coded from prose. Term-RFR rates (Term SOFR, Term SONIA) exist as supplementary forward-looking measures derived from RFR derivatives, but vanilla OIS uses backward-looking compounded RFR.

## 5. Key metrics PMs watch

Per curve in scope:

- **OIS rates by benchmark tenor:** 1W, 1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y. The 2Y, 5Y, 10Y, 30Y are the canonical "headline" points.
- **OIS curve spreads:** 2s10s, 5s30s, 2s5s, 5s10s — same set as sovereign, with cleaner interpretation.
- **OIS butterflies:** 2s5s10s, 5s10s30s — to detect belly richness/cheapness in the OIS expected-path curve.
- **OIS forward rates:** 1M1M, 3M3M, 6M6M, 1Y1Y, 2Y1Y, 5Y5Y. The 5Y5Y in particular is the canonical long-run neutral / structural-rate watchpoint.
- **Per-meeting policy pricing:** decomposition of the OIS strip into per-meeting hike/cut probabilities given the central bank's meeting calendar. This is the single most-watched OIS-derived metric on a rates desk, especially in the 1-3 meetings ahead.
- **Cross-currency OIS spreads:** SOFR-ESTR, SOFR-SONIA, SOFR-TONA, SOFR-AONIA, SOFR-CORRA, ESTR-SONIA at canonical tenors (typically 2Y for policy-cycle divergence, 10Y for long-cycle).
- **OIS-vs-sovereign spreads:** ASW, swap-spread, bond-vs-OIS at matched tenor — owned jointly with `01_sovereign_bonds.md`.
- **LIBOR/term-rate-vs-OIS analogues:** Term SOFR-vs-SOFR-OIS, FRA-OIS in jurisdictions where FRA still trades — funding stress indicators.
- **Z-scores and percentiles:** typically 252-day window for stability; 60d/126d for tactical signals.
- **Period changes:** 1d, 5d, 22d, 63d, 252d, YTD.

## 6. Standard workflows

The workflows below are the recurring units of analysis on a rates desk that involve OIS data. Each maps to a workflow file in `/workflows/` (or "TBD" where we haven't written it).

- **Morning OIS curve check** — pull rates, period changes, z-scores across all six curves. → `workflows/morning_briefing.md`
- **Central bank pricing read** — what's risk-neutrally priced for the next FOMC/ECB/BoE/BoJ/RBA/BoC meeting; per-meeting decomposition; terminal-rate identification. → `workflows/central_bank_pricing.md` (when written)
- **Policy repricing since event** — how much has the OIS-implied policy path moved since CPI / payrolls / FOMC / ECB / etc.? Pre/post comparison of per-meeting expected change and terminal rate. Probably the second-most-asked desk metric after raw meeting pricing. → TBD
- **OIS vs STIR futures reconciliation** — OIS-implied path vs SOFR / SONIA / Euribor / SARON futures-implied path. Desks often look at futures for liquidity and OIS for cleaner OTC path extraction; reconciling them is a regular cross-check. → cross to `04_bond_futures.md` and the eventual STIR-futures coverage when written.
- **Cross-CB divergence trades** — SOFR-ESTR, SOFR-SONIA, SOFR-TONA, etc., expressing relative policy-cycle views. → `workflows/relative_value.md`
- **Curve trades on OIS** — steepeners, flatteners, butterflies on a single OIS curve. → TBD
- **OIS forward expression of macro views** — e.g., "fade 5Y5Y SOFR back to long-run neutral if r-star is X" — using forward rates rather than spot rates as the trade vehicle. → TBD
- **ASW / bond-vs-swap RV** — joint with sovereign bonds. The OIS curve provides the discount curve and the par swap rates that anchor ASW computation. → cross to `01_sovereign_bonds.md`
- **Funding-stress monitor (modern)** — the post-LIBOR analogue is not one clean replacement: Term-RFR-vs-OIS where liquid (Term SOFR, Term SONIA), FRA-OIS, repo-vs-OIS, bank CP/CD spreads, and XCCY basis all carry different information about different funding channels. The workflow tracks them jointly. → TBD
- **Curve regime classification on OIS** — bull/bear × steepener/flattener/twist on the OIS curve. → `workflows/regime_classification.md`
- **Forward rate decomposition into expected-rate vs term-premium components** — Bucket 2 work, only meaningful at long tenors. → TBD
- **Stress / historical replay** — apply historical OIS path (e.g., 2022 hiking cycle, 2008 LIBOR-OIS blowout, ESTR transition) to current OIS positions. → TBD

## 7. Models and methodologies

Each model below classified by bucket, with reference. Specific tool names deferred to Section 9.

**OIS rate / curve spread / butterfly / cross-market spread** (1A given fixed weights and conventions) — basic arithmetic on quoted OIS rates. [Tuckman, Ch. 18]

**OIS forward rate** (1A as par-rate approximation with fixed annual compounding for >1Y; 1B with parameterized convention or using bootstrapped discount factors) — bootstrap forward from two par rates approximates the answer at standard pillars but does not handle stub periods, off-pillar windows, or convention mismatches cleanly. The exact computation comes from discount factors built by curve bootstrapping. [Tuckman, Ch. 18; BTRM 2020; Bianchetti & Scaringi 2025]

**Discount factor / zero curve bootstrap** (1B) — strip the par OIS curve into a zero curve via standard bootstrap with a chosen interpolation method (linear, log-linear, monotone cubic spline). Interpolation choice, curve pillars, calendars, convexity handling, payment delays, compounding mechanics, and bootstrap methodology are all assumptions — this is why it's 1B, not 1A. The output discount curve is the input to nearly every other Bucket 1B/2 tool that uses OIS. [BTRM SOFR construction; Tuckman, Ch. 19]

**Z-score and percentile rank** — 1A with default 252d window; 1B with custom window/method.

**Curve regime classification** (1B deterministic heuristic) — rule-based labeling with thresholds and labels that are convention-driven diagnostics, not market truths. Even with fixed default thresholds, classification involves subjective category boundaries; classified 1B rather than 1A on the discipline that "fixed defaults" alone don't make a tool 1A.

**Per-meeting policy pricing decomposition** (1B) — given a central bank meeting calendar and the OIS strip, decompose forward rates into per-meeting *risk-neutral implied* policy changes. Parameter choices: turn-of-year/quarter-end adjustments, holiday calendar, terminal rate assumption, stub-period handling, mapping from overnight fixings to policy target rate. The "Fed turn" effect (year-end funding pressure raising the spot rate over the turn) is a known nuisance that requires explicit handling. The output is risk-neutral implied probabilities, not real-world probabilities — the wedge between them is a term-premium-and-liquidity story that the tool surfaces as an assumption, not a hidden adjustment.

**LIBOR-OIS / FRA-OIS / Term-RFR-OIS spread** (1A given inputs, 1B if inputs include term-RFR rates that need their own bootstrap) — funding-stress proxy. Each variant captures a different funding channel; no single one is "the" modern LIBOR-OIS replacement. [BIS]

**Cross-currency OIS basis** (cross-domain) — relates to XCCY basis swaps, owned by `07_cross_currency_basis.md`. The OIS rates are inputs; the basis-swap quotes are the output of that module.

**Beta-adjusted cross-CB RV** (1B if used as a descriptive hedge-ratio tool; closer to 1B/2 boundary if used as a fair-value/residual signal) — rolling regression of one OIS series on another, returning residual hedge ratio. [Veronesi, Ch. 18]

**Cointegration tests on OIS pairs** (Bucket 2 in this taxonomy) — Engle-Granger, Johansen on cross-CB OIS pairs. Particularly applicable to short-end OIS (1Y, 2Y) pairs where the policy-cycle linkage produces strong cointegration in normal times. Classified 2 because it is statistical inference / model-estimation, not commodity deterministic analytics, even though the computation is deterministic given parameters. [Hamilton 1994]

**Half-life of mean reversion (OIS spreads)** (Bucket 2) — Ornstein-Uhlenbeck fit to OIS-spread series. Same classification reasoning as cointegration: it is statistical model fitting.

**Curve fitter (OIS)** (1B) — Nelson-Siegel / NSS / cubic spline on OIS pillar rates; useful both for off-pillar interpolation and for identifying rich/cheap OIS pillar points. [Nelson-Siegel 1987; Svensson 1994]

**Parametric scenario / historical replay** (1B) — apply chosen shock vectors or historical OIS paths to OIS positions.

**PCA on OIS curve** (2) — principal components of OIS-rate changes. Yields cleaner level/slope/curvature factors than sovereign PCA because there's less idiosyncratic supply/credit noise. [Litterman-Scheinkman 1991]

**HMM regime classifier on OIS / cross-CB features** (2) — fit a Gaussian HMM on a feature vector mixing OIS levels, slopes, vol, and cross-CB spreads. [Hamilton 1989; Rabiner 1989]

**Term premium decomposition on OIS** (2) — ACM-style on the OIS curve. The residual after subtracting estimated expected rates is an *estimated OIS term-premium / model residual*, cleaner than a sovereign-yield residual because there's no fiscal/supply confound, but still model-dependent and not directly observable. Different specifications (sample period, factor count, identification choices) can produce materially different term-premium time series [Federal Reserve, *Robustness of long-maturity term premium estimates*, 2017]. [ACM 2013]

**Dynamic Nelson-Siegel via Kalman filter on OIS** (2) — same machinery as on sovereign, applied to OIS. [Diebold-Li 2006]

**GARCH on OIS rate changes** (2) — conditional vol forecasting at chosen tenors, useful both for vol-targeted sizing and as input to other models. [Bollerslev 1986]

**Multi-currency OIS PCA** (2) — joint cross-section across all six curves × multiple tenors; identifies global rate factors and per-CB residuals. [Cross-domain RV foundation]

**Out of scope (deliberately):** trade signal generation, position sizing, bilateral CSA / collateral discounting math (XVA territory).

## 8. Data requirements

This section is a forward-looking specification of what data each tool in the OIS domain needs. For the current ingestion contract, see `/playbooks/ois.yml`.

### 8.1 Data fields referenced

**Time-series fields (per ticker, per business day):**

- `quoted_rate_mid` — closing mid OIS rate (Bloomberg `PX_LAST`)
- `quoted_rate_bid` / `quoted_rate_ask` — closing bid / ask OIS rate
- `bid_ask_spread` — derived from bid/ask
- `volume` — daily traded notional (where available; Bloomberg coverage is partial)
- `realized_vol` — rolling realized volatility of OIS-rate changes (computable from `quoted_rate_mid`)

**Reference / static fields (per ticker):**

- `security_name` (Bloomberg `SECURITY_DES`)
- `maturity_date` (Bloomberg `MATURITY`)
- `effective_date` (Bloomberg `SW_EFF_DT`)
- `underlying_index` (Bloomberg `SWAP_PRIMARY_INDEX`) — e.g., SOFRRATE, ESTRON, SONIO/N, TONAR, RBACOR, CORRA
- `calendar_code` (Bloomberg `CALENDAR_CODE`)
- `curve_family` — e.g., USD_SOFR_OIS
- `tenor` — pillar tenor label (1W, 1M, ..., 30Y)
- `bloomberg_ticker` / `composite_ticker` — full Bloomberg identifier
- `quote_source` — pricing source (Bloomberg composite vs specific dealer feed)
- `quote_type` — par swap rate, zero rate, forward, basis, vendor-interpolated
- `quote_convention` — annualized simple / compounded / zero / par
- `discounting_display` — "OIS" (constant in our universe but kept for schema generality)
- `settlement_convention_display` — T+0, T+1, T+2 per currency
- `first_live_date` — the date from which this ticker has live (not vendor-proxied) quotes
- `vendor_backfill_flag` — boolean: is pre-`first_live_date` history vendor-constructed?
- `proxy_lineage` — for pre-transition history: true RFR quote vs EONIA/EFFR/CDOR-equivalent proxy
- `bid_ask_availability_flag` — boolean: are bid/ask quotes populated for this ticker?
- `pillar_status` — directly quoted vs interpolated pillar
- Fixed-leg conventions: `fixed_leg_day_count`, `fixed_leg_pay_frequency`, `fixed_leg_business_day_adjustment`, `fixed_leg_adjust`, `fixed_leg_roll_convention`, `fixed_leg_calc_calendar_display`, `fixed_leg_pay_delay`
- Floating-leg conventions: `float_leg_day_count`, `float_leg_pay_frequency`, `float_leg_reset_frequency`, `float_leg_business_day_adjustment`, `float_leg_adjust`, `float_leg_roll_convention`, `float_leg_calc_calendar_display`, `float_leg_fixing_calendar`, `float_leg_accretion_mode`, `float_leg_fixing_lag`, `float_leg_pay_delay`, `float_leg_rate_cutoff`, `float_leg_reset_position`, `float_leg_lookback`, `float_leg_lockout`, `float_leg_observation_shift`
- `collateral_csa` / `discounting_convention` — the discount curve / collateral convention assumed for valuation (not always stored at ticker level; may be domain-shared)
- `clearing_venue` — LCH SwapClear, CME, Eurex, etc., where applicable

The data-lineage fields (`first_live_date`, `vendor_backfill_flag`, `proxy_lineage`) are *not currently in the playbook* and are an explicit roadmap addition. They matter because every Bucket 2 model fitting on the playbook's 2005-onwards history will hit transition regime breaks (ESTR-EONIA in 2019, SOFR-EFFR in 2020, post-YCC TONA in 2024, post-CDOR CORRA in 2024). Without lineage flags, models silently mix proxy and live data.

**External / cross-domain inputs:**

- Central bank meeting calendar — date, scheduled meeting flag, expected announcement window. Per central bank in scope (Fed, ECB, BoE, BoJ, RBA, BoC). Owned by `03_money_markets_and_cb_pricing.md` (when written) or treated as a domain-shared static dataset.
- Daily overnight RFR fixings — published by the central bank or its administrator (NY Fed for SOFR, ECB for ESTR, BoE for SONIA, BoJ for TONA, RBA for AONIA, BoC for CORRA). Useful for back-filling float-leg accruals and for verification of OIS prints near meeting dates.
- FX spot rates and FX forward points (cross-currency basis quotes) — owned by `07_cross_currency_basis.md`.
- Sovereign yield curves — owned by `01_sovereign_bonds.md`. Input to ASW, swap-spread, bond-vs-OIS analyses.
- MOVE index / swaption vol surface — owned by `06_swaptions_and_rates_vol.md`.

### 8.2 Per-tool data requirements

Format: `tool_name` — primary fields needed; key universe / history needs; cross-domain dependencies (if any).

#### Bucket 1A — built

**`rate_level`** — `quoted_rate_mid`; per (curve_family, tenor, date); 252+ business days history for z-score.

**`curve_spread`** (OIS) — `quoted_rate_mid` at two tenors of the same curve_family; full daily history.

**`forward_rate`** — `quoted_rate_mid` at two tenors of the same curve_family; uses annual compounding for >1Y and a chosen short-end convention for ≤1Y. Currently fixed at annual compounding for >1Y per system design.

**`cross_market_spread`** (OIS) — `quoted_rate_mid` at the same tenor across two curve_families; full daily history; both curves' calendars (for synchronizing across non-overlapping holiday calendars).

**`scanner`** (OIS) — `quoted_rate_mid` across the full (curve_family × tenor) universe; 252+ business days for z-score per series.

#### Bucket 1A — planned

**`butterfly`** (OIS) — `quoted_rate_mid` at three tenors of the same curve_family; default 50-50 weights.

**`libor_ois_or_sofr_term_spread`** — `quoted_rate_mid` plus an external term-RFR series (Term SOFR, Term SONIA, etc.) at matched tenor; spread + z-score + time series.

#### Bucket 1B — planned

**`zero_curve_bootstrap`** — full pillar grid of `quoted_rate_mid` for one curve_family at a single date; reference fields for fixed-leg pay frequency, day count, payment delay, and calendar. Outputs the zero / discount curve at all tenors via a chosen interpolation (linear, log-linear, monotone cubic spline). Interpolation choice, pillar selection, payment-delay handling, and bootstrap methodology are all assumptions — hence Bucket 1B, not 1A. Foundational for downstream tools — ASW, DV01 for OIS, exact (not approximation) forward rates, parametric scenarios, and term-premium models all depend on it.

**`zscore_custom`** — any series; no new fields.

**`butterfly_weighted`** (OIS) — for DV01-weighted: needs DV01 per leg, which requires the zero curve (cross-dependency on `zero_curve_bootstrap`). For PCA-weighted: needs PCA loadings (`pca_ois_curve`, Bucket 2).

**`curve_regime`** (OIS) — same data as the sovereign-bond curve_regime, applied to OIS. `quoted_rate_mid` across short-end and long-end tenors of the same curve_family, with rolling 1d/5d/22d windows. Uses fixed default thresholds; classified 1B (rather than 1A) on the discipline that thresholds and labels are subjective even when fixed.

**`curve_regime_parameterized`** (OIS) — same as `curve_regime` with PM-overridable thresholds.

**`forward_rate_parameterized`** — `quoted_rate_mid`, with parameter selection of the forward window (start tenor, end tenor, custom start/end dates), compounding convention (simple, linear, annual, continuous), and convention selection by regime (e.g., switch between simple and compounded at 1Y). The bootstrapped-discount-factor variant (`forward_rate_curve_exact`) builds on `zero_curve_bootstrap` for stub-period and off-pillar precision.

**`per_meeting_pricing`** — `quoted_rate_mid` across the front-end OIS strip (out to ~2Y typically), plus the central bank's meeting calendar (cross-domain), and a chosen turn-of-year/quarter adjustment policy. Outputs per-meeting *risk-neutral implied* policy changes (in bps) and probabilities, and the implied terminal rate. The single highest-value 1B build for an OIS module.

**`policy_path_since_event`** — front-end `quoted_rate_mid` strip on two reference dates (pre-event, post-event), the central bank's meeting calendar, and the same adjustment policy as `per_meeting_pricing`. Returns the per-meeting and terminal-rate change in implied policy pricing between the two dates. Probably the second-most-asked desk metric after raw meeting pricing — answers "how much did the Fed/ECB path move on CPI / payrolls / FOMC."

**`ois_vs_stir_futures_basis`** — `quoted_rate_mid` for OIS plus STIR futures prices (SOFR, SONIA, Euribor, SARON futures) at matched tenor windows. Returns the basis between OIS-implied and futures-implied policy paths. Cross-dependency on the eventual STIR-futures coverage in `04_bond_futures.md` or a future dedicated STIR module.

**`curve_fitter_ois`** — full pillar grid of `quoted_rate_mid` for one curve_family at a date; fits NS/NSS/spline. Returns fitted parameters, fitted curve at all tenors, per-pillar residuals.

**`beta_adjusted_ois_spread`** — paired `quoted_rate_mid` at chosen tenors across two curve_families; rolling regression with chosen window. Returns rolling beta, residuals, residual z-score, hedge ratio. Classified 1B as a descriptive hedge-ratio tool; if used as a fair-value/residual signal it sits closer to the 1B/2 boundary.

**`rolling_regression`** — generic; OIS series as input, OIS or any other series as regressors.

**`parametric_scenario`** (OIS) — position spec with DV01s plus a shock vector; revalues using the bootstrapped zero curve. Cross-dependency on `zero_curve_bootstrap`.

**`historical_replay`** (OIS) — position spec plus historical window; replays OIS-rate path. Deterministic given (window, position, interpolation).

**`asset_swap_spread`** — joint with sovereign bonds; lives in `01_sovereign_bonds.md` Section 9 but consumes `swap_curve_par` and `swap_zero_curve` from this module.

#### Bucket 2 — planned

**`pca_ois_curve`** — full pillar `quoted_rate_mid` for one curve_family; lookback 1-10Y configurable. Returns loadings, factor time series, variance explained, current factor levels.

**`hmm_ois_regime`** — feature vector configurable: typically OIS-rate changes at 2Y/10Y, slope changes, realized vol, optionally MOVE (cross-domain), optionally cross-CB OIS spreads. Lookback 5+ years.

**`cointegration_test`** — paired `quoted_rate_mid` series; lag specification, test choice (Engle-Granger or Johansen); returns p-value, long-run equilibrium, residual series. Particularly applied to cross-CB OIS pairs in the front end (1Y, 2Y) where policy-cycle linkages produce strong cointegration in normal regimes. Classified Bucket 2 because it is statistical inference — deterministic given parameters but produces estimated relationships, not commodity analytics.

**`half_life_ou`** — single OIS-spread series; Ornstein-Uhlenbeck fit; returns half-life in days plus current deviation from long-run mean. Same Bucket-2 classification reasoning as cointegration: statistical model fitting.

**`term_premium_ois_acm`** — ACM-style affine model on OIS curve. Returns *estimated* OIS term-premium and expected-short-rate components per tenor over time. Classified 2 because the decomposition is model-dependent: estimates can vary materially by specification, sample, and identification choices [Federal Reserve, *Robustness of long-maturity term premium estimates*, 2017]. Cleaner than a sovereign-yield analogue because there's no fiscal/supply confound, but the residual is still a model output, not a directly observable quantity. Long history needed (10+ years preferred). Pre-2019 ESTR, pre-2020 SOFR, pre-2024 CORRA, and pre-2024 TONA data are transition/proxy regimes — see Section 3 historical-episodes for lineage caveats. Regime breaks must be handled (regime dummies, sample restriction, or regime-conditional fitting).

**`dynamic_nelson_siegel_ois`** — Kalman-filter NS on OIS; long history needed.

**`garch_ois_vol`** — `quoted_rate_mid` series at chosen tenor; GARCH model order; long history.

**`probabilistic_stress_ois`** — depends on a fitted regime model plus position spec; generates curve-path distribution.

**`multi_currency_ois_pca`** — full pillar grid across all six curve families; identifies global OIS factors and per-CB residuals. Calendar synchronization is the key engineering challenge — six different holiday calendars must align cleanly, typically by intersecting business days or by resampling each series to a common calendar with forward-fill.

#### Aspirational (data-blocked or further out)

**`xccy_basis_aware_cross_market_spread`** — would integrate the FX basis to produce an FX-hedged OIS-rate-pickup metric. Requires XCCY basis data (from `07_cross_currency_basis.md`).

**`swaption_implied_terminal_rate`** — uses swaption surface to extract risk-neutral distribution of terminal rate. Cross-domain with `06_swaptions_and_rates_vol.md`.

**`policy_path_decomposition_advanced`** — beyond simple per-meeting decomposition, fits a structural model (e.g., shadow rate model, taylor-rule-conditional) to decompose forwards into "rule-implied path" + "market deviation." Requires macroeconomic data (inflation, unemployment, etc.) — not currently a defined domain.

### 8.3 Cross-domain data dependencies summary

- Sovereign yields → owned by `01_sovereign_bonds.md`. Input to ASW, swap-spread, bond-vs-OIS.
- Central bank meeting calendars → owned by `03_money_markets_and_cb_pricing.md` (when written), or shared static reference data. Critical input to `per_meeting_pricing`.
- XCCY basis quotes, FX spot/forwards → owned by `07_cross_currency_basis.md`. Input to FX-hedged OIS metrics.
- Swaption vol surface, MOVE → owned by `06_swaptions_and_rates_vol.md`. Input to vol-conditional OIS analysis.
- Term-RFR fixings (Term SOFR, Term SONIA) → not yet ingested. Input to LIBOR-OIS-modern-analogue spreads.
- Daily overnight RFR fixings (SOFR, ESTR, SONIA, TONA, AONIA, CORRA) → not yet ingested. Useful for verification and for short-window forward computation across meeting dates.

## 9. Tool inventory

Format: **`tool_name`** — takes [inputs], runs [model/computation], returns [outputs], used in [workflows] to surface [macro implication].

### Built (Bucket 1A)

**`rate_level`** — takes a curve_family, tenor, and date; queries the daily rates table; returns OIS rate level, 1d/5d/22d changes in bps, 252d high/low/percentile, and 252d z-score; used in `morning_briefing` and any directional analysis to surface where an OIS rate sits relative to its recent distribution.

**`curve_spread`** (OIS) — takes a curve_family and two tenors; computes the OIS rate differential time series; returns current spread, period changes, 252d z-score, and time series; used in `morning_briefing` and curve-trade workflows to surface OIS curve shape (cleanly, without supply/credit noise).

**`forward_rate`** — takes a curve_family, a forward window (start/end tenors, or custom dates), and the rates at the relevant pillar points; computes the implied forward rate using annual compounding for >1Y windows; returns the forward rate, period changes, z-score, time series; used in `central_bank_pricing` (for terminal-rate proxies like 1Y1Y, 2Y1Y, 5Y5Y) and forward-trade workflows to surface market-implied policy expectations at chosen horizons.

**`cross_market_spread`** (OIS) — takes two (curve_family, tenor) pairs; computes the OIS-rate differential time series; returns current spread, period changes, 252d z-score; used in `relative_value` and `morning_briefing` to surface cross-CB policy-cycle divergence (e.g., SOFR-ESTR 2Y as the canonical Fed-vs-ECB front-end-cycle divergence metric).

**`scanner`** (OIS) — takes a z-score threshold (default 2.0) and tenor universe; iterates `rate_level` across the full (curve_family, tenor) grid; returns ranked list of extremes with sign and magnitude; used in `morning_briefing` and idea-generation to surface where OIS markets are showing statistical stress.

### Planned (Bucket 1A)

**`butterfly`** (OIS) — takes a curve_family and three tenors with optional weights (default 50-50); computes the OIS curvature spread; returns current value, period changes, 252d z-score, per-wing decomposition; used in OIS curve-trade workflows to surface belly richness/cheapness in the risk-neutral expected-policy-path curve.

**`libor_ois_or_sofr_term_spread`** — takes a term-RFR series (Term SOFR or Term SONIA) and the matched-tenor OIS rate; computes the spread time series; returns spread, z-score, period changes; used in funding-stress monitoring as one component of the modern post-LIBOR funding-stress picture (the original 3M USD LIBOR-OIS peaked at 364bp on 10 October 2008 — no single modern metric carries the same information; Term-RFR-vs-OIS is one channel, FRA-OIS, repo-OIS, bank CP/CD spreads, and XCCY basis are others).

### Planned (Bucket 1B)

**`zero_curve_bootstrap`** — takes the pillar grid of OIS rates for a curve_family at a date plus the curve's reference conventions (day-count, payment frequency, payment delay, calendar) and a chosen interpolation method (linear, log-linear, monotone cubic spline default); strips into a zero curve; returns discount factors and zero rates at all tenors; foundational for downstream tools — ASW, DV01 for OIS, exact (not approximation) forward rates, parametric scenarios, and term-premium models all depend on it. Classified 1B because interpolation choice, pillar selection, payment-delay handling, and bootstrap methodology are assumptions.

**`zscore_custom`** — takes a series, lookback window, and standardization method; computes z-score; used as a building block.

**`butterfly_weighted`** (OIS) — takes a curve_family, three tenors, and a weighting scheme (50-50, DV01-weighted, PCA-weighted); computes appropriately weighted OIS curvature; returns the curvature plus weighting metadata.

**`curve_regime`** (OIS) — takes a curve_family and lookback windows; classifies the OIS curve move as BULL_STEEPENER, BEAR_FLATTENER, BULL_FLATTENER, BEAR_STEEPENER, PARALLEL_SHIFT, TWIST against fixed default thresholds; returns classification per window; used in `regime_classification` and `morning_briefing` to surface what kind of move drove today's OIS action — and to compare against the sovereign curve regime as an informal expected-path-vs-term-premium decomposition. Classified 1B because labels and thresholds are subjective even when fixed by default.

**`curve_regime_parameterized`** (OIS) — same as `curve_regime` with PM-overridable thresholds.

**`forward_rate_parameterized`** — takes a curve_family, custom forward window (any start/end date or tenor pair), and compounding convention (simple, linear, annual, continuous, or auto-switch by regime); computes the implied forward; returns the forward rate with metadata on convention used; used when default convention doesn't fit (e.g., short-window forwards across meeting dates need money-market simple, long-window forwards need annual compounding). Builds approximation-quality forwards from pillar par rates. The exact-curve sibling (`forward_rate_curve_exact`) is a Bucket 1B tool that depends on `zero_curve_bootstrap` for stub-period and off-pillar precision.

**`per_meeting_pricing`** — takes a curve_family, the central bank's meeting calendar, and explicit adjustment parameters (turn-of-year/quarter effects, terminal-rate assumption, stub-period handling, mapping from overnight fixing to policy target rate); decomposes the front-end OIS strip into per-meeting *risk-neutral implied* policy changes (in bps) and probabilities; returns per-meeting implied change, cumulative implied path, terminal rate at chosen horizon, plus the assumption set used; used in `central_bank_pricing` workflows to surface what the OIS market is pricing for the next meetings — *the single most-asked OIS metric on a macro desk*. The output is risk-neutral implied, not real-world expected; the wedge between them is term-premium-and-liquidity and is surfaced as an explicit assumption rather than absorbed silently.

**`policy_path_since_event`** — takes a curve_family, two reference dates (pre-event, post-event), the central bank's meeting calendar, and the same adjustment policy as `per_meeting_pricing`; computes per-meeting and terminal-rate change in implied policy pricing between the two dates; returns the path-difference profile and total terminal-rate move; used in CB-pricing workflows for "how much has the Fed/ECB path moved since CPI / payrolls / FOMC?" — typically the second-most-watched metric after raw meeting pricing.

**`ois_vs_stir_futures_basis`** — takes OIS rates and matched-tenor STIR futures prices (SOFR / SONIA / Euribor / SARON futures) for a chosen window; computes the basis between OIS-implied and futures-implied policy paths; returns the basis time series and z-score; used in OIS-vs-futures reconciliation workflows. Cross-dependency on the eventual STIR-futures coverage in `04_bond_futures.md` or a future dedicated STIR module.

**`curve_fitter_ois`** — takes the full pillar grid of OIS rates and a model choice (NS, NSS, cubic spline); fits the chosen model; returns fitted parameters, fitted curve at all tenors, per-pillar residuals; used in OIS-rich/cheap workflows to surface mispriced pillar tenors and as a source of smooth interpolated rates for downstream tools.

**`beta_adjusted_ois_spread`** — takes paired OIS series at chosen tenors across two curve_families, regression window, and frequency; runs OLS; returns rolling beta, residuals, residual z-score, hedge ratio; used in cross-CB RV workflows where raw OIS-vs-OIS spreads are misleading because the two CBs have different policy responsiveness (e.g., the Fed and ECB don't have a 1:1 relationship, and a beta-adjusted SOFR-ESTR captures the divergence signal better than the raw spread).

**`rolling_regression`** — generic primitive; takes OIS target series and any regressors; rolling OLS.

**`parametric_scenario`** (OIS) — takes a position spec (instruments and DV01s) plus a shock vector (parallel shift, twist, custom curve shock); revalues using the bootstrapped OIS zero curve from `zero_curve_bootstrap`; returns P&L by instrument and total.

**`historical_replay`** (OIS) — takes a position spec and historical window (e.g., 2022-01-01 to 2023-06-01 for the Fed hiking cycle); replays OIS-rate path; returns P&L path, drawdown, vol; used in stress-testing workflows. Deterministic given (window, position, interpolation).

### Planned (Bucket 2)

**`pca_ois_curve`** — takes a curve_family, tenor universe, and lookback window; computes principal components of OIS-rate changes; returns loadings, factor time series, variance explained, current factor levels; used in attribution and scenario workflows; particularly clean factor structure on OIS because no supply/credit confound. Reference loadings for `butterfly_weighted` (PCA-weighted variant) and for cross-domain attribution analogues.

**`hmm_ois_regime`** — takes a curve_family or multi-curve set, feature vector (OIS levels/changes, slopes, realized vol, optionally MOVE), N states, lookback; fits Gaussian HMM; returns state probability series, transition matrix, current state, per-state feature distributions; used in regime classification workflows. 5+ years history preferred.

**`cointegration_test`** — takes paired or basket of OIS series (typically front-end OIS like 1Y or 2Y across CBs), lag spec, test choice (Engle-Granger or Johansen); returns p-value, equilibrium relationship, residual; used in cross-CB RV strategy development to surface which OIS pairs are statistically mean-reverting. Classified Bucket 2 because it produces statistical inference, not commodity analytics, even though deterministic given parameters.

**`half_life_ou`** — takes an OIS-spread series (cross-CB or cross-tenor); fits Ornstein-Uhlenbeck; returns half-life in days and current deviation; used in RV workflows to size and time mean-reversion trades. Same Bucket-2 reasoning as cointegration.

**`term_premium_ois_acm`** — takes a curve_family and its OIS history; estimates ACM-style affine model on OIS specifically; returns *estimated* term-premium and expected-short-rate components per tenor over time; used in term-premium decomposition workflows. The OIS analogue is cleaner than a sovereign analogue because there's no fiscal/supply confound — but the residual is still a model-dependent estimate of OIS term-premium, not a directly observable quantity. Different specifications can produce materially different term-premium time series.

**`dynamic_nelson_siegel_ois`** — takes curve_family OIS history; estimates Diebold-Li dynamic NS via Kalman; returns latent level/slope/curvature factors; used in forecasting and curve-shape analysis.

**`garch_ois_vol`** — takes OIS-rate series at chosen tenor and model order; estimates conditional vol; returns vol forecast; used in vol-targeted sizing and vol-regime workflows.

**`probabilistic_stress_ois`** — takes a position spec and a fitted regime model; generates OIS-curve-path distribution; returns P&L distribution (VaR, ES, stress quantiles).

**`multi_currency_ois_pca`** — takes the full multi-CB OIS panel; computes joint cross-section PCA; returns global OIS factor loadings and per-CB exposures; used in cross-CB RV to identify common factors and idiosyncratic country residuals. Calendar synchronization across six different business-day calendars is the critical engineering challenge.

### Aspirational (data-blocked or further out)

**`xccy_basis_aware_cross_market_spread`** — would integrate XCCY basis quotes (cross-domain from `07_cross_currency_basis.md`) to produce an FX-hedged OIS-rate-pickup metric. Specifically, the metric "what does a JPY investor actually earn (in JPY terms) holding USD SOFR, after FX-hedging," which is the right cross-CB RV metric for institutional flow analysis.

**`swaption_implied_terminal_rate`** — would use swaption volatility surface data (from `06_swaptions_and_rates_vol.md`) to extract the risk-neutral distribution of the terminal rate, complementing the deterministic point-estimate from `per_meeting_pricing`.

**`policy_path_decomposition_advanced`** — would decompose forwards into "Taylor-rule-implied path" + "market deviation from rule," requiring inflation/unemployment/output-gap data we don't currently treat as a domain.

## 10. Open questions

These are things to validate at Brevan or with the advisory firm. Each tagged.

- *#parameter-default* — Default z-score lookback is 252d. As with sovereign, validate whether 60d/126d is more common for tactical OIS signals (especially around CB meetings).
- *#convention* — *EUR ESTR OIS payment delay discrepancy.* Our playbook encodes 1 business day; OpenGamma Strata's standard convention is 2 business days for EUR_FIXED_1Y_ESTR_OIS. Reconcile against ISDA RFR Conventions table and Bloomberg `DAYS_TO_PAYMENT` field (or equivalent) per ticker before using pay-delay-sensitive analytics.
- *#convention* — *General convention reconciliation.* The playbook's day-count, payment-delay, and observation-shift fields come from Bloomberg ticker reference data and may differ from standard ISDA RFR Conventions. Build a one-time reconciliation pass that flags any per-ticker convention that differs from the ISDA/CCP-house default, document deltas, and store both as metadata.
- *#parameter-default* — Default `forward_rate` uses annual compounding for >1Y. Is the simple/money-market convention preferred for sub-1Y forwards on a typical macro desk, or do PMs always quote in compounded terms?
- *#parameter-default* — Default butterfly weights 50-50. PMs more often use DV01-weighted or PCA-weighted on OIS — confirm.
- *#convention* — Per-meeting decomposition: turn-of-year/quarter adjustments are needed but the practitioner consensus on how to handle them varies. What's standard practice — explicit "turn fixed-income" adjustment, or treat the turn as additional meeting-implied movement?
- *#convention* — When discussing 5Y5Y or 1Y1Y, do PMs prefer "percentage-rate forwards" (annual compounded) or "money-market simple forwards" as the quoted metric in informal conversation? Probably depends on horizon.
- *#workflow-validation* — How granular is per-meeting policy pricing on a typical desk? Is "4bps priced into the next meeting" precision sufficient, or do PMs decompose into per-meeting probability distributions (e.g., 75% probability of 25bp cut, 20% of hold, 5% of 50bp cut)?
- *#workflow-validation* — Cross-CB RV: how widely is beta-adjusted OIS-vs-OIS used vs raw spread as the trading signal? My guess: raw spread for headline display, beta-adjusted for sizing, cointegration for filter on which pairs to trade.
- *#workflow-validation* — How important is the LIBOR-OIS / Term-SOFR-vs-OIS funding-stress metric in current practice? It was canonical pre-LIBOR-cessation; is the term-SOFR analogue actually watched?
- *#workflow-validation* — How is the OIS curve regime classification used relative to the sovereign one — comparison ratio (term premium read), independent signal, or background context?
- *#scope* — JGB: TONA OIS is post-2024 a normal market; pre-2024 the YCC distortion suppresses 2Y-10Y rates. Do PMs treat pre-2024 data as essentially unusable for fitting current models, or apply regime-dependent discounts in HMMs and term-premium models?
- *#scope* — ESTR pre-October 2019 is synthetic/EONIA-equivalent. SOFR pre-October 2020 is Fed-Funds-based. Do PMs/analysts on macro desks routinely use the back-history with regime adjustments, or treat each transition as a hard cut?
- *#scope* — AUD AONIA: liquidity in AUD OIS is materially thinner than in the other five RFRs. Worth treating AUD as a different class of instrument for analytics (smaller universe, more interpolation), or analytically equivalent for our purposes?
- *#methodology* — For OIS curve fitting: is there a "house standard" methodology (NS, NSS, monotone spline) at major macro funds, or does it vary?
- *#methodology* — Is term-premium decomposition on OIS specifically (rather than on sovereign) actually used in practice? It's analytically cleaner but I don't know if PMs use it as-such or just look at OIS-vs-sovereign as the informal decomposition.
- *#methodology* — On `multi_currency_ois_pca`, calendar synchronization: is the standard practitioner approach to intersect business days (small loss of data, clean alignment) or to forward-fill (preserve series length, potential staleness on holidays)?

## 11. References

- *[ISDA]* — ISDA 2006 Definitions and Supplements 64, 74, 76, 80, 89, 91 covering RFR floating rate options for SOFR, ESTR, SONIA, TONA, SARON, CORRA, and others. Also *Adoption of Risk-Free Rates: Major Developments in 2020*.
- *[ARRC]* — Alternative Reference Rates Committee. Paced Transition Plan documents (2018-2023) and SOFR-related guidance.
- *[ECB Working Group]* — European Central Bank, Working Group on Euro Risk-Free Rates. Reports on EONIA-to-ESTR transition (August 2019, February 2020, March 2020, June 2020).
- *[Bank of Canada]* — Bank of Canada, *Methodology for calculating the Canadian Overnight Repo Rate Average (CORRA)*; transition material 2020-2024.
- *[BoE]* — Bank of England, transition to sterling risk-free rates from LIBOR; SONIA methodology.
- *[BoJ]* — Bank of Japan, *New Framework for Strengthening Monetary Easing* (September 2016, YCC introduction); March 2024 monetary policy decision (YCC and NIRP exit).
- *[RBA]* — Reserve Bank of Australia, Cash Rate Methodology, AONIA documentation.
- *[NY Fed]* — Federal Reserve Bank of New York. SOFR data and methodology pages; Treasury Term Premia data.
- *[LCH]* — LCH SwapClear. *Transition to SOFR discounting in SwapClear* (October 2020); related circulars.
- *[CME]* — CME Group. *SOFR Discounting Transition Process for Cleared Swaps*; CME OIS / SOFR contract specs.
- *[Eurex Clearing]* — Eurex Clearing circulars on EurexOTC Clear ESTR transition.
- *[BIS]* — Bank for International Settlements. *Beyond LIBOR: a primer on the new benchmark rates* (BIS Quarterly Review, March 2019); various working papers on benchmark transition and OIS-curve construction.
- *[Bianchetti & Scaringi 2025]* — Bianchetti, M., & Scaringi, M. (2025). *No Fear of Discounting: How to Manage the Transition from EONIA to ESTR.* arXiv 2503.06806.
- *[BTRM SOFR]* — Bank Treasury Risk Management. *SOFR OIS Pricing and Riskless USD Curve Construction* (Working Paper 15, December 2020). Useful practitioner reference for SOFR OIS mechanics, day-count handling, and curve construction.
- *[Clarus]* — Clarus Financial Technology. *SOFR Swap Nuances* and related blog posts on SOFR vs LIBOR conventions.
- *[OpenGamma Strata]* — OpenGamma's Strata library. `FixedOvernightSwapConventions` documentation. Useful reference for cross-currency OIS conventions.
- *[Tuckman]* — Tuckman, B., & Serrat, A. (2022). *Fixed Income Securities: Tools for Today's Markets* (4th ed.). Wiley. Chapters 18-19 for swap mechanics and curve bootstrapping.
- *[Hamilton 1989]* — Hamilton, J. D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle." *Econometrica*. Foundational regime-switching paper.
- *[Hamilton 1994]* — Hamilton, J. D. (1994). *Time Series Analysis*. Princeton University Press. Cointegration, Kalman filter, state-space models.
- *[Rabiner 1989]* — Rabiner, L. R. (1989). "A tutorial on hidden Markov models and selected applications in speech recognition." *Proceedings of the IEEE*, 77(2), 257–286.
- *[Litterman & Scheinkman 1991]* — Litterman, R., & Scheinkman, J. (1991). "Common Factors Affecting Bond Returns." *Journal of Fixed Income*, 1(1), 54–61.
- *[Nelson & Siegel 1987]* — Nelson, C. R., & Siegel, A. F. (1987). "Parsimonious Modeling of Yield Curves." *Journal of Business*, 60(4), 473–489.
- *[Svensson 1994]* — Svensson, L. E. O. (1994). "Estimating and Interpreting Forward Interest Rates: Sweden 1992-1994." NBER Working Paper No. 4871.
- *[ACM 2013]* — Adrian, T., Crump, R. K., & Moench, E. (2013). "Pricing the Term Structure with Linear Regressions." *Journal of Financial Economics*, 110(1), 110–138.
- *[Diebold & Li 2006]* — Diebold, F. X., & Li, C. (2006). "Forecasting the term structure of government bond yields." *Journal of Econometrics*, 130(2), 337–364.
- *[Bollerslev 1986]* — Bollerslev, T. (1986). "Generalized Autoregressive Conditional Heteroskedasticity." *Journal of Econometrics*, 31(3), 307–327.
- *[Veronesi]* — Veronesi, P. (2010). *Fixed Income Securities: Valuation, Risk, and Risk Management*. Wiley.
- *[Risk.net]* — Risk.net coverage of the SOFR/EFFR basis dynamics around the October 2020 CCP discounting transition.
- *[Macro Compass]* — Peccatiello, A. The Macro Compass. Practitioner blog with useful framing on OIS-vs-Treasury curve interpretation.
- *[Neuberger Berman]* — Neuberger Berman. *The Most Important Rate in the Whole Wide World* (5Y5Y SOFR / terminal rate framing).
