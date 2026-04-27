# Inflation Swaps

**Status:** v2 internal draft — built from `/playbooks/inflation_swaps.yml`, with convention caveats, data-lineage requirements, and bucket-classification discipline. Revised to align with `04_inflation_indexed_bonds_v2.md` on inflation-compensation language, forward-rate treatment, and swap-breakeven basis sign. To be validated against live Bloomberg metadata, dealer/advisory feedback, and Brevan workflow observations.

**Scope:** 21 zero-coupon inflation swap benchmark series across 3 curve families currently in `/playbooks/inflation_swaps.yml`: USD ZCIS, EUR ZCIS, and GBP ZCIS. Each curve includes 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, and 30Y tenors. The playbook currently stores Bloomberg `PX_MID` as `yield_mid`, but this is a naming artifact: economically the field is the **zero-coupon inflation swap rate**, not a bond yield.

**Cross-references:** Inflation swaps are the derivative-market analogue to linker-implied breakevens in `04_inflation_indexed_bonds.md`. The linker module owns real-yield and bond-implied breakeven analytics; this module owns zero-coupon inflation swap rates, forward inflation swap rates, inflation-swap curve analytics, and the canonical `swap_minus_breakeven_basis = ZCIS - bond-implied breakeven` definition. Central-bank inflation-expectations monitoring lives partly in `workflows/morning_briefing.md` and partly in a future `workflows/inflation_expectations.md`. Inflation options, inflation caps/floors, CPI fixing swaps, and year-on-year inflation swaps are related instruments but are outside the current playbook.

---

## 1. What it is

An inflation swap is an OTC derivative that transfers inflation risk between two parties. One party pays a fixed inflation rate; the other pays realized inflation linked to a specified consumer price index. The most common structure in this playbook is the **zero-coupon inflation swap** (`ZCIS`): both legs settle once at maturity rather than paying coupons periodically.

For a stylized zero-coupon inflation swap with notional `N`, maturity `T`, fixed rate `K`, initial index level `I_0`, and final index level `I_T`, the maturity exchange can be written approximately as:

```text
inflation leg = N × (I_T / I_0 - 1)
fixed leg     = N × ((1 + K)^T - 1)
```

The quoted zero-coupon inflation swap rate is the par fixed rate `K` that makes the swap have zero value at inception under the relevant collateral, discounting, index, lag, interpolation, calendar, and payment conventions. Economically, the rate is a market-based measure of **inflation compensation** over the swap horizon. It is not a pure expected-inflation forecast. In physical-measure language, it embeds expected inflation plus inflation risk premium and swap-market technicals; equivalently, it can be described as risk-neutral inflation compensation plus collateral, funding, liquidity, seasonality, index-lag, and technical effects.

In scope for this module, by curve family and reference index:

- **United States — USD ZCIS.** Reference index: US CPI-U NSA, represented in Bloomberg convention by CPI-U / CPURNSA-style references. Playbook curve family: `USD_ZCIS`. Tenors: 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y. Playbook tickers: `USSWIT1`, `USSWIT2`, `USSWIT3`, `USSWIT5`, `USSWIT10`, `USSWIT20`, `USSWIT30` `Curncy`. Playbook lag: `3M`; interpolation: `Daily`. Common market templates define initial/final index levels using interpolated CPI levels between the second and third months preceding the relevant period date. [BLS; TP ICAP US CPI ZCIS; ISDA Inflation Definitions]

- **Euro area — EUR ZCIS.** Reference index: euro-area HICP excluding tobacco (`HICPxT`). Playbook curve family: `EUR_ZCIS`. Tenors: 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y. Playbook tickers: `EUSWI1`, `EUSWI2`, `EUSWI3`, `EUSWI5`, `EUSWI10`, `EUSWI20`, `EUSWI30` `Curncy`. Playbook lag: `3M`; interpolation: `Monthly`. Common EUR ZCIS templates use the HICPxT index for the month three months preceding the relevant period date. Analytics libraries and vendor curves may support both monthly and daily-interpolated mechanics, but production tooling should follow Bloomberg security metadata, ISDA terms, and clearing-house terms for the specific ticker rather than hard-coding conventions from prose. [ECB; Eurostat; TP ICAP HICPxT ZCIS; Eurex]

- **United Kingdom — GBP ZCIS.** Reference index: UK Retail Price Index (`RPI`). Playbook curve family: `GBP_ZCIS`. Tenors: 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y. Playbook tickers: `BPSWIT1`, `BPSWIT2`, `BPSWIT3`, `BPSWIT5`, `BPSWIT10`, `BPSWIT20`, `BPSWIT30` `Curncy`. Playbook lag: `2M`; interpolation: `Monthly`. Common UK RPI ZCIS templates use the RPI index for the month two months preceding the relevant period date, with no daily interpolation. **Critical:** RPI is scheduled to be aligned with CPIH from February 2030, with no compensation to index-linked gilt holders. This affects GBP RPI swaps as well because the floating leg references published RPI. [ONS; TP ICAP UK RPI ZCIS; HM Treasury / UKSA RPI Reform]

The current playbook is deliberately narrower than the global inflation-swaps market. It excludes French CPIxT, Italian FOI, Spanish CPI, Japanese CPI, Canadian CPI swaps, year-on-year swaps, inflation caps/floors, CPI fixing swaps, and sub-1Y fixing instruments. That is acceptable for the MVP because USD/EUR/GBP ZCIS curves cover the core G3 inflation-compensation workflow.

---

## 2. Why it exists / role in the system

Inflation swaps serve five economic functions for a macro rates product.

**Derivative-market inflation compensation.** A zero-coupon inflation swap is the cleanest OTC derivative-market quote for inflation compensation over a specified horizon. A 10Y USD ZCIS rate is the par fixed rate against realized CPI-U inflation over ten years under the stated swap convention. A 5Y5Y EUR inflation swap rate, in the current playbook, is a **derived par forward inflation rate** for the five-year period beginning five years forward, calculated from the spot ZCIS curve under a stated compounding convention. Central banks, macro PMs, inflation desks, and strategists use these rates to monitor whether inflation compensation is anchored or de-anchoring.

**Inflation hedge without physical linkers.** Inflation swaps let pension funds, insurers, real-money accounts, banks, corporates, and hedge funds hedge or take inflation exposure without buying or shorting inflation-linked bonds. This matters because linkers have real-duration exposure, liquidity premia, bond-specific scarcity, index-ratio mechanics, settlement conventions, and deflation-floor features. A swap isolates the inflation leg more directly, though it introduces collateral, counterparty/clearing, margin, and derivative-liquidity considerations.

**Cleaner comparator to breakevens.** Bond-implied breakevens are contaminated by linker liquidity, nominal-bond liquidity/convenience premia, supply/demand technicals, and deflation-floor effects. Inflation swaps avoid some bond-specific distortions, so the **inflation swap rate minus linker breakeven** is a core basis measure. A positive basis under the canonical sign convention below can indicate that linkers are cheap, swaps are rich, or that liquidity/balance-sheet/seasonality effects differ across the two markets. This basis is one of the most important cross-links between this module and `04_inflation_indexed_bonds.md`.

**CPI fixing and front-end inflation trading.** Front-end inflation swaps, especially 1Y and 2Y, are sensitive to near-term CPI prints, energy prices, taxes, tariffs, administered prices, base effects, and seasonality. They are not simply “long-run expectations.” A 1Y USD ZCIS move can reflect gasoline, food, rents, medical services, tariffs, or the mechanical impact of known CPI prints and index lag. This makes front-end inflation swaps useful for event attribution and CPI-release workflows.

**Cross-market inflation relative value.** USD CPI, euro HICPxT, and UK RPI are different inflation indices with different baskets, institutional targets, publication schedules, and methodological risks. Cross-market ZCIS spreads — for example, USD 5Y vs EUR 5Y, GBP 10Y vs EUR 10Y, or USD 5Y5Y vs EUR 5Y5Y — are not pure “US inflation minus Europe inflation.” They are index-specific inflation compensation spreads that include risk premia, currency-area macro differences, and local index methodology. This makes them valuable, but convention-heavy.

---

## 3. What its movements signal (macro context)

### The inflation swap decomposition

A zero-coupon inflation swap rate is best described as **inflation compensation**. A useful first-pass decomposition is:

```text
ZC inflation swap rate
≈ real-world expected inflation over the horizon
+ inflation risk premium
+ swap-market liquidity / collateral / balance-sheet premium
+ seasonality and index-lag technicals
+ index-specific methodology effects
```

Equivalently, the ZCIS rate can be described as risk-neutral inflation compensation plus swap-market and index-specific technicals. The product should not present it as a pure CPI/HICP/RPI forecast.

Compared with linker breakevens, inflation swaps remove some bond-market technicals: no linker scarcity, no linker deflation-floor valuation, no bond-specific issue size, and no OTR/OFR linker roll. A swap held to maturity has no cash-bond real-duration component in the linker sense, but mark-to-market P&L still depends on inflation-swap curve moves, nominal discounting, collateral rates, margin, and liquidity. Swaps are not frictionless: they are OTC derivatives, usually cleared or collateralized, and they can embed dealer balance-sheet costs, margin/collateral effects, liquidity premia, seasonality, and risk premia.

### Spot ZCIS rates

A 1Y ZCIS rate is mostly about near-term realized inflation, known CPI prints, expected upcoming prints, and seasonality. A 10Y ZCIS rate blends near-term inflation, medium-term policy credibility, long-run inflation risk premium, and index methodology. A 30Y ZCIS rate is heavily shaped by long-horizon liability hedging, pension demand, dealer capacity, and structural risk-premium supply/demand.

The same move has different meaning by tenor. A 50bp rise in 1Y USD ZCIS after an oil shock may be a front-end CPI carry event. A 50bp rise in 5Y5Y USD or EUR inflation swaps is much more consequential: it suggests a repricing of long-horizon inflation compensation, inflation risk premium, or monetary-policy credibility.

### Forward inflation swaps

Forward inflation swaps transform spot ZCIS rates into inflation compensation for future windows. The canonical forward is 5Y5Y:

```text
5Y5Y = ((1 + ZCIS_10Y)^10 / (1 + ZCIS_5Y)^5)^(1/5) - 1
```

This is the inflation-swap analogue of the 5Y5Y forward breakeven in the linker module. The ECB explicitly describes the 5Y5Y inflation-linked swap rate as a widely used longer-term market-based inflation-expectations indicator, defined as the average inflation rate over a five-year period starting in five years. [ECB Market-Based Indicators]

The product must label this correctly: **5Y5Y inflation swap rate = market-based long-horizon inflation compensation**, not a pure forecast of average CPI/HICP/RPI. If the user asks “what does the market expect inflation to be in five years,” the answer should explain that the number is market-implied compensation and includes risk premia.

### Curve shape

The ZCIS curve shape carries macro information:

- **Front-end high, back-end anchored:** near-term inflation shock perceived as transitory, or front-end energy/tax/tariff/base-effect pressure with credible central bank anchoring.
- **Parallel rise:** broad inflation-compensation repricing across horizons.
- **Back-end-led rise:** de-anchoring risk, inflation risk premium repricing, or long-horizon liability demand/supply imbalance.
- **Front-end collapse with stable 5Y5Y:** disinflation or recessionary near-term shock without long-run de-anchoring.
- **Front-end low, back-end high:** possible near-term demand weakness but persistent long-horizon risk premium, depending on macro context.

### Swap-vs-breakeven basis

Inflation swaps and bond breakevens should be directionally related but need not match. The basis can be defined as:

```text
swap_minus_breakeven_basis = ZC inflation swap rate - bond-implied breakeven rate
```

This is the canonical sign convention for this module and should be mirrored in the linker module. The inverse, if needed, should be explicitly named `breakeven_minus_swap_basis`.

A positive value means swaps trade above bond-implied breakevens. Economically, this may mean the swap market prices higher inflation compensation than the linker market, or that linkers are cheap because of liquidity/technical pressure. During stress, the basis can be dominated by linker liquidity rather than inflation expectations. The 2008 TIPS liquidity episode is the canonical reminder: bond breakevens can collapse for liquidity reasons while inflation swaps remain a cleaner, though still imperfect, measure of inflation compensation.

### Cross-market spreads

USD, EUR, and GBP inflation swaps are not directly fungible because the reference indices differ:

- USD: CPI-U NSA.
- EUR: euro-area HICP excluding tobacco.
- GBP: RPI, with a scheduled methodology alignment to CPIH from 2030.

A raw GBP-EUR 10Y inflation swap spread therefore mixes expected UK-vs-euro inflation, RPI-vs-HICP methodology, RPI reform, UK pension demand, and cross-market risk premia. A raw USD-EUR 5Y5Y spread mixes relative inflation credibility, energy sensitivity, fiscal/monetary regimes, and index-basket differences. These spreads are still highly useful, but they should always be labelled as **index-specific inflation compensation differentials**.

### Specific historical episodes worth knowing

**2008-2009 TIPS liquidity crisis and swap-breakeven basis.** During the Lehman crisis, TIPS breakevens collapsed partly because forced sellers and liquidity premia overwhelmed the inflation-expectations signal. New York Fed research notes that the differential between inflation swaps and breakevens exceeded 100bp during the crisis, with debate over whether this reflected TIPS mispricing, liquidity premia in swaps, liquidity premia in TIPS, or a combination. This is the canonical example of why inflation swaps are necessary alongside linker breakevens: the basis itself contains information. [NY Fed Fleming-Sporn Appendix; Fed DKW]

**2014-2016 euro-area disinflation and 5Y5Y ILS.** Euro-area 5Y5Y inflation-linked swap rates fell substantially during the low-inflation period, becoming a central market signal watched by the ECB. ECB material describes the 5Y5Y ILS rate as a widely used longer-term market-based inflation-expectations measure and discusses its fall during this period. This is the canonical European case for using EUR ZCIS forwards in monetary-policy anchoring analysis. [ECB Market-Based Indicators]

**2020-2022 global inflation surge.** Front-end and intermediate inflation swaps repriced sharply as realized inflation rose after Covid reopening, supply-chain disruption, fiscal stimulus, energy shocks, and later the Russia/Ukraine energy shock. Inflation swaps were useful because they moved in real time and avoided some bond-specific linker liquidity distortions. The key product lesson is that the **shape** of the inflation swap curve matters: near-term swaps can price a temporary CPI surge while 5Y5Y stays comparatively anchored, or long-end forwards can rise if markets question central-bank credibility.

**UK RPI reform, 2020 announcement / 2030 implementation.** The UK government and UK Statistics Authority confirmed that RPI methodology will be aligned to CPIH from February 2030, with no compensation to index-linked gilt holders. Because GBP inflation swaps reference RPI, the reform is directly relevant to GBP ZCIS curve interpretation. GBP swaps whose horizons include post-2030 RPI are affected by the expected lower future RPI path. This is a structural break in GBP inflation swap data and a required caveat for any long-dated GBP inflation model. [HM Treasury / UKSA RPI Reform; OBR; LCP]

**UK inflation-swap market segmentation.** Bank of England research using transaction-level UK inflation swap data finds that the market is segmented: pension funds trade at long maturities, hedge funds trade at short maturities, and dealer banks serve as counterparties to both. This matters for interpreting curve moves: the long-end GBP inflation swap curve can reflect liability-hedging flow and pension demand, not just inflation expectations. [Bank of England Market for Inflation Risk]

**Index publication and fallback risk.** Inflation swaps depend on official CPI/RPI/HICP publications. Delayed or revised index publication is an operational risk that must be documented. ISDA has issued guidance for delayed CPI-U publication under the 2008 ISDA Inflation Derivatives Definitions. For production tooling, index-publication calendars and fallback rules are not optional metadata; they are part of the instrument definition. [ISDA CPI-U Delayed Publication Guidance]

---

## 4. Market microstructure (just enough)

**OTC derivative, often cleared.** Inflation swaps are OTC derivatives. Many standard ZCIS trades are cleared at major CCPs such as LCH SwapClear and Eurex, subject to product eligibility by index and maturity. Clearing reduces bilateral counterparty exposure but introduces variation margin, initial margin, clearing fees, and collateral mechanics. Bilateral trades still exist, especially for bespoke structures or maturities.

**Zero-coupon structure.** In a ZCIS, there is no periodic exchange of fixed and inflation-linked coupons during the life of the swap. The fixed and inflation legs settle at maturity, while the trade is marked to market and collateralized/cleared through its life. This is distinct from standard coupon inflation swaps and year-on-year inflation swaps, which have periodic payment structures.

**Quoting convention.** ZCIS rates are quoted as annualized compounded fixed rates in percent or basis points. A 10Y USD ZCIS quote of 2.45% means the par fixed rate that equates the compounded fixed leg to realized CPI-U index growth over the swap term under the applicable convention. It does **not** mean the market literally forecasts CPI-U at 2.45% with certainty.

**Index lag and interpolation.** Index lag is central:

- USD ZCIS in the playbook uses a 3M lag and daily/interpolated index treatment.
- EUR ZCIS in the playbook uses a 3M lag and monthly index treatment.
- GBP ZCIS in the playbook uses a 2M lag and monthly/no-daily-interpolation treatment.

These fields must be stored per ticker and displayed in methodology cards. Do not infer conventions from country alone; verify Bloomberg security metadata, ISDA terms, and clearing-house product terms.

**Inflation index seasonality.** Short-end inflation swaps are highly sensitive to CPI/RPI/HICP seasonality. This is especially true around known CPI prints, energy price seasonality, administered-price dates, tax changes, and index-lag roll dates. A front-end inflation swap tool must distinguish “market-implied inflation compensation” from “mechanical carry because known CPI prints enter the reference index.”

**Liquidity by tenor.** Liquidity is usually best at benchmark tenors such as 1Y, 2Y, 5Y, 10Y, and sometimes 30Y depending on market. EUR and GBP markets may trade beyond 30Y in clearing frameworks, but the current playbook stops at 30Y. Off-pillar tenors require interpolation and should be flagged as model-derived rather than directly quoted.

**Participants.** Inflation swap users include pension funds and insurers hedging inflation-linked liabilities, banks/dealers warehousing and recycling inflation risk, hedge funds trading CPI/fixing/relative-value views, real-money investors, and corporates with inflation-linked revenues or costs. In the UK, pension/liability hedging is structurally important at long maturities.

**Collateral and discounting.** ZCIS valuation requires nominal discount factors and collateral assumptions. The playbook’s `PX_MID` rates are market quotes; precise PV, carry, and scenario tools require OIS discount curves, collateral currency, payment dates, and clearing conventions. For dashboard-level analytics, the rate can be treated as an observed market quote; for P&L and carry, you need the full swap terms.

**Index rebasing and methodology changes.** HICP, RPI, and CPI index levels can be rebased, revised, or subject to methodology/fallback events. Clearing houses and data vendors may apply rebasing keys or adjusted index levels. A robust product needs an `index_base`, `rebasing_flag`, and `index_lineage` field.

---

## 5. Key metrics PMs watch

Per curve in scope:

- **ZCIS rates by tenor:** 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y. The 1Y/2Y points are front-end CPI/fixing-heavy; 5Y and 10Y are core medium-term inflation compensation; 30Y is long-horizon inflation risk/liability demand.

- **Forward inflation swap rates:** 1Y1Y, 2Y1Y, 3Y2Y, 5Y5Y, 10Y10Y where supported. 5Y5Y is the canonical long-horizon anchoring metric.

- **Inflation swap curve spreads:** 1s5s, 2s10s, 5s10s, 5s30s, 10s30s. These describe whether inflation compensation is front-end-led or long-end-led.

- **Swap-breakeven basis:** ZCIS minus bond-implied breakeven at matched tenor. This is a core RV/liquidity metric connecting this module to `04_inflation_indexed_bonds.md`.

- **Cross-market inflation swap spreads:** USD-EUR, GBP-EUR, USD-GBP at 5Y, 10Y, 30Y, and 5Y5Y. These require index-family caveats.

- **Front-end fixing-implied inflation:** 1Y and shorter-dated inflation pricing, where available, adjusted for known CPI prints and seasonality. The current playbook starts at 1Y, but the workflow should eventually include CPI fixing swaps or short-dated inflation forwards.

- **Realized-vs-implied inflation:** realized CPI/RPI/HICP over a horizon compared with the inflation swap rate fixed at trade inception. Useful for post-mortem analysis and model validation.

- **Period changes and z-scores:** 1d, 5d, 22d, 63d, 252d changes; 252d default z-score with 60d/126d tactical alternatives and 504d structural alternative.

- **Seasonality-adjusted rates:** especially for 1Y/2Y, where index lag and seasonal CPI patterns can distort raw comparisons across dates.

- **Liquidity/confidence indicators:** bid/ask spread, quote age, direct-vs-interpolated flag, clearing eligibility, and vendor backfill/proxy flag.

---

## 6. Standard workflows

- **Morning inflation-swap check** — pull USD/EUR/GBP ZCIS levels, period changes, z-scores, curve spreads, and 5Y5Y forwards. → `workflows/morning_briefing.md`

- **Inflation anchoring monitor** — track 5Y5Y and other long-horizon forwards across USD/EUR/GBP; compare to central-bank target context, surveys, and recent realized inflation. → TBD / future `workflows/inflation_expectations.md`

- **Front-end CPI/fixing monitor** — explain moves in 1Y/2Y swaps using known CPI prints, energy prices, base effects, seasonality, and upcoming releases. → TBD

- **Inflation event repricing** — measure how the inflation swap curve changed since CPI, payrolls, central-bank meeting, energy shock, fiscal event, or tariff announcement. → `workflows/morning_briefing.md` / TBD

- **Swap-vs-breakeven basis RV** — compare ZCIS rates to linker-implied breakevens; flag where linkers look cheap/rich versus swaps after liquidity and carry caveats. → `workflows/relative_value.md`

- **Cross-market inflation RV** — compare USD vs EUR vs GBP inflation compensation at matched tenors and forwards, with index-family and RPI-reform caveats. → `workflows/relative_value.md`

- **Inflation curve trades** — front-end vs long-end inflation swap steepeners/flatteners, 5Y vs 10Y, 5Y5Y vs spot, etc. → TBD

- **UK RPI-CPIH reform monitor** — estimate how the GBP RPI swap curve prices the February 2030 methodology transition. → TBD

- **Inflation regime classification** — classify regimes using real yields, breakevens, ZCIS levels, forwards, CPI surprises, and energy moves: disinflation, reflation, stagflation repricing, anchored shock, de-anchoring. → `workflows/regime_classification.md`

- **Stress / historical replay** — replay 2008 swap-breakeven basis stress, 2014-2016 EUR disinflation, 2020-2022 inflation surge, or UK RPI reform through inflation swap positions. → TBD

- **Model workbench / risk premium decomposition** — decompose swap rates into expected inflation and inflation risk premium using model templates. → TBD / Bucket 2

---

## 7. Models and methodologies

**Inflation swap rate level** (1A) — quoted or vendor-composite ZCIS rate for a given curve family, tenor, and date. Deterministic given ticker, field, and date. Caveat: the playbook field `yield_mid` should be renamed or aliased to `swap_rate_mid` in user-facing payloads.

**ZCIS curve spread** (1A) — difference between two tenors on the same inflation swap curve, e.g. USD 2s10s or GBP 5s30s. Simple arithmetic once data and tenor mapping are fixed.

**Cross-market ZCIS spread** (1A with caveats; 1B if index-adjusted) — raw spread between two inflation swap curves at the same tenor, e.g. USD 10Y minus EUR 10Y. Raw arithmetic is 1A; any adjustment for index basket, RPI-CPIH reform, FX, or beta/volatility becomes 1B/2 depending on method.

**Forward inflation swap rate** (1A for fixed formula; 1B when convention is parameterized) — compute forward inflation compensation from two spot ZCIS rates:

```text
forward(a,b) = ((1 + ZCIS_b)^b / (1 + ZCIS_a)^a)^(1/(b-a)) - 1
```

where rates are annualized and expressed in decimal form. For example, 5Y5Y uses 5Y and 10Y rates. The compounding convention must be displayed.

**Z-score and percentile rank** (1A with fixed house default; 1B with custom window/method) — default 252d z-score is a product convention, not a market law. Front-end inflation swaps may need shorter tactical windows because regimes shift quickly around CPI and energy shocks.

**Swap-breakeven basis, simple generic** (1A) — ZCIS rate minus same-tenor generic bond-implied breakeven. Useful for dashboard triage. Must be labelled as generic/screen-level because exact maturity matching and index treatment are ignored.

**Swap-breakeven basis, exact** (1B) — compare ZCIS to a matched linker/nominal breakeven using specific bonds, maturity interpolation, index ratios, carry, floor effects, and liquidity assumptions. This is a serious RV tool and should expose all assumptions.

**Seasonality adjustment / CPI carry model** (1B) — adjust front-end inflation swap rates for known CPI prints, seasonal factors, and index-lag mechanics. Parameters: CPI seasonal model, known-vs-forecast CPI treatment, energy assumptions, horizon, and index family.

**Inflation swap curve construction** (1B) — build a smooth inflation zero/forward curve from observed ZCIS pillars. Parameters: interpolation method, extrapolation rule, seasonality treatment, index-lag adjustment, direct-vs-interpolated pillar handling.

**UK RPI-CPIH transition monitor** (1B) — deterministic framework to estimate the wedge priced around February 2030 using GBP RPI ZCIS. Parameters: curve-fitting method, pre/post-2030 cash-flow treatment, CPIH/RPI wedge assumption, and selected tenors.

**Inflation event repricing** (1B) — compare the inflation swap curve before and after a specified event date. Deterministic given timestamps, curve pillars, calendars, and interpolation. This is high-value for CPI, central-bank, energy, and fiscal events.

**Inflation swap carry / roll** (1B) — expected P&L contribution from carrying a ZCIS position over a horizon, given discounting, curve roll, index accrual, collateral assumptions, and CPI seasonality. Requires more than the current `PX_MID` playbook.

**Parametric scenario** (1B) — apply a user-specified shock to ZCIS levels or forwards, e.g. +50bp front-end inflation shock, +25bp 5Y5Y de-anchoring shock, or curve twist. Deterministic once shock vector and position spec are fixed.

**Historical replay / stress engine** (1B) — replay historical ZCIS paths through a position. Bucket 1B because the calculation is deterministic once the user chooses the window, instruments, positions, interpolation, and carry assumptions. A probabilistic inflation-path simulator would be Bucket 2.

**Beta-adjusted cross-market inflation RV** (1B/2 boundary) — rolling regression of one ZCIS series on another. If used as descriptive hedge-ratio computation, 1B. If residual is treated as fair-value signal, Bucket 2.

**Cointegration of inflation swap pairs / swap-breakeven basis** (Bucket 2) — statistical inference about long-run relationships between inflation swap curves, or between swaps and bond breakevens. Output is model-dependent.

**PCA on inflation swap curve** (Bucket 2) — PCA on ZCIS changes across tenors. Outputs level/slope/curvature-style inflation compensation factors.

**Multi-market inflation PCA** (Bucket 2) — joint PCA across USD/EUR/GBP curves and tenors to identify global inflation-compensation factors and local residuals.

**Inflation risk premium decomposition** (Bucket 2) — affine or state-space model combining inflation swaps, inflation-linked bonds, nominal yields, realized inflation, and surveys to decompose swap rates into expected inflation and inflation risk premium. Model output must be labelled as estimated, not observed.

**HMM inflation regime classifier** (Bucket 2) — regime model using features such as ZCIS changes, 5Y5Y changes, breakeven changes, real-yield changes, CPI surprises, energy prices, and rates vol. Output: state probabilities and transition matrix.

**GARCH / stochastic volatility on inflation swaps** (Bucket 2) — conditional volatility model for inflation swap changes. Useful for risk scaling and vol-regime detection.

**Out of scope deliberately for this module:** standalone CPI forecasting, trade recommendations, position sizing advice, inflation options pricing, XVA/collateral optimization, and bespoke inflation exotic pricing.

---

## 8. Data requirements

### 8.1 Current playbook fields

**Time-series fields, per ticker / business day:**

- `yield_mid` — current storage name for Bloomberg `PX_MID`. Economically this is the zero-coupon inflation swap rate. User-facing APIs should alias this to `swap_rate_mid` or `zcis_rate_mid`.

**Reference / static fields, per ticker:**

- `maturity_date` — Bloomberg `MATURITY`.
- `security_name` — Bloomberg `SECURITY_DES`.
- `underlying_index` — Bloomberg `SWAP_PRIMARY_INDEX`.
- `ticker` — Bloomberg ticker, e.g. `USSWIT10 Curncy`.
- `instrument_type` — `inflation_swap`.
- `pricing_type` — current playbook value `zero_coupon_breakeven`; canonical analytics-layer value should be `zcis_rate` or `zero_coupon_inflation_swap_rate`. Treat `zero_coupon_breakeven` as raw-playbook legacy metadata, not bond-breakeven terminology.
- `curve_family` — `USD_ZCIS`, `EUR_ZCIS`, `GBP_ZCIS`.
- `country` — `US`, `EU`, `UK`.
- `currency` — `USD`, `EUR`, `GBP`.
- `tenor` — 1Y, 2Y, 3Y, 5Y, 10Y, 20Y, 30Y.
- `inflation_index_family` — current playbook values: `US_CPI_URBAN`, `EU_HICP`, `UK_RPI`. Canonical analytics-layer value for EUR should be `EA_HICPXT`; treat `EU_HICP` as a legacy shorthand for euro-area HICP excluding tobacco.
- `index_lag` — 3M for USD/EUR; 2M for GBP in the current playbook.
- `interpolation` — Daily for USD; Monthly for EUR/GBP in the current playbook.

### 8.2 Required additional fields for production-quality analytics

**Market data fields:**

- `swap_rate_bid` / `swap_rate_ask` — bid/ask ZCIS rates.
- `bid_ask_spread` — derived liquidity proxy.
- `quote_source` — Bloomberg composite, dealer composite, BVAL, clearing-house curve, etc.
- `quote_timestamp` — especially important for intraday or stale quote detection.
- `direct_quote_flag` — direct market quote vs vendor-interpolated point.
- `vendor_backfill_flag` — whether the series includes backfilled/proxy history.
- `clearing_eligibility` — eligible CCP(s), if relevant.
- `liquidity_score` — internal score combining bid/ask, quote age, tenor, and source confidence.

**Convention fields:**

- `index_source` — CPURNSA, HICPxT, UKRPI, etc.
- `index_lag_months` — numeric lag.
- `interpolation_method` — linear daily, monthly/non-interpolated, or other.
- `initial_index_rule` — how `I_0` is determined.
- `final_index_rule` — how `I_T` is determined.
- `effective_date_rule` — spot, IMM, 15th of month, or other.
- `maturity_date_rule` — exact end-date convention.
- `payment_date_rule` — payment delay and business-day adjustment.
- `fixed_leg_compounding` — annualized compounded rate convention for fixed leg.
- `calendar` — London, New York, TARGET, joint calendars.
- `business_day_convention` — modified following, following, none, etc.
- `collateral_currency` — relevant for valuation.
- `discount_curve_family` — OIS curve used for PV calculations.

**Inflation-index data:**

- Monthly CPI-U NSA levels.
- Monthly euro-area HICPxT levels.
- Monthly UK RPI levels.
- CPI/RPI/HICP release calendar.
- Data-revision/fallback flags.
- Index rebasing history.
- RPI-CPIH reform metadata.
- Known CPI prints and publication lag.

**Cross-domain dependencies:**

- Nominal OIS curves from `02_ois_swaps.md` — discounting, scenario valuation, and PV.
- Linker-implied breakevens from `04_inflation_indexed_bonds.md` — swap-breakeven basis.
- Real yields and nominal sovereign yields — decomposition and bond comparison.
- Energy prices, FX, commodities — optional explanatory variables for CPI/fixing workflows.
- Survey expectations — SPF, Michigan, NY Fed SCE, ECB SPF, BoE/Ipsos-style surveys for anchoring comparisons.
- Inflation options / caps/floors — optional for implied distribution and inflation skew.

### 8.3 Data-lineage notes

- **USD ZCIS:** The playbook uses `USSWIT` tickers and starts extraction from 2005. Confirm Bloomberg ticker lineage, whether history is pure dealer composite, BVAL, or vendor backfill, and whether early-tenor quotes are direct or interpolated. Front-end USD ZCIS requires careful handling of known CPI prints and 3M lag.

- **EUR ZCIS:** The playbook uses `EUSWI` tickers and euro-area HICPxT. Confirm migration from raw playbook label `EU_HICP` to canonical analytics label `EA_HICPXT`, because euro inflation swaps usually reference euro-area HICP excluding tobacco. Verify whether `interpolation: Monthly` means no daily interpolation or monthly index-level treatment under Bloomberg convention.

- **GBP ZCIS:** The playbook uses `BPSWIT` tickers and UK RPI with 2M lag. This is not the same lag structure as modern UK index-linked gilts. RPI reform from February 2030 is a hard structural caveat for long-dated GBP swaps. The data model should include `rpi_reform_exposure_flag` for maturities with post-2030 exposure.

- **Tenor coverage:** Current playbook stops at 30Y. Market/clearing frameworks may support longer maturities, especially GBP and EUR. The 30Y cap is a playbook design choice, not necessarily the market limit.

- **Field naming:** `yield_mid` is inherited from generic rates schema. For this domain it should be aliased as `swap_rate_mid` in payloads and tool output to avoid conceptual confusion.

---

## 9. Tool inventory

Format: **`tool_name`** — takes [inputs], runs [model/computation], returns [outputs], used in [workflows] to surface [macro implication].

### Built

*None yet specifically built for inflation swaps. Existing rates tools can be cloned structurally, but the domain requires inflation-specific terminology, index conventions, and basis/carry logic. Do not expose `yield_mid` language to users for inflation swaps.*

### Planned (Bucket 1A)

**`inflation_swap_rate_level`** — takes `curve_family`, `tenor`, and `date`; queries `swap_rate_mid` / current `yield_mid`; returns ZCIS rate level, 1d/5d/22d changes, 252d high/low/percentile, and z-score; used in `morning_briefing` to surface where inflation compensation sits relative to recent history.

**`inflation_swap_curve_spread`** — takes one curve family and two tenors; computes the tenor spread, period changes, z-score, and time series; used to identify front-end-led vs long-end-led inflation repricing.

**`inflation_swap_forward`** — takes one curve family and a forward window defined by two tenors, e.g. 5Y and 10Y for 5Y5Y; computes forward inflation swap rate using the fixed house compounding convention; returns forward level, period changes, z-score, and formula metadata.

**`cross_market_inflation_swap_spread`** — takes two curve families and a tenor or forward window; computes raw spread, period changes, and z-score; used in cross-market inflation RV with index-family caveats.

**`inflation_swap_scanner`** — scans all USD/EUR/GBP ZCIS pillars for z-score extremes above threshold; returns ranked extremes with curve, tenor, current rate, percentile, and latest move.

**`swap_breakeven_basis_simple`** — takes a curve family, country/linker family, tenor, and date; computes ZCIS rate minus generic linker-implied breakeven; returns basis level, z-score, and time series; used to flag linker-vs-swap RV opportunities.

**`inflation_swap_event_change`** — takes a curve family, event date, and tenors; computes pre/post changes in ZCIS levels and forwards; returns event repricing table. This is deterministic if dates and curves are fixed.

### Planned (Bucket 1B)

**`zscore_custom`** — takes any inflation swap series, lookback window, and standardization method; returns custom z-score and percentile. Needed because front-end inflation swaps may require tactical windows shorter than 252d.

**`forward_inflation_parameterized`** — computes forwards for arbitrary windows with user-selected compounding convention, interpolation, and start/end treatment. Used for 1Y1Y, 2Y3Y, 5Y5Y, 10Y10Y, and custom event windows.

**`inflation_curve_builder`** — takes ZCIS pillars and curve construction parameters; builds a smooth zero/forward inflation curve. Parameters: interpolation method, seasonality adjustment, extrapolation rule, and direct-vs-interpolated flags.

**`seasonality_adjusted_front_end`** — takes a curve family, front-end tenor, known inflation prints, release calendar, and seasonal model; decomposes front-end ZCIS into known inflation, expected remaining inflation, and residual risk premium/technical component.

**`cpi_fixing_path`** — takes an index family, start date, end date, known prints, and assumptions for missing months; constructs an implied CPI/RPI/HICP path consistent with front-end swap quotes. This is underdetermined unless assumptions are imposed for unknown monthly prints; the tool must disclose whether it assumes flat monthly inflation, seasonal patterns, market fixing inputs, or user-specified monthly values. It should be clearly labelled as an implied path, not a forecast.

**`swap_breakeven_basis_exact`** — takes a ZCIS curve, selected nominal/linker bonds or generic curves, maturity matching method, carry/floor assumptions, and index conventions; computes exact or near-exact linker-vs-swap basis. This is a serious RV tool and needs transparent assumptions.

**`uk_rpi_cpih_transition_monitor`** — takes GBP ZCIS curve and RPI reform metadata; estimates the pre/post-2030 wedge priced in the GBP inflation curve. Outputs implied RPI-CPIH transition effect with methodology card.

**`cross_market_index_adjusted_spread`** — adjusts raw USD/EUR/GBP inflation swap spreads for index-basket differences, RPI-CPIH reform, and optionally historical beta. Assumptions must be visible.

**`inflation_swap_carry_roll`** — computes expected carry and roll for a ZCIS position over a user-selected horizon using discount curves, curve roll, index lag, and known/forecast CPI. Requires OIS curves and CPI data.

**`parametric_scenario_inflation_swaps`** — applies user-defined shocks to ZCIS curves or forwards; returns rate changes and position P&L if position data is supplied.

**`historical_replay_inflation_swaps`** — replays historical inflation swap curve paths through selected positions; returns P&L path and decomposition. Bucket 1B because the replay is deterministic once parameters are chosen.

### Planned (Bucket 2)

**`pca_inflation_swap_curve`** — takes curve family, tenor set, and lookback; computes PCA on ZCIS changes; returns loadings, factor time series, variance explained, and current factor moves.

**`multi_market_inflation_pca`** — computes joint PCA across USD/EUR/GBP curves; separates global inflation-compensation factors from local index/country residuals.

**`inflation_risk_premium_decomposition_swaps`** — estimates expected inflation vs inflation risk premium from ZCIS using affine/state-space models and optional survey/realized inflation inputs. Outputs are model-dependent.

**`hmm_inflation_swap_regime`** — fits HMM or other regime model to ZCIS rates/forwards, breakevens, real yields, CPI surprises, and energy prices; returns regime probabilities and transition matrix.

**`garch_inflation_swap_vol`** — estimates conditional volatility of inflation swap changes at selected tenors.

**`cointegration_swap_breakeven_basis`** — tests whether ZCIS and bond breakevens share a stable long-run relation; returns equilibrium relationship, residual, and statistical diagnostics.

**`probabilistic_inflation_path`** — generates a distribution of CPI/RPI/HICP outcomes consistent with inflation swap curves and selected model assumptions. This is explicitly probabilistic and should not be presented as deterministic market pricing.

### Aspirational (data-blocked or further out)

**`yoy_inflation_swap_curve`** — requires YoY inflation swap data. Useful for periodic inflation exposure and some European workflows.

**`inflation_options_surface`** — caps/floors and inflation swaptions; used to infer inflation distribution, skew, and tail pricing.

**`cpi_fixing_swaps`** — requires short-dated fixing instruments; valuable for CPI release and fixing-market workflows.

**`sdr_liquidity_monitor`** — uses SDR/EMIR/DTCC trade repository data where available to estimate actual traded liquidity by tenor and participant type.

**`real_time_cpi_nowcast_overlay`** — combines high-frequency energy, food, rent, used-car, and other component data to compare nowcast CPI with inflation swap pricing.

---

## 10. Open questions

- *#schema* — Should `yield_mid` be renamed to `swap_rate_mid` at the canonical analytics layer, while preserving the DB field for schema compatibility?

- *#convention* — Confirm Bloomberg convention for each ticker: USD daily interpolation, EUR monthly/no-daily interpolation, GBP monthly/no-daily interpolation. Do not rely solely on playbook shorthand.

- *#convention* — Should EUR `inflation_index_family` be migrated from raw playbook label `EU_HICP` to canonical analytics label `EA_HICPXT` to avoid confusion with headline HICP including tobacco and with the broader EU rather than euro-area index family?

- *#convention* — What compounding convention should be the default for forward inflation swap rates? FRED-style annual compounding is transparent; dealer screens may differ for specific products.

- *#convention* — For GBP RPI swaps, how do desks quote and adjust around the February 2030 RPI-CPIH methodology change? Is there a standard curve break treatment?

- *#workflow-validation* — Do macro PMs use raw ZCIS levels more, or forward swap rates such as 5Y5Y and 1Y1Y?

- *#workflow-validation* — For inflation anchoring, do desks prefer inflation swaps over linker breakevens, or use both side-by-side with basis commentary?

- *#workflow-validation* — How important is swap-breakeven basis in discretionary macro pods versus specialist inflation/RV desks?

- *#workflow-validation* — Are 1Y/2Y ZCIS instruments sufficient for front-end CPI workflows, or do we need actual CPI fixing swaps / fixing curves?

- *#workflow-validation* — Do PMs want cross-market inflation swap spreads displayed raw, index-adjusted, beta-adjusted, or all three?

- *#data* — Do Bloomberg `USSWIT`, `EUSWI`, and `BPSWIT` histories include vendor backfill or interpolated history before robust liquidity? Need field-level lineage.

- *#data* — We need bid/ask data for liquidity confidence. Is `PX_BID` / `PX_ASK` available consistently for these tickers?

- *#data* — CPI/RPI/HICP release calendars and index levels are required for front-end decomposition and carry. What is the ingestion source and schema?

- *#data* — Should we ingest 50Y GBP and EUR inflation swap tenors if users care about long liability-hedging points, even though current playbook stops at 30Y?

- *#scope* — Should French CPIxT swaps be included alongside EUR HICPxT because French OATi/OATei basis may matter for Europe inflation RV?

- *#scope* — Should YoY inflation swaps be a separate module or a subsection of this module once ingested?

- *#methodology* — For swap-breakeven basis, should simple generic basis be MVP, with exact matched-bond basis later? My current recommendation: yes.

- *#methodology* — For inflation risk premium decomposition, should the first model be survey-augmented state-space or a simpler affine term-structure template?

- *#methodology* — How should the product represent risk-neutral vs real-world expectations in natural language? Need standard wording to prevent “market expects” overclaiming.

---

## 11. References

- *[ISDA Inflation Definitions]* — ISDA. *2008 ISDA Inflation Derivatives Definitions* and related guidance; 2021 Definitions upgrade materials for inflation swap confirmations.

- *[ISDA CPI-U Delayed Publication Guidance]* — ISDA. *Guidance for Delayed Publication of CPI-U under the 2008 ISDA Inflation Derivatives Definitions*.

- *[NY Fed Fleming-Sporn 2013]* — Fleming, M. J., & Sporn, J. (2013). “Trading Activity and Price Transparency in the Inflation Swap Market.” Federal Reserve Bank of New York, *Economic Policy Review*.

- *[NY Fed Fleming-Sporn Appendix]* — Fleming, M. J., & Sporn, J. Staff Report appendix on inflation swap trading activity, liquidity, and swap-breakeven differentials.

- *[Fed Inflation Swaps Advanced Economies]* — Federal Reserve IFDP Notes. “Drivers of Inflation Compensation: Evidence from Inflation Swaps in Advanced Economies.”

- *[Fed DKW]* — D'Amico, S., Kim, D. H., & Wei, M. “Tips from TIPS: The Informational Content of Treasury Inflation-Protected Security Prices” and Fed updates on TIPS inflation compensation.

- *[ECB Market-Based Indicators]* — ECB Economic Bulletin. “Interpreting recent developments in market-based indicators of longer-term inflation expectations.”

- *[ECB Inflation Expectations]* — ECB occasional papers and working papers on inflation expectations, inflation-linked swaps, and market-based measures.

- *[Bank of England Market for Inflation Risk]* — Bahaj, S., Czech, R., Ding, S., & Reis, R. “The Market for Inflation Risk.” Bank of England Staff Working Paper / Bank Underground summary.

- *[BLS]* — U.S. Bureau of Labor Statistics. Consumer Price Index for All Urban Consumers (`CPI-U`) documentation and releases.

- *[Eurostat]* — Eurostat. Harmonised Index of Consumer Prices (`HICP`) and HICP excluding tobacco datasets.

- *[ONS]* — Office for National Statistics. Retail Prices Index (`RPI`) methodology and monthly publications.

- *[HM Treasury / UKSA RPI Reform]* — UK Government and UK Statistics Authority. Response to the consultation on the reform to RPI methodology, November 2020.

- *[OBR RPI-CPI wedge]* — Office for Budget Responsibility. Discussion of the long-run difference between RPI and CPI/CPIH and the 2030 alignment.

- *[LCP RPI Reform]* — Lane Clark & Peacock. “RPI will be aligned to CPIH from 2030 with no compensation for holders of index-linked gilts.”

- *[TP ICAP US CPI ZCIS]* — TP ICAP / Coex Partners. Market template for US CPI zero-coupon inflation swap.

- *[TP ICAP HICPxT ZCIS]* — TP ICAP / Coex Partners. Market template for HICPxT zero-coupon inflation swap.

- *[TP ICAP UK RPI ZCIS]* — TP ICAP / Coex Partners. Market template for UK RPI zero-coupon inflation swap.

- *[LCH Inflation Swaps]* — LCH / CFTC self-certification filings and SwapClear product materials for zero-coupon and standard coupon inflation swaps.

- *[Eurex Inflation Swaps]* — Eurex Clearing. Inflation swaps product page and eligibility information.

- *[Bloomberg Inflation Swap Tracker Methodology]* — Bloomberg. *Inflation Swap Tracker Indices Methodology*, for notional rolling ZCIS index construction and backtest assumptions.

- *[OpenGamma Inflation Instruments]* — OpenGamma. “Inflation Instruments: Zero-Coupon Swaps and Bonds.” Practitioner reference for inflation derivative mechanics and conventions.

- *[Deacon-Derry-Mirfendereski]* — Deacon, M., Derry, A., & Mirfendereski, D. *Inflation-Indexed Securities: Bonds, Swaps and Other Derivatives.* Wiley.

- *[Kerkhof 2005]* — Kerkhof, J. “Inflation Derivatives Explained.” Practitioner reference on inflation derivatives, pricing, and conventions.
