# Sovereign Bonds

**Status:** v1 — revised against external review on factual claims. To be further iterated against feedback from Brevan and advisory contacts.
**Scope:** US Treasuries, German Bunds (and Schatz/Bobl), UK Gilts, Japanese Government Bonds, French OATs, Italian BTPs, Canadian Government Bonds, Australian Commonwealth Government Bonds, Spanish Bonos.
**This module focuses on nominal fixed-rate benchmark sovereign bonds.** Bills, FRNs, and inflation-linked sovereigns (TIPS, linkers, OATi, BTPei) are covered in `03_money_markets_and_cb_pricing.md` and `05_inflation_linked.md` respectively.

---

## 1. What it is

Sovereign bonds are debt instruments issued by national governments to fund their fiscal operations. The instrument is a contractual claim on a stream of cash flows: typically periodic fixed coupon payments plus return of principal at maturity. Each bond is characterized by issuer, currency, maturity, coupon rate, day-count convention, and settlement convention. Yield is the market-implied internal rate of return on the bond's cash flows at a given price.

In scope for this module: US Treasuries (UST), German Bunds and the shorter-dated Schatz/Bobl, UK Gilts (UKT), Japanese Government Bonds (JGB), French OATs, Italian BTPs, Canadian Government Bonds (CAN), Australian Commonwealth Government Bonds (ACGB), and Spanish Bonos.

## 2. Why it exists / role in the system

Sovereign bonds serve four distinct economic functions, all of which matter for how a PM thinks about them.

**Government financing.** Governments issue bonds to fund deficits and roll maturing debt. Issuance follows a scheduled calendar (the US Treasury's quarterly refunding, the German Finanzagentur's annual schedule, etc.), but auction sizes, buyback programs, maturity-mix, and refunding strategy are policy choices that can change. The size and tenor mix of issuance materially affects supply-demand dynamics in the secondary market.

**Benchmark / safe-asset function.** Major sovereign curves are the benchmark duration curves for their domestic cash markets. USTs are the global dollar safe-asset benchmark; Bunds anchor the euro-area collateral and reference curve; Gilts, JGBs, ACGBs, GoCs, OATs, BTPs, and Bonos serve analogous domestic benchmark roles for their respective markets. These curves are not "risk-free" in the derivatives-discounting sense — they embed term premium, liquidity/convenience premia, fiscal/supply risk, collateral value, and (for some issuers) credit or redenomination premia. For collateralized derivatives, OIS/RFR curves replaced LIBOR-style discounting post-2008 [ISDA 2020; BIS]; sovereigns remain the core cash-market and macro-duration benchmarks but are not the standard derivatives discount curve. See `02_ois_swaps.md` for the OIS / RFR discounting framework.

**Reserve and foreign-demand channel.** Foreign investors are structurally important holders of major sovereign debt. As of late 2025, foreign investors held roughly one-third of marketable USTs; of that foreign total, official holdings (foreign central banks and governments) accounted for roughly 42% and private foreign investors for roughly 58% [US Treasury TIC; CRS RS22331; Federal Reserve Notes 2025]. Foreign official share has declined relative to foreign private over the past decade. For macro PMs, changes in reserve-management demand, FX-hedged yield pickup, and foreign-private appetite (especially Japanese lifers and reserve managers in advanced economies) all matter for long-end pricing. Bunds and JGBs play similar reserve-asset roles for the euro and yen blocs respectively, though at smaller scale than USTs.

**Collateral and HQLA.** Major DM sovereign bonds are central HQLA and collateral assets. Many qualify as Level 1 HQLA under Basel III LCR rules, subject to eligibility, risk-weight, currency, and jurisdictional treatment [BIS Basel framework]. Banks hold them for liquidity coverage, repo desks fund themselves against them, and central counterparties accept them as initial margin. The collateral function is what makes sovereign bonds distinct from any other instrument — they are simultaneously an investment, a unit of margin, and an instrument for raising overnight cash.

**Credit and fragmentation dimension: BTPs, Bonos, OATs.** Italy and Spain are not "credit-risky exceptions" against an otherwise pure risk-free universe. Rather, BTPs and Bonos carry a larger and more explicit euro-area sovereign-spread / fragmentation / ECB-backstop component than Bunds. France has increasingly traded with a fiscal/political risk premium since 2024 [Reuters, June 2024]. Gilts also carry fiscal-credibility risk, which the September-October 2022 episode demonstrated emphatically. The market treats these dimensions differently — peripheral spreads are the canonical "credit dimension" of euro sovereigns — but no major sovereign is genuinely free of fiscal-credibility risk at all times. JGBs are in their own analytical category because of the long YCC era (September 2016 to March 2024) and its ongoing unwind.

## 3. What its movements signal (macro context)

This is the most important section of this document. Yields don't move randomly; they encode expectations about the macro state. A PM's job is to read the encoding.

### The decomposition

A working decomposition for nominal sovereign yields is:

```
yield ≈ expected path of nominal short rates
      + term premium
      + liquidity / convenience / collateral premium
      + credit / fiscal / redenomination premium (where relevant)
      + tax / regulatory / segmentation effects (where relevant)
```

For US, German, UK, Japanese, Canadian, French, and Australian bonds in normal regimes, the first two components dominate the macro interpretation. The expected-short-rate component is what the OIS market also prices (with some basis); the term premium compensates investors for bearing duration risk — inflation risk, supply uncertainty, the chance that policy turns out different than expected. Decomposing observed yields into these components is a Bucket 2 problem (term premium models — see `term_premium_acm` below). For BTPs and Bonos, and increasingly for OATs in political/fiscal episodes, spread premia must be treated explicitly. For on-the-run USTs, Bunds, and Gilts in stress, the liquidity/convenience component can be decisive.

### Curve shape

The slope of the curve is a compressed signal, not a clean read. The same 2s10s level can mean different things depending on the driver. A steep curve can encode: expectations of higher future short rates from a low starting point (typical mid-recovery), term-premium expansion (e.g., supply concerns, inflation uncertainty), bear-flattening reversal, or bull-steepening at a recession bottom where the front end rallies more than the long end. A flat or inverted curve can encode: expected policy cuts, tight current policy, term-premium compression, QE/scarcity effects suppressing the long end, or regulatory safe-asset demand. The 2s10s spread is the canonical curve metric, but it is not a mechanical recession oracle — see below.

The 2s10s has inverted before several modern US recessions (1980, 1989, 2000, 2007, 2019). The 2022–2024 inversion was the longest on record (~25 months) and the second-deepest since the early 1980s, with a trough near −108bp [Reuters; FRED T10Y2Y; Deutsche Bank's Jim Reid]. Crucially, it has not been followed by a recession through the time of writing. This makes it an important live counterexample for any model using inversion as a recession signal. Several explanations have been proposed (locked-in low borrowing rates from the prior cycle, fiscal-impulse persistence, AI-driven productivity), but the episode is genuinely unresolved and warrants model humility. Butterflies (2s5s10s, 5s10s30s) are the canonical *curvature* metrics — they capture richness or cheapness of the belly independent of overall direction or slope.

### Cross-market spreads

UST-Bund 10Y is the canonical "US vs Europe macro divergence" metric, but it is not just about US-vs-Europe growth and policy. The spread also embeds: FX-hedged yield pickup for European investors buying USTs (and the cross-currency basis governing that hedge cost), Bund scarcity / collateral premium, relative QE/QT stance (the ECB and Fed run very different balance-sheet policies), fiscal issuance and supply, safe-haven demand episodes, and EUR fragmentation risk. Through the 2010s the spread varied widely — from near zero in early-decade safe-haven episodes (when Bunds rallied on euro-crisis flight-to-quality) to 250bp+ in the 2017-2019 ECB-QE / Fed-hiking divergence. It widened sharply again in 2022-2023 as the Fed front-ran the ECB on hiking, and has been compressing as the cycles converge.

BTP-Bund 10Y is the canonical Eurozone-stress / periphery metric, but it is not pure "credit risk." It is a composite of: Italian fiscal risk, euro-area fragmentation / redenomination tail risk, ECB-backstop credibility (TPI, OMT, PEPP-flexibility), domestic political risk, broad risk sentiment / vol regime, and technical issuance flows. Decomposing which component is moving on a given day matters for trade construction. OAT-Bund is a subtler signal — France isn't really "periphery" — but the spread has been increasingly driven by French political and fiscal news since 2024.

### Specific historical episodes worth knowing

These are the events you reach for when explaining what a number means.

**September-October 2022 UK gilt / LDI crisis.** The Truss government's "mini-budget" in September announced ~£45bn of unfunded tax cuts. Long-dated gilt yields rose violently in days [Bank of England working paper, *An anatomy of the 2022 gilt market crisis*]. UK pension funds running Liability-Driven Investment strategies faced collateral calls on their interest-rate hedges, were forced to sell gilts to meet margin, which pushed yields higher, which created more margin calls — a doom loop. The Bank of England intervened with a temporary financial-stability buy/sell tool on long-dated and index-linked gilts. This is the canonical recent example of how flow dynamics and balance-sheet plumbing can dominate fundamentals in sovereign markets, and why monitoring forced-seller dynamics matters.

**2011-2012 Eurozone crisis.** BTP-Bund 10Y spread blew out to roughly 500bp+ at peak [BIS]. Draghi's July 2012 "whatever it takes" speech, later institutionalized through OMT, marked the regime shift — it changed the expected policy backstop rather than mechanically compressing the spread. This is the canonical episode for understanding how peripheral sovereign spreads price redenomination/break-up risk distinct from credit risk per se, and how central-bank reaction-function shifts work as a regime variable.

**March 2020 Treasury market dysfunction.** Even USTs — the deepest market in the world — saw severe liquidity dysfunction. Bid-ask spreads widened by an order of magnitude, order-book depth collapsed, on-the-run/off-the-run and cash/futures relationships dislocated, and levered relative-value trades (notably the cash-futures basis) were forced to unwind violently [FSB; NY Fed Staff Reports; BIS Working Paper 966]. The Fed intervened with roughly $1T of Treasury purchases in Q1 2020. The episode revealed that even the "risk-free" benchmark has plumbing fragility, and gave rise to the standing repo facility as a permanent backstop. The basis-trade unwind is a particularly important case study because it has recurred in attenuated form in subsequent stress events (March 2023 banking turmoil, April 2025 tariff shock).

**2013 taper tantrum.** Bernanke's May 22 testimony hinting at QE tapering pushed the 10Y UST from approximately 2% to approximately 3% over four months [Brookings; FRED]. This is the canonical example of "long-end repricing with a large term-premium component" — the front end barely moved, but the long end repriced violently. Useful as a worked example for any term-premium model, with the caveat that the exact split between term-premium and expected-rate components is model-dependent [Federal Reserve, *Robustness of long-maturity term premium estimates*].

**2022 bond bear market.** One of the worst years for global fixed income on modern record — the Bloomberg US Aggregate index posted a deeply negative total return as Treasury yields rose sharply on aggressive Fed hiking [Reuters, September 2022]. Driven by inflation surprise (US CPI peaked at 9.1% YoY in June 2022, the highest since 1981 [BLS]) and the Fed's pivot to aggressive hiking. Useful as a stress case for any model and a regime-shift case for any HMM.

**JGB YCC era and unwinding.** The BoJ pegged 10Y JGB yields to ~0% from September 2016, then progressively widened the band, then formally exited YCC and the negative-interest-rate policy in March 2024 [BoJ]. The BoJ continues to purchase JGBs at roughly the prior pace, so the post-exit market is "less explicitly yield-targeted" rather than fully free. JGB yields have since traded in a way materially different from the YCC era. The episode created a multi-year structural distortion in JGB markets that materially affects how to interpret pre-2024 JGB data and how to think about JGBs as a regime-shift case study.

## 4. Market microstructure (just enough)

**On-the-run vs off-the-run.** OTR is the most recently auctioned bond at a given tenor. It carries a liquidity premium — its yield is below that of off-the-runs at similar maturity, and it can be on special in repo. The OTR/OFR spread is itself a sensitive indicator of liquidity stress. For a serious RV product, OTR/OFR analysis should be bond-specific (CUSIP-level), not just tenor-level — benchmark mapping and nearest-maturity comparisons matter.

**Auction calendar.** USTs follow a regular cadence: 2Y, 3Y, 5Y, and 7Y notes are issued monthly; 10Y notes and 20Y/30Y bonds have quarterly new issues with scheduled reopenings in the intervening months [TreasuryDirect]. The German Finanzagentur publishes annual issuance plans; the UK DMO conducts gilt auctions per a published schedule; the Japanese MoF has a regular JGB auction calendar. Auctions matter because (a) they reset the OTR, (b) demand metrics (auction tail / stop-through, bid-cover, indirect/direct shares) are watched as positioning and demand signals, and (c) supply concessions in the days before auctions create predictable yield dynamics.

**Quoting and conventions.** USTs quote in 32nds with halves and quarters (e.g., "99-08+" = 99 + 8.5/32 = 99.265625); yields are reported in percent. European bonds quote in decimal price. Day counts vary by issuer: USTs, Bunds, OATs, and Gilts customarily use ACT/ACT (ICMA) for fixed-rate non-USD bonds [ICMA]; JGBs use ACT/365 in many implementations, though accrued-interest and yield calculations can differ in specifics. UST settlement is T+1 (long-standing for government securities, predating the 2024 SEC T+1 rule for equities and corporate debt) [Schwab; FINRA]. European and UK bonds are largely T+2 with planned transition to T+1 (ESMA recommends 11 October 2027 for the EU).

**Repo specials.** When a particular bond is in heavy demand (e.g., for shorting against a basis trade, or because it's the cheapest-to-deliver into a futures contract), it trades "special" in repo — i.e., the repo rate at which one can borrow that bond falls below the GC repo rate. Specials matter for two reasons: (1) the financing-adjusted total return on a bond on special is *higher* than the quoted yield suggests if the holder can lend it special — note that this does not change the *cash yield* itself, only the holding return; (2) extreme specials are a microstructure signal of positioning stress.

**Primary dealers.** A small number of large bank dealers are expected (with specifics that vary by jurisdiction) to participate in auctions and support secondary-market liquidity. Their balance sheet capacity (constrained by the Supplementary Leverage Ratio in the US, and analogous regulations elsewhere) is one of the binding constraints on liquidity provision in stress.

## 5. Key metrics PMs watch

Per country in scope, the canonical metrics are:

- **Yields by benchmark tenor:** 2Y, 3Y, 5Y, 7Y, 10Y, 30Y. Add 20Y for US and Gilts; 40Y/50Y for some European issuers and JGBs. T-bills (3M, 6M, 1Y) are covered separately in `03_money_markets_and_cb_pricing.md`.
- **Curve spreads:** 2s10s, 5s30s. Plus 2s5s and 5s10s for finer resolution.
- **Butterflies:** 2s5s10s, 5s10s30s. Default 50-50 weighted unless otherwise specified; DV01-weighted and PCA-weighted are the common alternatives — *which is "canonical" is itself a question to validate at Brevan*.
- **Cross-market spreads at 10Y:** UST-Bund, UST-Gilt, BTP-Bund, OAT-Bund, Bonos-Bund, Gilt-Bund, ACGB-UST, JGB-UST, CAN-UST.
- **Z-scores and percentiles:** typically over a 252-day (1Y) trailing window — but 60d and 126d are commonly used for tactical signals.
- **Period changes:** 1d, 5d, 22d (1M), 63d (3M), 252d (1Y), and YTD.
- **Real yields and breakevens:** for countries with linker markets (US, UK, France, Italy, Australia). Cross to `05_inflation_linked.md`.
- **Asset-swap and swap-spread metrics:** I-spread, par-ASW, Z-spread, bond-vs-OIS spread — convention explicitly selected per analysis. Cross to `02_ois_swaps.md`.
- **Carry and roll:** 1M and 3M holding periods, computed at point in time with explicit financing assumption.
- **Auction metrics:** tail / stop-through, bid-cover, indirect/direct bidder share.
- **Liquidity / technical metrics:** OTR/OFR spread, repo specialness flags, futures basis / CTD linkage, dealer positioning where available, bid-ask, depth, fails (to the extent data is available).

## 6. Standard workflows

The workflows below are the recurring units of analysis on a rates desk. Each maps to a workflow file in `/workflows/` (or "TBD" where we haven't written it yet).

- **Morning yield/curve check** — pull yields, period changes, z-scores across the global sovereign universe. → `workflows/morning_briefing.md`
- **Cross-market relative value** — UST vs Bund, BTP vs Bund, etc., with z-scores and beta-adjustment. → `workflows/relative_value.md`
- **Curve trades** — steepeners, flatteners, butterflies, with weighting and carry analysis. → TBD
- **Periphery spread trades** — BTP-Bund, Bonos-Bund, with regime context (ECB stance, election risk, fiscal news). → TBD
- **Fitted-curve rich/cheap** — fit a curve through observed points, identify which bonds trade rich or cheap to the fitted curve. → TBD
- **Carry/roll attribution** — what is the expected return from holding a position purely from carry and roll, before any spot move. → TBD
- **Auction concession / supply calendar monitor** — pre-auction yield concession analysis, post-auction tail/stop-through interpretation. → TBD
- **Cash-futures basis / CTD linkage** — bond-vs-futures rich/cheap, CTD identification, implied repo. → TBD (cross to `04_bond_futures.md` once written)
- **Real-yield / breakeven decomposition** — was the nominal yield move real-rate-led or inflation-led? → TBD (cross to `05_inflation_linked.md`)
- **Curve regime classification** — bull steepener, bear flattener, twist, parallel shift; used as a state variable for other analyses. → `workflows/regime_classification.md`
- **Term premium decomposition** — separate yields into expected-rates and term-premium components. → TBD
- **Stress scenario / historical replay** — apply a chosen historical period's yield path to current positions. → TBD

## 7. Models and methodologies

Each model below is classified by bucket, with a citation to the canonical reference. Specific tool names are deferred to Section 9.

**Yield-to-maturity from price** (1A) — closed-form for zero-coupons, Newton's method for coupon bonds. [Tuckman, Ch. 3]

**Duration, DV01, modified duration, convexity** (1A) — standard formulas from Macaulay onward. [Tuckman, Ch. 4-6]

**Spread, butterfly, cross-market spread** (1A given fixed weights and conventions) — basic arithmetic on yield differentials. [Tuckman, Ch. 8]

**Z-score and percentile rank** — 1A with default 252d window; 1B with custom window/method.

**Curve regime classification** — 1A as a rule-based heuristic with fixed default thresholds; 1B with parameterized thresholds. The labels are convention-driven diagnostics, not market truths.

**Asset-swap spreads** (1B) — with explicit convention selection. Par-ASW is the cleanest cash-vs-swap richness metric for bonds trading near par; I-spread (yield minus interpolated swap) is simpler but flawed for off-par bonds; Z-spread is the parallel shift to the swap zero curve that re-prices the bond. All three appear on desks; the right convention depends on the trade. [Tuckman, Ch. 17; Choudhry]

**Carry and roll-down** (1B) — expected return from coupon income plus price change from rolling down the (fitted or interpolated) curve, over a chosen holding period and financing assumption. Sensitive enough to financing/interpolation/horizon choices that it doesn't qualify as 1A. [Tuckman, Ch. 16; Martellini-Priaulet]

**Curve fitting** (1B) — Nelson-Siegel [Nelson & Siegel 1987], Nelson-Siegel-Svensson [Svensson 1994], cubic spline. Output is a smooth fitted curve plus per-bond rich/cheap residuals. The choice of methodology is a parameter the PM should be able to override.

**Beta-adjusted cross-market RV** (1B) — rolling regression of one yield series on another, returning the residual as the "RV signal" rather than the raw spread. Hedge ratio comes from the regression slope. [Veronesi, Ch. 18]

**Cointegration tests** (1B) — Engle-Granger and Johansen on RV pairs/baskets. Output is a stationarity test plus the long-run equilibrium relationship. Deterministic given (series, lag, deterministic terms), but produces statistical inference. [Hamilton 1994, Ch. 19]

**Half-life of mean reversion** (1B) — Ornstein-Uhlenbeck fit to a spread series. Output is half-life in days plus current deviation from the long-run mean. Same caveat as cointegration: deterministic given parameters, but estimating a statistical relationship.

**Parametric scenario engine** (1B) — apply a chosen shock vector to current curves and revalue a position. Deterministic given (shock, position).

**Historical replay** (1B) — apply a chosen historical period's daily yield path to current positions. Deterministic given (window, position, interpolation choice). The Bucket-2 sibling is a probabilistic stress engine that *generates* scenarios from a fitted regime model.

**PCA on yield curve** (2) — principal component analysis on cross-tenor yield changes. The first three components are canonically interpreted as level, slope, and curvature. [Litterman & Scheinkman 1991]. Foundational for almost everything quantitative downstream. Classified Bucket 2 because it extracts latent factors — the textbook example of the bucket.

**HMM curve regime classifier** (2) — fits a hidden Markov model on a feature vector (e.g., daily yield change, slope change, MOVE level) with N states. Output: state probabilities over time, transition matrix, current state, per-state feature distributions. Distinct from the 1A/1B rule-based classifier in that states are *learned*, not *defined*. [Hamilton 1989; Rabiner 1989 for HMM mechanics]

**Term premium decomposition** (2) — ACM five-factor affine model [Adrian, Crump & Moench 2013] or Kim-Wright [2005]. State-space estimation that decomposes nominal yields into expected-short-rate and term-premium components. The decomposition is model-dependent; estimates can vary materially by specification and sample [Federal Reserve, *Robustness of long-maturity term premium estimates*].

**Dynamic Nelson-Siegel via Kalman filter** (2) — dynamic version of Nelson-Siegel where level/slope/curvature factors evolve as a state-space model. Useful for forecasting. [Diebold & Li 2006]

**GARCH yield volatility forecasting** (2) — conditional volatility of yield changes. [Bollerslev 1986]

**Probabilistic stress engine** (2) — generate a distribution of curve paths from a fitted regime model (e.g., HMM-conditioned bootstrap, factor-based Monte Carlo), then revalue a position over the distribution. Distinct from `historical_replay` (1B) which uses a single chosen historical window.

**Issuance impact model** (2) — regression-based or event-study estimation of how scheduled auction supply affects yields and curve shape. [Lou-Yan-Zhang 2013]

**Out of scope (deliberately):** trade signal generation, position sizing, "should I buy this bond" recommendations. The product surfaces information; the PM decides.

## 8. Data requirements

This section is a **forward-looking specification of what data each tool in the sovereign bonds domain needs** in order to function. It is not a description of the system's current state — for the current ingestion contract, see `/playbooks/sovereign_bonds.yml`. This section evolves as we scope new tools; the playbook evolves only when we actually build a tool that needs new fields.

### 8.1 Data fields referenced

The fields below are referenced by name in the per-tool specs that follow. Each field is defined once here to avoid repetition.

**Time-series fields (per ticker, per business day):**

- `yield_mid` — closing mid yield (Bloomberg `YLD_YTM_MID`)
- `yield_bid` / `yield_ask` — closing bid / ask yield
- `price_clean` — closing clean price
- `price_dirty` — closing dirty price (clean + accrued)
- `accrued_interest` — accrued interest as of close
- `bid_ask_spread` — closing bid-ask spread
- `volume` — daily traded volume (where available)
- `repo_rate_gc` — General Collateral repo rate at relevant tenor (overnight, term)
- `repo_rate_special` — bond-specific (special) repo rate where applicable
- `dv01` — pre-computed DV01 (alternatively, computed from price/yield/cash-flow)
- `realized_vol` — rolling realized volatility of yield changes (computable from `yield_mid` series)

**Reference / static fields (per ticker):**

- `maturity_date` (Bloomberg `MATURITY`)
- `coupon_rate` (Bloomberg `CPN`)
- `coupon_frequency` (Bloomberg `CPN_FREQ`)
- `issue_date` / `dated_date`
- `day_count_convention` (Bloomberg `DAY_CNT_DES`)
- `issue_size` / `amount_outstanding`
- `settlement_convention` (T+1, T+2)
- `otr_flag` — boolean: is this the current OTR benchmark for its tenor?
- `cusip_or_isin` — primary identifier for individual bonds (vs generic OTR tickers)
- `original_issue_cusip` — for reopenings, link to the original issue
- `ctd_basket_membership` — for individual bonds, eligibility for which futures contracts (cross to `04_bond_futures.md`)
- `inflation_linked_flag` (always false in this module; cross to `05_inflation_linked.md`)

**Event / calendar fields (per country):**

- `auction_calendar` — announce date, auction date, issue date, tenor, announced size
- `auction_results` — high yield, bid-cover ratio, indirect bidder share, direct bidder share, tail / stop-through
- `buyback_operations` — date, size, securities included
- `holiday_calendar` — country-specific business day calendar

**External / cross-domain inputs (data owned by other modules):**

- `swap_curve_par` — par swap rates by tenor (from `02_ois_swaps.md`)
- `swap_zero_curve` — bootstrapped swap zero curve (from `02_ois_swaps.md`)
- `move_index` — MOVE volatility index (from `06_swaptions_and_rates_vol.md`)

### 8.2 Per-tool data requirements

Format: `tool_name` — primary fields needed; key universe / history needs; cross-domain dependencies (if any).

#### Bucket 1A — built

**`yield_levels`** — `yield_mid`; per (curve_family, tenor, date); 252+ business days history for z-score.

**`curve_spread`** — `yield_mid` at two tenors of the same curve_family; full daily history.

**`cross_market_spread`** — `yield_mid` at the same tenor across two curve_families; full daily history.

**`butterfly`** — `yield_mid` at three tenors of the same curve_family; full daily history.

**`curve_regime`** — `yield_mid` across short-end and long-end tenors of the same curve_family; rolling 1d/5d/22d windows.

**`scanner`** — `yield_mid` across the full (curve_family × tenor) universe; 252+ business days for z-score per series.

#### Bucket 1A — planned

**`yield_change_decomposition_simple`** — `yield_mid` across the full tenor grid for one curve_family over a chosen window; no new data beyond what `yield_levels` and friends already need.

**`duration_dv01`** — `coupon_rate`, `coupon_frequency`, `maturity_date`, `day_count_convention`, current `yield_mid` (or `price_clean`) for the specific bond. Requires extending reference fields beyond the current `MATURITY` + `SECURITY_DES` set.

**`otr_ofr_spread`** — `yield_mid` for both OTR benchmark and a parallel **off-the-run bond universe** at the same tenor. The current playbook covers OTR generic tickers only (`GTxxx Govt`); enabling this tool requires ingesting individual-bond CUSIPs near each tenor point, with `otr_flag` mapping.

**`auction_calendar_and_results`** — `auction_calendar` and `auction_results` per country. Bloomberg fields available (e.g., `BIDDER_INDIRECT_PCT`, `AUCTION_HIGH_YIELD`, `BID_TO_COVER_RATIO`) but not currently ingested.

#### Bucket 1B — planned

**`zscore_custom`** — `yield_mid` (or any series); no new fields.

**`butterfly_weighted`** — for DV01-weighted: `dv01` for each leg (or the inputs to compute it). For PCA-weighted: PCA loadings from `pca_yield_curve` (Bucket 2 dependency).

**`curve_regime_parameterized`** — same data as 1A version; no new fields.

**`carry_and_roll`** — `yield_mid`, `price_clean`, `price_dirty`, `accrued_interest`, `coupon_rate`, `coupon_frequency`, `maturity_date`, `day_count_convention` for the specific bond. Plus a financing assumption: `repo_rate_gc` at a chosen term, or a constant rate, or implied from `swap_curve_par` (cross-domain). The financing input is the biggest delta vs current playbook.

**`curve_fitter`** — for OTR-only fits: `yield_mid` across tenors (sufficient with current playbook). For full-curve fitting that captures cross-bond rich/cheap: requires the **individual-bond universe** (`cusip_or_isin`, `coupon_rate`, `maturity_date`) so that bonds at all maturities — not just the eight benchmark tenors — can be fitted.

**`rich_cheap_screen`** — depends on `curve_fitter`; requires individual-bond universe per country.

**`asset_swap_spread`** — for I-spread: `yield_mid` (have), `swap_curve_par` at matched tenor (cross-domain, OIS module). For par-ASW: bond cash flows (`coupon_rate`, `coupon_frequency`, `maturity_date`, `day_count_convention`, `dated_date`) plus the swap zero curve. For Z-spread: bond cash flows plus `swap_zero_curve`.

**`beta_adjusted_spread`** — paired `yield_mid` series at chosen tenors; full daily history at user-chosen window. No new fields.

**`cointegration_test`** — paired or basket of `yield_mid` series; long history (5+ years preferred for stability of test).

**`half_life`** — single residual / spread series; long enough history for OU fit (typically 1Y+).

**`rolling_regression`** — target + regressor `yield_mid` series; user-chosen rolling window.

**`parametric_scenario`** — user-supplied position spec (instruments + sizes); current curve (`yield_mid`); `dv01` per instrument (or computed from cash flows).

**`historical_replay`** — user-supplied position spec; historical curve panel (`yield_mid` across full tenor grid for chosen countries) over user-specified historical window.

#### Bucket 2 — planned

**`pca_yield_curve`** — `yield_mid` across the full tenor grid (1Y through 30Y, eight points) for one or more curve_families; lookback of 1–10 years configurable. Sensitive to history length: short windows give unstable loadings, long windows mix regimes. The existing playbook's 2005 start date supports up to 20-year lookbacks.

**`yield_change_attribution_pca`** — depends on `pca_yield_curve` output (loadings); no new raw data.

**`hmm_curve_regime`** — feature vector configurable: typical inputs include daily `yield_mid` changes at 2Y/10Y/30Y, slope changes, `realized_vol` (computable from `yield_mid`), and optionally `move_index` (cross-domain from rates vol module). Lookback typically 5+ years to capture multiple regime episodes.

**`term_premium_acm`** — full daily `yield_mid` panel across tenors for one country; long history (typically 10+ years). The 1Y–30Y coverage in the playbook is well-suited; specifically the 2005 start gives pre-GFC, GFC, ZIRP, taper-tantrum, and 2022-cycle regimes in-sample.

**`dynamic_nelson_siegel`** — full daily `yield_mid` panel; long history.

**`garch_yield_vol`** — `yield_mid` series at chosen tenor; long history (5+ years for stable parameter estimates).

**`probabilistic_stress`** — depends on a fitted regime model (`hmm_curve_regime` or similar) and a user-supplied position spec; no new raw data beyond what those models need.

**`multi_country_pca`** — `yield_mid` panels across all countries × all tenors; long history. Requires careful holiday-calendar handling across countries (`holiday_calendar` per country).

**`issuance_impact_model`** — `auction_calendar`, `auction_results`, plus surrounding daily `yield_mid` movements for event-window analysis; ideally also macro surprise data (consensus expectations vs releases) as controls. Auction data is the binding constraint and is shared with `auction_calendar_and_results` (1A).

#### Aspirational

**`repo_specialness_monitor`** — `repo_rate_gc` and `repo_rate_special` per benchmark bond, daily. Requires a repo data source (e.g., DTCC GCF, OFR's repo data collection, Bloomberg's repo screens). Bloomberg coverage is partial; OFR's bilateral repo data became available in July 2025 with limited waivers and may be the canonical source going forward.

**`dealer_positioning_model`** — Fed primary dealer survey data (FR2004), TRACE-like sovereign trade data (released at increasing frequency since 2020), plus `auction_results` (overlaps with 1A). All publicly available but requires non-Bloomberg ingestion.

**`survey_vs_market_decomposition`** — Survey of Professional Forecasters (SPF) data, Bloomberg consensus economist forecasts (`ECO` screens), or central bank own forecasts (FOMC dot plot, ECB SPF, BoE MPR). Cross-domain with macro forecast data we don't currently treat as a domain.

**`flow_attribution`** — Treasury TIC data (foreign holdings), Fed Z.1 Financial Accounts (sectoral holdings), ETF fund flow data, pension fund disclosure data. All publicly available, all at low frequency (monthly to quarterly), and requires substantial non-Bloomberg ingestion plumbing.

### 8.3 Cross-domain data dependencies summary

A handful of fields above are owned by other instrument modules. Tools in this module should not duplicate that ingestion — they consume from the canonical owner.

- `swap_curve_par`, `swap_zero_curve` → owned by `02_ois_swaps.md` / OIS playbook
- `move_index` → owned by `06_swaptions_and_rates_vol.md` (when written)
- Macro forecast / consensus data → not yet a defined domain; will need its own module if `survey_vs_market_decomposition` is prioritized

## 9. Tool inventory

Format: **`tool_name`** — takes [inputs], runs [model/computation], returns [outputs], used in [workflows] to surface [macro implication].

### Built (Bucket 1A)

**`yield_levels`** — takes a country, tenor, and date; queries the daily yields table for the OTR benchmark; returns the yield level, 1d/5d/22d changes in bps, 252d high/low/percentile, and 252d z-score; used in `morning_briefing` and any directional analysis to surface where a yield sits relative to its recent distribution.

**`curve_spread`** — takes a country and two tenors; computes the yield differential at each historical date; returns current spread in bps, 1d/5d/22d changes, 252d z-score, and time series; used in `morning_briefing` and curve-trade workflows to surface curve shape and recent steepening/flattening dynamics.

**`cross_market_spread`** — takes two (country, tenor) pairs; computes the yield differential time series; returns current spread, period changes, and 252d z-score; used in `relative_value` and `morning_briefing` to surface cross-country macro divergence (e.g., UST-Bund 10Y encoding US vs Europe policy, growth, supply, and basis dynamics jointly).

**`butterfly`** — takes a country and three tenors with optional weights (default 50-50); computes the curvature spread (2 × middle − wing₁ − wing₂); returns current value, period changes, 252d z-score, and per-wing decomposition; used in curve-trade workflows to surface belly richness or cheapness independent of overall curve direction.

**`curve_regime`** — takes a country and lookback windows (1d/5d/22d); classifies the curve move as BULL_STEEPENER, BEAR_FLATTENER, BULL_FLATTENER, BEAR_STEEPENER, PARALLEL_SHIFT, or TWIST based on directional combinations of front-end and long-end changes against fixed default thresholds; returns the classification per window with magnitude metrics; used in `regime_classification` and `morning_briefing` to surface what kind of move is driving today's yield action (rate-expectations vs term-premium vs flow).

**`scanner`** — takes a z-score threshold (default 2.0) and tenor universe; iterates `yield_levels` across all (country, tenor) combinations; returns ranked list of extremes with sign and magnitude; used in `morning_briefing` and idea-generation to surface where the global sovereign universe is showing statistical stress in one query.

### Planned (Bucket 1A)

**`yield_change_decomposition_simple`** — takes a country, tenor, and date range; mechanically decomposes the cumulative yield change across selected tenor contributions (front, belly, long); returns bps attributed to each region of the curve; used in attribution and post-mortem workflows to surface what kind of move actually happened (parallel shift vs reshape). The PCA-based attribution (`yield_change_attribution_pca`) lives in Bucket 2 below because the loadings are estimated.

**`duration_dv01`** — takes an instrument and date; computes Macaulay duration, modified duration, DV01, and convexity from the bond's cash flows and current yield; returns the risk metrics; used in position-sizing and hedge-ratio workflows to surface interest-rate exposure per unit of position.

**`otr_ofr_spread`** — takes a country and tenor; computes the spread between the OTR and the most recent OFR of the same tenor; returns current spread, z-score, and time series; used in liquidity-stress monitoring to surface market microstructure stress (the OTR/OFR spread is a sensitive indicator of dealer balance sheet strain — the March 2020 widening is the canonical example).

**`auction_calendar_and_results`** — takes a country and date range; queries the auction calendar and historical results; returns upcoming auctions with announced sizes plus historical metrics (tail/stop-through, bid-cover, indirect share); used in supply-calendar and pre-auction concession workflows to surface scheduled supply pressure.

### Planned (Bucket 1B)

**`zscore_custom`** — takes a series, lookback window, and standardization method (rolling vs expanding); computes z-score; returns z-score time series; used as a building block for any analysis where the default 252d window doesn't fit (e.g., faster-moving signals at 60d, longer-horizon at 504d).

**`butterfly_weighted`** — takes a country, three tenors, and a weighting scheme (50-50, DV01-weighted, or PCA-weighted); computes the appropriately weighted curvature spread; returns the same structure as the 1A `butterfly` plus the weighting metadata; used when the default 50-50 weighting doesn't give the right risk-neutral view.

**`curve_regime_parameterized`** — takes a country, lookback windows, and threshold parameters (front-end and long-end change cutoffs, magnitude floors); runs the same logic as the 1A `curve_regime` tool with custom thresholds; returns classification with sensitivity to threshold choice; used when default thresholds don't fit a specific market environment (e.g., the absolute scale of moves in a high-vol regime).

**`carry_and_roll`** — takes an instrument, holding horizon (1M/3M/6M), and a financing assumption (custom repo curve, constant rate, or implied from OIS); computes coupon income plus price change from rolling down a chosen curve interpolation (linear, NS, NSS); returns total carry-and-roll in bps decomposed into coupon income, roll-down, and pull-to-par; used in directional, curve-trade, and ASW workflows to surface expected return absent any spot move.

**`curve_fitter`** — takes a set of bond yields at a given date and a model choice (Nelson-Siegel, NSS, cubic spline); fits the chosen model; returns fitted parameters, fitted curve at all tenors, and per-bond residuals (rich/cheap); used in rich-cheap workflows to surface which specific bonds are mispriced relative to the fitted curve. [Nelson & Siegel 1987; Svensson 1994]

**`rich_cheap_screen`** — takes a country and date; runs `curve_fitter` and ranks bonds by residual magnitude; returns top-N rich and top-N cheap bonds with residual size, historical residual percentile, and OTR/OFR/specialness context; used in idea-generation and rich-cheap workflows to surface specific trade candidates.

**`asset_swap_spread`** — takes a sovereign bond, a swap curve, and a convention choice (par-ASW, I-spread, or Z-spread); computes the spread under the chosen convention with proper handling of the bond's actual cash flows (for par-ASW); returns the ASW level, z-score, and time series; used in cash-vs-swap RV workflows to surface dislocations between sovereign and swap markets, and as a relative-value primitive that bridges this module and `02_ois_swaps.md`. The convention matters: par-ASW for near-par RV; I-spread for quick comparisons; Z-spread for credit-adjacent / off-par bonds. [Tuckman, Ch. 17; Choudhry]

**`beta_adjusted_spread`** — takes two yield series, a regression window (rolling vs expanding), and a frequency; runs OLS of one on the other; returns rolling beta, residual time series, residual z-score, and current hedge ratio; used in cross-market RV workflows where raw spreads are misleading because the two series have different volatilities (e.g., BTP and Bund are not 1:1 sensitive to a common driver, so a raw BTP-Bund spread overstates "true" RV signal during high vol).

**`cointegration_test`** — takes two or more yield/spread series, lag specification, and test choice (Engle-Granger or Johansen); runs the cointegration test; returns p-value, long-run equilibrium relationship, and residual series; used in RV strategy development to surface whether a pair is statistically mean-reverting (a precondition for many spread trades). [Hamilton 1994]

**`half_life`** — takes a residual or spread series; fits an Ornstein-Uhlenbeck process; returns half-life in days, current deviation from long-run mean, and confidence interval; used in RV workflows to size and time mean-reversion trades.

**`rolling_regression`** — takes a target series and one or more regressors with a rolling window; returns time series of betas, residuals, R², and current hedge ratio; used as a generic primitive for hedge-ratio computation, factor model construction, and beta-adjusted analyses.

**`parametric_scenario`** — takes a position spec (instruments and DV01s) and a shock vector (parallel shift, twist defined by front-end and back-end shocks, or custom curve shock); revalues the position; returns P&L by instrument, total P&L, and post-shock curve; used in scenario-analysis workflows for position-level stress without requiring a full historical replay.

**`historical_replay`** — takes a position spec and a historical window (e.g., 2013-05-01 to 2013-09-01 for the taper tantrum); replays the daily yield/curve changes through the position; returns P&L path, drawdown, vol, worst-day, and decomposition by instrument; used in stress-testing workflows for "what would happen to my book if 2013 happened again." Deterministic given (window, position, interpolation).

### Planned (Bucket 2)

**`pca_yield_curve`** — takes a country (or set of countries), tenor universe, and lookback window; computes principal components of yield changes; returns loadings (per tenor), factor time series, variance explained, and current factor levels; used in attribution, scenario, and RV workflows to surface the underlying factor structure of curve moves and to provide loadings for `yield_change_attribution_pca`. The level/slope/curvature decomposition is foundational for most quantitative rates work. [Litterman & Scheinkman 1991]

**`yield_change_attribution_pca`** — takes a country, tenor, date range, and the PCA loadings from `pca_yield_curve`; decomposes the cumulative yield change at a chosen tenor into level/slope/curvature factor contributions plus residual; returns bps attributed to each factor; used alongside the simpler 1A decomposition to give a model-based attribution that respects the empirical factor structure of the curve.

**`hmm_curve_regime`** — takes a country, feature vector (e.g., daily yield change, slope change, MOVE level), number of states N, and lookback window; fits a Gaussian HMM via expectation-maximization; returns state probability time series, transition matrix, current state, and per-state feature distributions; used in `regime_classification` workflows to surface market regimes that emerge from the data rather than from rule-based labeling. [Hamilton 1989; Rabiner 1989]

**`term_premium_acm`** — takes a country and yield curve history; estimates the ACM five-factor affine term structure model; returns term premium and expected-short-rate components at each tenor over time; used in term-premium-decomposition workflows to surface whether a yield move is driven by repricing of policy expectations or by repricing of duration risk premium. The 2013 taper tantrum is the textbook example: most of the long-end move is commonly attributed to term premium repricing, though the exact split is model-dependent. [Adrian, Crump & Moench 2013; NY Fed Treasury Term Premia data]

**`dynamic_nelson_siegel`** — takes a country, yield curve history, and lookback; estimates Diebold-Li dynamic Nelson-Siegel via Kalman filter; returns time series of latent level/slope/curvature factors with their state-space dynamics; used in forecasting and curve-shape analysis. [Diebold & Li 2006]

**`garch_yield_vol`** — takes a yield or spread series, model order (typically GARCH(1,1)), and window; estimates conditional volatility; returns vol forecast and vol-of-vol; used in vol-regime workflows and in volatility-targeted position sizing. [Bollerslev 1986]

**`probabilistic_stress`** — takes a position spec and a fitted regime model (HMM, factor model); generates a distribution of curve paths conditional on the current regime or unconditionally; returns a P&L distribution with VaR, ES, and stress quantiles; used in stress-testing workflows where a single historical replay is insufficient.

**`multi_country_pca`** — takes a set of countries and tenors; computes PCA across the joint cross-section of yield changes; returns global factor loadings and per-country factor exposures; used in cross-market RV workflows to identify common factors driving multiple markets and idiosyncratic country residuals.

**`issuance_impact_model`** — takes a country and auction schedule; runs an event-study or regression of yield changes around scheduled auctions controlling for macro surprises; returns estimated supply concession in bps as a function of size, tenor, and market conditions; used in tactical-positioning workflows to surface predictable supply-driven yield dynamics. [Lou-Yan-Zhang 2013]

### Aspirational (data-blocked or further out)

**`repo_specialness_monitor`** — needs repo data we don't ingest. Would identify bonds trading special, quantify the specialness in bps below GC, and produce a financing-adjusted total return / implied repo metric. Would not overwrite the cash yield, but produce a parallel "holding-return-aware" view.

**`dealer_positioning_model`** — needs Fed primary dealer survey data and TRACE-like sovereign trade data, which is partial and lagged.

**`survey_vs_market_decomposition`** — needs SPF / Bloomberg consensus expectations data. Would decompose forward yields into "consensus-expected path" plus "market-vs-consensus residual" to surface where the market is pricing differently from the consensus economist view.

**`flow_attribution`** — needs Treasury TIC data, pension fund flow data, ETF flow data. Would attribute yield moves to identifiable flow drivers (foreign official, foreign private, domestic real money, leveraged accounts, ETF retail).

## 10. Open questions

These are things to validate at Brevan or with the advisory firm. Each tagged with a category.

- *#parameter-default* — Default z-score lookback is 252d. Is this what PMs actually use, or is 60d/126d more common for tactical signals? Likely depends on the workflow.
- *#parameter-default* — Default butterfly weight is 50-50. PMs often use DV01-weighted or PCA-weighted; need to confirm which is canonical and in which contexts.
- *#parameter-default* — Default carry horizon is 1M/3M. Is there a "house view" horizon at major macro funds, or does it vary by trade thesis?
- *#convention* — ASW: par-asset-swap vs Z-spread vs I-spread — which convention is most commonly demanded? My current guess: par-ASW for cash-vs-swap RV near par, Z-spread for credit-adjacent / off-par bonds, I-spread for quick comparisons. Validate.
- *#convention* — How are repo specials actually integrated into RV decisions? Quantitative adjustment to a financing-adjusted yield, or qualitative "this trade has a specials tailwind" overlay?
- *#workflow-validation* — Does the curve regime classifier actually drive trading decisions, or is it primarily descriptive (used to confirm or refute the PM's prior on what's driving the move rather than as a signal in its own right)?
- *#workflow-validation* — How widely is term premium decomposition (ACM/Kim-Wright) actually used in discretionary macro? Is it mostly a quant/research artifact rather than a trader artifact, or does it directly feed trade theses?
- *#workflow-validation* — Periphery RV: how do PMs decide when BTP-Bund is a "fundamentals trade" vs an "ECB-policy trade" vs a "redenomination-risk trade"? The decomposition matters for sizing and for hedge selection.
- *#workflow-validation* — How important is pre-auction concession analysis for actual trading vs as research?
- *#workflow-validation* — Cash-futures basis: how live is the basis-trade unwind risk in PM decision-making, and what monitoring metrics (basis level, repo, dealer balance sheet proxies) do they actually watch?
- *#data* — Do major macro pods rely on internal repo data, or is GC-only sufficient for most workflows?
- *#scope* — JGBs in the post-YCC era are still finding their natural range. Do PMs treat pre-2024 JGB data as essentially unusable for current analysis, or do they apply regime-dependent discounts?
- *#scope* — How do PMs handle the Italy/Spain "credit-risky sovereign" dimension — dedicated periphery analysis tools, or live with the credit/fragmentation overlay in standard sovereign tools?
- *#scope* — France: at what point do PMs start treating OAT-Bund as a fragmentation/credit signal rather than a core RV signal?
- *#methodology* — For curve fitting: is there a "house standard" methodology (NS, NSS, spline) at major funds, or does it vary by desk?
- *#methodology* — Is HMM-style regime classification actually used live, or is it a research-paper artifact that doesn't survive the live-trading test?

## 11. References

- *[Tuckman, Ch. X]* — Tuckman, B., & Serrat, A. (2022). *Fixed Income Securities: Tools for Today's Markets* (4th ed.). Wiley. Canonical fixed-income reference.
- *[Veronesi]* — Veronesi, P. (2010). *Fixed Income Securities: Valuation, Risk, and Risk Management*. Wiley.
- *[Choudhry]* — Choudhry, M. (2010). *The Bond and Money Markets: Strategy, Trading, Analysis*. Butterworth-Heinemann. Practitioner reference for ASW conventions.
- *[Nelson & Siegel 1987]* — Nelson, C. R., & Siegel, A. F. (1987). "Parsimonious Modeling of Yield Curves." *Journal of Business*, 60(4), 473–489.
- *[Svensson 1994]* — Svensson, L. E. O. (1994). "Estimating and Interpreting Forward Interest Rates: Sweden 1992-1994." NBER Working Paper No. 4871.
- *[Litterman & Scheinkman 1991]* — Litterman, R., & Scheinkman, J. (1991). "Common Factors Affecting Bond Returns." *Journal of Fixed Income*, 1(1), 54–61. Foundational PCA-on-yields paper.
- *[Hamilton 1989]* — Hamilton, J. D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle." *Econometrica*, 57(2), 357–384. Foundational regime-switching paper.
- *[Hamilton 1994]* — Hamilton, J. D. (1994). *Time Series Analysis*. Princeton University Press. Reference for cointegration, Kalman filter, state-space models.
- *[Rabiner 1989]* — Rabiner, L. R. (1989). "A tutorial on hidden Markov models and selected applications in speech recognition." *Proceedings of the IEEE*, 77(2), 257–286. HMM mechanics reference.
- *[Adrian, Crump & Moench 2013]* — Adrian, T., Crump, R. K., & Moench, E. (2013). "Pricing the Term Structure with Linear Regressions." *Journal of Financial Economics*, 110(1), 110–138. ACM term premium model. See also NY Fed Treasury Term Premia data page.
- *[Kim & Wright 2005]* — Kim, D. H., & Wright, J. H. (2005). "An Arbitrage-Free Three-Factor Term Structure Model and the Recent Behavior of Long-Term Yields and Distant-Horizon Forward Rates." Federal Reserve Board FEDS 2005-33.
- *[Diebold & Li 2006]* — Diebold, F. X., & Li, C. (2006). "Forecasting the term structure of government bond yields." *Journal of Econometrics*, 130(2), 337–364.
- *[Bollerslev 1986]* — Bollerslev, T. (1986). "Generalized Autoregressive Conditional Heteroskedasticity." *Journal of Econometrics*, 31(3), 307–327.
- *[Lou-Yan-Zhang 2013]* — Lou, D., Yan, H., & Zhang, J. (2013). "Anticipated and Repeated Shocks in Liquid Markets." *Review of Financial Studies*, 26(8), 1891–1912. Auction supply impact.
- *[Martellini-Priaulet]* — Martellini, L., Priaulet, P., & Priaulet, S. (2003). *Fixed-Income Securities: Valuation, Risk Management and Portfolio Strategies*. Wiley.
- *[ISDA 2020]* — ISDA. *Adoption of Risk-Free Rates: Major Developments in 2020.* For OIS discounting and post-LIBOR transition.
- *[BIS]* — Bank for International Settlements. Quarterly Reviews and working papers; in particular *Beyond LIBOR: a primer on the new benchmark rates* (2019) and *The Treasury market in spring 2020 and the response of the Federal Reserve* (Working Paper 966) for March 2020 dynamics.
- *[BoE 2023]* — Bank of England. *An anatomy of the 2022 gilt market crisis* (Working Paper); *Financial stability buy/sell tools: a gilt market case study* (Quarterly Bulletin, 2023).
- *[FSB 2020]* — Financial Stability Board. *Holistic Review of the March 2020 Market Turmoil* (November 2020).
- *[NY Fed Staff Reports]* — Federal Reserve Bank of New York. Staff Report 998, *The Federal Reserve's Market Functioning Purchases* (2021); various Liberty Street Economics posts on Treasury market liquidity.
- *[Federal Reserve TP robustness]* — Federal Reserve Board. *Robustness of long-maturity term premium estimates* (FEDS Notes, April 2017).
- *[BoJ]* — Bank of Japan. *New Framework for Strengthening Monetary Easing* (September 2016, YCC introduction); *Monetary Policy Decision* (March 2024, YCC and NIRP exit).
- *[TreasuryDirect]* — US Treasury. *General Auction Timing*, *Schedule of Auction Reopenings*. Authoritative reference for UST auction cadence.
- *[ICMA]* — International Capital Market Association. *Day Count Fraction* (Appendix A5).
- *[BIS Basel framework]* — Basel Committee on Banking Supervision. *LCR30 — High-quality liquid assets*. For HQLA classification.
- *[Reuters; FRED; Bloomberg]* — used for specific market data points cited in Section 3, including 2s10s inversion records (Reuters, March 2024; FRED T10Y2Y series), the 2022 bond bear market (Reuters, September 2022), the 2022 CPI peak (BLS), and UST-Bund spread observations (multiple sources, including BofA via Investing.com, ING).
- *[CRS RS22331]* — Congressional Research Service. *Foreign Holdings of Federal Debt* (multiple updates, 2024-2025). For foreign ownership share breakdown (official vs private).
- *[US Treasury TIC]* — US Treasury International Capital System. *Foreign Portfolio Holdings of US Securities* annual reports.
