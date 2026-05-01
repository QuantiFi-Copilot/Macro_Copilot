# Inflation-Indexed Bonds

**Status:** v3 internal draft — revised for factual precision, convention caveats, data-lineage requirements, and bucket-classification discipline. Still to be validated against live Bloomberg playbooks, Brevan feedback, and advisory contacts.

**Scope:** 24 instruments across 4 countries currently in `/playbooks/inflation_indexed_bonds.yml`: US TIPS, UK Index-Linked Gilts, French OATei, and Canadian Real Return Bonds. The playbook currently includes US TIPS generic real-yield benchmarks at 5Y, 10Y, 20Y, and 30Y; UK linker generic real-yield benchmarks at 1Y, 2Y, 3Y, 5Y, 10Y, 15Y, 20Y, 30Y, and 50Y; French OATei generic real-yield benchmarks at 2Y, 5Y, 7Y, 10Y, and 15Y; and Canadian RRB generic real-yield benchmarks at 5Y, 10Y, 15Y, 20Y, 25Y, and 30Y.

**Cross-references:** Breakeven inflation = nominal yield (`01_sovereign_bonds.md`) minus real yield (this module). Breakeven is *derived* from the nominal and real curves jointly, not owned by either module alone. Forward breakeven and 5Y5Y breakeven forward use the same general forward-rate logic as `02_ois_swaps.md`, but convention choices must be explicitly stated. Inflation swaps (zero-coupon and year-on-year) are related but distinct instruments; zero-coupon inflation swaps now live in `05_inflation_swaps.md`.

---

## 1. What it is

An inflation-indexed bond, also called a linker, is a sovereign debt instrument whose principal and coupon payments are linked to a published consumer price index. The bond pays a fixed real coupon on an inflation-adjusted principal. As the reference price index rises, the adjusted principal rises and coupon cash flows rise with it. If the reference price index falls, the adjusted principal can fall as well, subject to market-specific deflation-floor terms.

The *real yield* on a linker is the yield-to-maturity computed on the inflation-linked cash flows under the relevant indexation, settlement, coupon, and day-count conventions. It represents the market's required real rate of return after inflation indexation, not a direct forecast of realized real economic growth.

The difference between a nominal sovereign yield and the corresponding real yield is the *breakeven inflation rate*. Breakeven is best understood as **inflation compensation**, not pure expected inflation. It is an approximation of market-implied inflation compensation for the relevant horizon, but it embeds inflation risk premium, linker liquidity premium, nominal-bond liquidity/convenience effects, indexation technicals, and sometimes deflation-floor value.

In scope for this module, by country and reference index:

- **United States — TIPS (Treasury Inflation-Protected Securities).** Reference index: CPI-U NSA (Consumer Price Index for All Urban Consumers, not seasonally adjusted). Indexation lag: 3 months with daily interpolation. Deflation floor: yes, on principal at maturity. Coupon: semiannual, fixed real rate on adjusted principal. The Treasury currently issues 5Y, 10Y, and 30Y TIPS. A 20Y TIPS sector existed historically, but new 20Y issuance was discontinued in 2009; any 20Y TIPS generic in the playbook should therefore be treated as a generic/constant-maturity or outstanding-sector benchmark, not as an actively issued on-the-run tenor. First TIPS issuance: January 1997. [US Treasury; TreasuryDirect]

- **United Kingdom — Index-Linked Gilts.** Reference index: Retail Price Index (RPI). Indexation lag: 3 months for gilts first issued from September 2005 onward; 8 months for older legacy issues. Deflation floor: no TIPS-style par floor at maturity. Coupon: semiannual. Tenors in playbook: 1Y, 2Y, 3Y, 5Y, 10Y, 15Y, 20Y, 30Y, 50Y. First issued March 1981. **Critical:** RPI will be aligned to CPIH from 2030, with no compensation to existing holders. RPI has historically run roughly 1% per annum above CPIH on average, so this is a material regime change for long-dated UK linkers. Post-2030-maturity linker cash flows are not pure CPIH: they are a blend of pre-2030 cash flows linked to current RPI methodology and post-reform cash flows linked to RPI aligned with CPIH. [UK DMO; LCP; Economic History Society]

- **France — OATei (Obligations Assimilables du Trésor indexées sur l'inflation européenne).** Reference index: Euro-area HICP excluding tobacco. Indexation lag: 3 months with interpolation. Deflation floor: yes, on principal at maturity. Coupon: annual. Tenors in playbook: 2Y, 5Y, 7Y, 10Y, 15Y. The first French CPI-linked OATi was issued in 1998; the euro-area HICP-linked OATei variant followed in 2001. **Note:** the playbook tags French linkers as `EU_HICP`, meaning these are OATei, not French-CPI-linked OATi. The distinction matters for French domestic inflation basis risk. [Agence France Trésor]

- **Canada — Real Return Bonds (RRBs).** Reference index: Canada CPI all-items, not seasonally adjusted. Indexation lag: approximately 3 months with interpolation. Deflation floor: no TIPS-style par floor should be assumed; the final nominal repayment varies with the index ratio and may be below original principal if cumulative indexation is negative. Coupon: semiannual. Tenors in playbook: 5Y, 10Y, 15Y, 20Y, 25Y, 30Y. First issued 1991. **Critical:** Canada ceased new RRB issuance after the November 2022 Fall Economic Statement, citing low demand and a desire to consolidate issuance in more liquid core sectors. Outstanding RRBs remain relevant for existing books, liability hedgers, and specialist RV analysis, but the CAD linker curve should be treated as a declining-liquidity legacy market. [Government of Canada 2022 Fall Economic Statement; Bank of Canada; C.D. Howe Institute]

Bloomberg tickers in the playbook follow generic benchmark convention: `GTII10 Govt` for the US 10Y TIPS generic, `GTGBPII10Y Govt` for the UK 10Y index-linked gilt generic, etc. These are benchmark real-yield series, not individual CUSIPs/ISINs. Bloomberg may roll generic series to track benchmark/on-the-run or constant-maturity sectors. Exact carry, settlement, floor valuation, and linker-vs-nominal RV require actual bond-level data, not only generic real-yield series.

---

## 2. Why it exists / role in the system

Inflation-indexed bonds exist at the intersection of four economic functions that matter for how a PM thinks about them.

**Real rate of return as a distinct asset class.** A nominal bond embeds an inflation assumption that the holder cannot unbundle. A linker separates nominal duration into real-rate exposure and inflation compensation. For liability-driven investors — pension funds, insurers, and other institutions whose liabilities are linked to inflation — linkers are structural hedging instruments that nominal bonds cannot perfectly replicate. UK pension demand for index-linked gilts is a canonical example. The September-October 2022 UK LDI crisis was not simply a linker crisis, but long-duration gilt and linker exposures were central to the liability-hedging ecosystem.

**Market-implied inflation compensation.** Breakeven inflation derived from comparing linker yields with nominal yields is one of the most widely cited market-based measures of inflation compensation. The 5Y breakeven, 10Y breakeven, and 5Y5Y forward breakeven are watched by central banks, real-money investors, inflation desks, and macro PMs as indicators of inflation-compensation and anchoring. They should not be described as pure inflation expectations because they include inflation risk premium, liquidity premium, and market technicals.

**Inflation carry and relative value.** A linker return has two major components: real-duration return and inflation indexation accrual. A nominal bond earns nominal yield and price return. A long linker / short nominal breakeven trade expresses a view that realized inflation plus linker technicals will outperform the breakeven priced at entry. Conversely, short linker / long nominal expresses the opposite. For macro PMs, this is the instrument-level expression of an inflation view, but it is not a pure spot-CPI bet unless the position is very short maturity or explicitly hedged for real-duration exposure.

**Real-rate macro signal.** Real yields are macro state variables in their own right. A rise in real yields is often a tightening of financial conditions even if nominal yields are unchanged. A decline in real yields can reflect easier policy, lower real growth expectations, lower real term premium, QE/liquidity effects, or a combination. This is why real yields are central to cross-asset analysis: equity duration, gold, FX, EM, and credit all respond to the real-rate component of nominal yields.

**Complication: linkers are not pure inflation bets.** Intermediate and long linkers have meaningful real-rate duration. In 2022, TIPS lost roughly 12% despite high realized CPI because the real-yield selloff overwhelmed inflation accrual. Short-maturity linkers are closer to pure inflation exposure because they have much lower real-duration sensitivity, but even there indexation lag, carry, liquidity, and floor effects matter.

---

## 3. What its movements signal (macro context)

### The breakeven decomposition

The breakeven inflation rate is the headline number derived from linkers, but it is not a pure measure of expected inflation. A standard first-pass decomposition is:

```text
breakeven ≈ expected inflation
          + inflation risk premium
          - linker liquidity premium
          + nominal-bond liquidity / convenience effects
          + indexation, seasonality, tax, and deflation-floor technicals
```

For US TIPS, the canonical D'Amico-Kim-Wei style decomposition is often written more compactly as:

```text
TIPS inflation compensation = expected inflation
                            + inflation risk premium
                            - TIPS liquidity premium
```

The inflation risk premium compensates nominal-bond holders for bearing inflation uncertainty, but it is time-varying and model-dependent; it should not be assumed to be always positive in every regime. The linker liquidity premium reflects the lower liquidity of linkers relative to nominal sovereigns; it pushes real yields higher and breakevens lower. Under calm conditions, breakevens are often useful proxies for market inflation expectations. Under stress, the wedge can become very large, as in 2008.

### Real yields

Real yields are a macro signal distinct from breakevens. The 10Y TIPS real yield is one of the key traded proxies for the ex-ante real cost of capital in the dollar economy. Negative 10Y real yields, common during large parts of the 2012-2022 period, reflected some combination of accommodative policy, QE/liquidity effects, low real-growth expectations, safe-asset demand, and compressed real term premium. The return of positive real yields in 2022-2023 was one of the major tightening signals of the cycle.

A real-yield move answers a different question from a breakeven move:

- Real yields up, breakevens flat/down: real tightening / policy credibility / growth-risk repricing.
- Real yields down, breakevens up: reflationary easing / inflation compensation rising while real conditions ease.
- Real yields up, breakevens up: nominal selloff driven by both real-rate and inflation-compensation repricing.
- Real yields down, breakevens down: disinflationary growth shock / flight to safety / recession pricing.

### Specific historical episodes worth knowing

**2008-2009 TIPS liquidity crisis.** In the Lehman aftermath, TIPS breakevens collapsed and briefly went negative at the 5Y point. A naive reading would have said the market expected sustained deflation. A better reading is that linker liquidity premia exploded: forced sellers, deleveraging, thin TIPS liquidity, and market dysfunction pushed TIPS prices down and breakevens artificially low. The 5Y5Y forward breakeven reached an extreme high around the same period because the forward calculation mechanically amplified distortions between 5Y and 10Y breakevens. This is the canonical case study for why breakevens are inflation compensation, not pure expectations.

**2020-2022 inflation surge and real-rate shock.** US 10Y breakevens rose from around 1% in March 2020 to around 3% by April 2022, repricing inflation compensation dramatically. TIPS outperformed nominals in 2020 and 2021 as inflation accrual and breakeven widening helped. In 2022, however, TIPS lost roughly 12% despite high CPI because real yields rose sharply as the Fed tightened. This is the canonical recent example of why intermediate/long linkers are real-duration instruments, not pure inflation bets.

**UK RPI reform announcement, November 2020.** The UK announced that RPI would be aligned to CPIH from 2030, with no compensation to existing index-linked gilt holders. Since CPIH has historically run roughly 1% below RPI on average, long-dated UK linker cash flows were repriced. This creates a regime break in UK linker data. Breakevens on bonds maturing before 2030 still primarily reflect current RPI methodology; breakevens on bonds with cash flows beyond 2030 embed the transition to RPI aligned with CPIH. Any model or z-score spanning the maturity spectrum must handle this bifurcation.

**Canada RRB discontinuation, November 2022.** Canada ceased new RRB issuance after the 2022 Fall Economic Statement. Outstanding RRBs continue to trade, but no new issuance means declining benchmark quality and liquidity over time. The CAD linker curve remains relevant for outstanding positions and liability hedging but should be flagged as a legacy, declining-liquidity market in scanners, z-score tools, and regime models.

**CPI seasonality and carry dynamics.** CPI-U NSA exhibits seasonal patterns. Because TIPS accrue inflation on an NSA basis with a lag, TIPS carry is seasonal. Positive carry tends to be stronger in historically high-seasonal-inflation months and weaker in low-seasonal-inflation months, but the exact pattern is time-varying and should be modelled rather than hard-coded. Macro PMs running linker-vs-nominal RV trades must account for CPI seasonality, base effects, known prints, and indexation lag.

---

## 4. Market microstructure (just enough)

**Quoting convention.** The Bloomberg generic tickers in this playbook return real-yield benchmark series. In the cash market, linkers may be traded, marked, or discussed in real clean price, real yield, breakeven, asset-swap spread, or inflation-swap-basis terms depending on the market and workflow. For settlement and carry, actual bond-level data is required: clean real price, dirty price, index ratio, accrued interest, coupon, maturity, base index, and deflation-floor terms.

**Index ratio and indexation lag.** Linker principal is adjusted using an index ratio based on the relevant consumer price index. TIPS use CPI-U NSA with a 3-month lag and daily interpolation. UK linkers use RPI, with an 8-month lag for older issues and 3-month lag for newer issues. French OATei use euro-area HICP excluding tobacco, with a 3-month lag and interpolation. Canadian RRBs use Canada CPI with lagged/interpolated reference CPI. This lag means recent inflation prints are not immediately fully reflected in the principal index ratio; short-dated linkers can therefore be very sensitive to CPI surprises and carry changes.

**Front-end indexation sensitivity.** A one-month CPI surprise has a much larger yield-equivalent effect on very short linkers than on 10Y linkers because the same near-term indexation shock is amortized over a much shorter duration. The exact bp impact is bond-specific and depends on maturity, duration, index-ratio timing, known CPI prints, accrued indexation, and whether the surprise changes only near-term carry or the broader inflation path.

**Deflation floor.** TIPS and French OATei/OATi have a par-style deflation floor at maturity. UK index-linked gilts do not have a TIPS-style par floor. Canadian RRBs should not be assumed to have a TIPS-style par floor; the final nominal repayment varies with the index ratio and may be below original principal. The deflation floor is an embedded option. Its value is highest for newly issued linkers with index ratios near 1.0 and low cumulative inflation accrual; it is often negligible for seasoned bonds with substantial indexation.

**Liquidity.** Linkers are structurally less liquid than their nominal counterparts. US TIPS are the deepest linker market globally but still generally trade with wider bid-ask spreads than nominal USTs. UK linkers are large and important but can be thin, especially in the very long end. French OATei have moderate liquidity. Canadian RRB liquidity has been impaired by the cessation of new issuance. This liquidity differential is itself a tradeable basis and a source of risk; the 2008 TIPS episode was fundamentally a liquidity-premium shock.

**Auction and issuance calendars.** US Treasury currently issues 5Y, 10Y, and 30Y TIPS on a regular schedule with reopenings. The 20Y TIPS sector existed historically but is no longer actively issued. UK linker issuance follows the DMO calendar. French OATei/OATi issuance is managed by Agence France Trésor. Canada no longer issues new RRBs. Issuance and auction calendars matter for supply concessions, benchmark roll behaviour, and linker-specific liquidity.

**Phantom income / taxation.** In the US, inflation adjustment to TIPS principal is taxable in the year it accrues even though the holder does not receive the principal adjustment in cash until maturity. This creates phantom income for taxable investors and is one reason TIPS are often held in tax-sheltered or institutional accounts. UK, French, and Canadian tax treatment differs and should not be generalized without jurisdiction-specific tax references.

---

## 5. Key metrics PMs watch

Per country in scope:

- **Real yield levels by tenor:** primary playbook output. US: 5Y, 10Y, 20Y generic/outstanding-sector, 30Y. UK: 1Y through 50Y. France: 2Y through 15Y OATei. Canada: 5Y through 30Y legacy RRB curve.

- **Breakeven inflation rates by tenor:** nominal yield from `01_sovereign_bonds.md` minus real yield from this module at a matched tenor. Generic breakevens are screen-level indicators; exact linker-vs-nominal RV requires matched bond pairs or curve interpolation.

- **5Y5Y forward breakeven:** forward inflation compensation for the 5-year period starting 5 years from now. For the US, FRED publishes T5YIFR using 5Y/10Y nominal and inflation-adjusted Treasury yields. It is a canonical market-based long-run inflation-compensation measure watched by central banks and macro PMs, but not a pure expected-inflation measure.

- **Real yield curve shape:** real 5s30s, 5s10s, and other curve spreads where tenors exist. These separate real-rate curve dynamics from nominal-yield curve dynamics.

- **Breakeven curve shape:** 5Y vs 10Y, 10Y vs 30Y, and 5Y5Y vs spot breakeven. A front-end-led breakeven rise often signals near-term inflation pressure; a long-end-led move may signal inflation-expectation de-anchoring or inflation risk premium.

- **Cross-country breakeven differential:** US vs UK vs French breakevens at matched tenors. These require caution because the inflation indices differ: US CPI-U, UK RPI/CPIH transition, and euro-area HICPxT are not directly equivalent.

- **Real-yield vs breakeven decomposition of nominal moves:** whether a nominal-yield move is real-rate-led, inflation-compensation-led, or both.

- **Period changes and z-scores:** 1d/5d/22d changes and 252d z-score on real yields and breakevens, with tactical alternatives such as 60d/126d and structural alternatives such as 504d.

- **TIPS carry / linker carry:** expected indexation accrual over 1M/3M/6M horizons, driven by known CPI prints, CPI seasonality, base effects, and real-yield carry.

- **Swap-breakeven basis:** canonical sign convention is `swap_minus_breakeven_basis = zero-coupon inflation swap rate - bond-implied breakeven` at the same tenor. Positive values mean swaps trade above linker-implied breakevens; negative values mean swaps trade below. The inverse should be explicitly named `breakeven_minus_swap_basis` if needed. This requires inflation swap data and is a key measure of linker-specific liquidity, supply/demand, and balance-sheet technicals.

- **Deflation-floor value:** primarily relevant for newly issued linkers with index ratio close to 1.0 or in deflation-risk regimes.

---

## 6. Standard workflows

- **Morning inflation check** — real yields, breakevens, 5Y5Y forward breakeven, period changes, and z-scores across countries. → `workflows/morning_briefing.md`

- **Nominal move decomposition** — decompose nominal yield moves into real-yield and breakeven components: real-rate-led tightening, inflation-compensation-led selloff, or mixed. → `workflows/morning_briefing.md` / `workflows/regime_classification.md`

- **Linker vs nominal RV** — breakeven level relative to history; carry analysis; linker liquidity context; trade: long linker / short nominal or the reverse. → `workflows/relative_value.md`

- **Inflation expectations monitoring** — 5Y5Y forward breakeven, survey expectations, inflation swaps, and model decompositions where available. → TBD

- **Cross-country inflation RV** — US breakeven vs UK breakeven vs EUR breakeven at matched tenors, with index-family caveats and FX/currency considerations. → `workflows/relative_value.md`

- **Real-yield curve trade** — steepener/flattener on the real yield curve, e.g. TIPS 5s30s. → TBD

- **Breakeven curve trade** — long front-end breakeven / short back-end breakeven, or vice versa, expressing a view on transitory vs persistent inflation compensation. → TBD

- **CPI carry calendar** — forecast 1-3 months of linker carry from known CPI prints, seasonal factors, and indexation lag. → TBD

- **UK RPI-CPIH basis monitoring** — track the implied pricing of the 2030 methodology transition in UK linkers. → TBD

- **Swap-breakeven basis** — compare zero-coupon inflation swaps to bond-implied breakevens using the canonical sign `ZCIS - breakeven`; isolate linker-specific liquidity/supply-demand effects. → `05_inflation_swaps.md` / TBD workflow

- **Stress / historical replay** — replay 2008 TIPS liquidity crisis, 2020-2022 inflation surge, or 2022 real-rate shock through current real-yield/breakeven positions. → TBD

- **Regime classification** — classify inflation-market regimes using real-yield and breakeven moves: reflationary easing, real tightening, stagflationary repricing, disinflationary growth shock, etc. → `workflows/regime_classification.md`

---

## 7. Models and methodologies

**Real yield level** (1A) — quoted or vendor-derived real yield benchmark for a linker tenor. Deterministic given data source and field. Caveat: generic series are not equivalent to actual bond-level settlement analytics.

**Simple generic breakeven calculation** (1A) — breakeven = nominal generic yield minus real generic yield at matched tenor. Trivial arithmetic but screen-level only. The output should be labelled “generic breakeven / inflation compensation,” not pure expected inflation.

**Exact matched-bond breakeven** (1B) — construct breakeven using specific nominal and linker bonds, maturity matching, interpolation, index ratio, settlement date, coupon, floor treatment, and carry. This is parameterized deterministic and should not be collapsed into the simple 1A screen metric.

**Forward breakeven** (1A for fixed FRED-style generic formula; 1B when convention is parameterized) — apply forward-rate arithmetic to breakeven rates. For example, a 5Y5Y forward breakeven from 5Y and 10Y breakevens can be computed as:

```text
((1 + BE10)^10 / (1 + BE5)^5)^(1/5) - 1
```

where BE10 and BE5 are annualized 10Y and 5Y breakevens expressed in decimal form. The compounding convention must be displayed.

**Z-score and percentile rank on real yields and breakevens** (1A with fixed house default; 1B with custom window/method) — default 252d window is a product convention, not a market law. Tactical workflows may prefer 60d/126d; structural regime work may prefer 504d or longer.

**Real yield curve spread** (1A) — real 5s30s, 5s10s, etc. Simple arithmetic on real-yield tenor points.

**Breakeven curve spread** (1A) — 5Y breakeven vs 10Y breakeven, 10Y vs 30Y, etc. Simple arithmetic on derived breakevens.

**Cross-country breakeven differential** (1A with caveats; 1B if adjusted for index differences) — US 10Y breakeven minus UK or French 10Y breakeven. Generic raw spreads are 1A; index-adjusted or beta-adjusted versions are 1B/2 depending on model.

**TIPS / linker carry with seasonal CPI projection** (1B) — uses known CPI prints and a chosen seasonal model to project index-ratio accrual over a holding horizon. Parameters: horizon, seasonal window, CPI forecast assumption, indexation lag, and bond-level details. Output: carry in bps decomposed into inflation accrual, real yield carry, roll, and financing where available.

**Carry-adjusted breakeven** (1B) — adjusts static breakeven for expected carry over the holding period. Parameters include holding period, CPI seasonal model, financing assumption, and bond/cash matching method.

**Breakeven regression vs inflation swap** (1B/2 boundary) — if used descriptively to compare bond-implied breakeven against zero-coupon inflation swap rate, treat as 1B. If used as a fair-value residual or signal model, treat as Bucket 2.

**Breakeven term-structure fitting** (1B) — Nelson-Siegel, Nelson-Siegel-Svensson, or spline fit on breakeven rates to smooth the curve and identify residuals. Fitting method and constraints are user-visible assumptions.

**Real yield curve fitting** (1B) — NS/NSS/spline on real yields. Same caveats as nominal and OIS curve fitting.

**UK RPI-CPIH basis monitor** (1B) — deterministic framework to estimate the market-implied RPI-CPIH wedge around the 2030 transition boundary, given chosen bonds, curve-fitting method, and interpolation assumptions.

**Inflation risk premium decomposition** (Bucket 2) — decomposes breakevens into expected inflation, inflation risk premium, and liquidity premium using affine term-structure models. Canonical references include D'Amico-Kim-Wei for US TIPS and Hördahl-Tristani for euro-area/US inflation risk premia. Outputs are model-dependent and should never be presented as directly observed components.

**PCA on real yield curve** (Bucket 2) — PCA on real-yield changes across tenors. Returns level/slope/curvature-style factors for the real curve.

**PCA on breakeven curve** (Bucket 2) — PCA on breakeven changes across tenors. Helps distinguish parallel inflation-compensation repricing from breakeven term-structure twist.

**HMM inflation regime classifier** (Bucket 2) — Gaussian HMM or similar regime model using features such as breakeven changes, real-yield changes, CPI surprises, energy prices, and rates vol. Output: state probabilities, transition matrix, and regime labels. Regime labels are analyst-defined after observing state characteristics.

**GARCH on breakeven volatility** (Bucket 2) — conditional volatility model for breakeven changes, useful for vol regimes and risk scaling.

**Cointegration of breakevens and inflation swaps** (Bucket 2) — tests whether bond-implied breakevens and inflation swaps have a stable long-run relation. Output is statistical inference, not deterministic truth.

**Historical replay / stress engine** (1B) — deterministic replay of selected historical real-yield/breakeven paths through a specified position. Bucket 1B because it is parameterized deterministic once the user chooses the window, instruments, position/DV01s, interpolation, carry, and indexation assumptions. A probabilistic inflation-stress simulator would be Bucket 2.

**Out of scope deliberately:** CPI forecasting as a standalone macro forecast product; inflation-swap pricing without inflation-swap data; TIPS portfolio optimization; trade recommendations or position sizing advice.

---

## 8. Data requirements

### 8.1 Data fields referenced

**Time-series fields, per ticker / benchmark / business day:**

- `yield_mid` — daily real yield-to-maturity or benchmark real yield. Primary field for generic linker analytics.
- `yield_bid` / `yield_ask` — if available; needed for liquidity and confidence scoring.
- `clean_real_price` — real clean price, actual bond-level if available.
- `dirty_real_price` — settlement price including accrued interest and indexation, actual bond-level if available.
- `index_ratio` — inflation index ratio applicable on the settlement date. Required for exact carry, settlement, and floor analytics.
- `breakeven` — derived field, not a raw playbook field. Computed as nominal yield minus real yield using the selected matching method.
- `forward_breakeven` — derived from breakeven term structure using selected forward formula and compounding convention.
- `bid_ask_spread` — derived if bid/ask available; key for liquidity confidence.
- `amount_outstanding` — if available over time; needed for liquidity and scarcity context.
- `volume` / `trade_count` — if available; Bloomberg coverage may be partial.

**Reference / static fields, per actual bond where available:**

- `isin_or_cusip`
- `generic_ticker`
- `actual_bond_ticker`
- `security_name`
- `country`
- `currency`
- `curve_family` — USD_TIPS, GBP_LINKER, EUR_FR_OATEI, CAD_RRB.
- `tenor`
- `issue_date`
- `maturity_date`
- `coupon`
- `coupon_frequency`
- `day_count_convention`
- `settlement_convention`
- `base_cpi`
- `base_index_date`
- `inflation_index_family` — US_CPI_U_NSA, UK_RPI, EU_HICP_EX_TOBACCO, CAN_CPI_ALL_ITEMS_NSA.
- `indexation_lag_months`
- `interpolation_method`
- `has_deflation_floor`
- `floor_type`
- `first_live_date`
- `benchmark_roll_date`
- `on_the_run_flag`
- `amount_outstanding`
- `pricing_type` — generic real yield, actual real clean price, actual YTM, etc.
- `real_yield_compounding`
- `nominal_yield_matching_method`
- `vendor_backfill_flag`
- `generic_or_actual_flag`
- `liquidity_score_or_bid_ask`
- `tax_treatment_notes`
- `regime_break_dates`

**Current deflation-floor mapping:**

```yaml
has_deflation_floor:
  US_TIPS: true
  FR_OATEI: true
  UK_LINKER: false
  CAD_RRB: false  # no TIPS-style par floor; verify issue-specific terms before production
```

**Cross-domain data dependencies:**

- Nominal sovereign yields from `01_sovereign_bonds.md` — required to compute breakevens.
- OIS zero curves from `02_ois_swaps.md` — required for OIS-discounted carry, if chosen.
- CPI release data — required for carry projection and index-ratio verification.
  - US: BLS CPI-U NSA.
  - UK: ONS RPI / CPIH.
  - Euro area: Eurostat HICP ex-tobacco.
  - Canada: StatCan CPI.
- Inflation swap rates — required for breakeven-vs-inflation-swap basis.
- FX and cross-currency basis — required for cross-country inflation RV when viewed from a hedged investor perspective.
- Energy prices / CPI component data — optional inputs for CPI nowcast/carry extensions.

### 8.2 Data lineage notes

- **US TIPS:** first issued January 1997. Current active Treasury issuance is 5Y, 10Y, and 30Y. The 20Y sector existed historically but new 20Y issuance was discontinued in 2009. Generic 20Y series may still exist as constant-maturity/outstanding-sector benchmarks; tools must label them accordingly.

- **UK linkers:** first issued March 1981. Legacy bonds issued before the 2005 redesign use an 8-month indexation lag; newer bonds use a 3-month lag. Bloomberg generics may mix underlying issues with different lag conventions depending on benchmark rolls. Exact analytics require bond-level lag metadata.

- **UK RPI-CPIH 2030 break:** long-dated UK linker cash flows embed a methodology transition from RPI to RPI aligned with CPIH from 2030. Post-2030-maturity breakevens are not clean CPIH breakevens; they blend pre- and post-transition cash flows.

- **France OATei:** OATi and OATei are different instruments. OATi references French CPI excluding tobacco; OATei references euro-area HICP excluding tobacco. The current playbook includes OATei only. French domestic inflation vs euro-area inflation basis requires OATi data.

- **Canada RRBs:** no new issuance after November 2022. Outstanding bonds will mature progressively. Liquidity and benchmark quality are expected to decline over time. Do not treat CAD RRB z-scores and scanner signals as equivalent in confidence to US TIPS or UK linkers without a liquidity flag.

### 8.3 Data sufficiency by tool tier

**MVP / generic analytics require:**

- generic real-yield series,
- nominal sovereign generic yields,
- tenor mapping,
- basic benchmark metadata,
- daily history for z-scores and changes.

**Exact carry / settlement / linker RV require:**

- actual bond CUSIP/ISIN,
- clean and dirty real prices,
- coupon and maturity,
- index ratio,
- base CPI,
- accrued interest,
- deflation-floor terms,
- nominal matched-bond or curve construction methodology.

**Inflation RV and basis analytics require:**

- zero-coupon inflation swaps,
- CPI release history,
- inflation swap curve conventions,
- bid/ask/liquidity metrics,
- real-money/auction/issuance data if available.

---

## 9. Tool inventory

Format: **`tool_name`** — takes [inputs], runs [model/computation], returns [outputs], used in [workflows] to surface [macro implication].

### Built

*None yet built specifically for linkers. The sovereign-module tools (`yield_levels`, `curve_spread`, `cross_market_spread`, `scanner`) can be reused for real-yield series if the linker playbook is ingested into the same framework. The tools below are linker-specific builds.*

### Planned — Bucket 1A

**`real_yield_level`** — takes a `(curve_family, tenor, date)`; queries the linker daily real-yield series; returns real yield level, 1d/5d/22d changes in bps, 252d high/low/percentile, and 252d z-score; used in `morning_briefing` to surface where real rates sit relative to recent history.

**`breakeven_inflation_simple`** — takes `(country, tenor, date)`; joins nominal generic yield from sovereign playbook and real generic yield from linker playbook; returns generic breakeven level, period changes, z-score, and matching metadata; used in `morning_briefing` and `relative_value` to surface market inflation compensation. Label output as “generic breakeven / inflation compensation,” not pure expected inflation.

**`forward_breakeven_simple`** — takes two breakeven points, e.g. 5Y and 10Y, and computes the forward breakeven for the intervening period, e.g. 5Y5Y, using a fixed displayed formula; returns level, changes, z-score, and formula metadata; used in `morning_briefing` and inflation-expectations monitoring.

**`breakeven_curve_spread`** — takes a country and two breakeven tenors; computes the differential; returns current spread, period changes, z-score; used to surface the term structure of inflation compensation.

**`real_yield_curve_spread`** — takes a country and two real-yield tenors; computes the spread; returns current value, period changes, z-score; used in real-yield curve-trade workflows.

**`cross_country_breakeven_spread_simple`** — takes two countries at the same tenor; computes raw breakeven differential; returns current spread, period changes, z-score, and index-family caveat; used in `relative_value` to surface cross-country inflation-compensation divergence.

**`scanner_linkers`** — takes a z-score threshold and linker universe; scans real-yield levels and generic breakevens across all `(curve_family × tenor)` combinations; returns ranked extremes with liquidity/confidence flags; used in `morning_briefing` to surface statistical stress.

### Planned — Bucket 1B

**`zscore_custom`** — generic primitive; takes a series, lookback window, and standardization method; returns z-score and percentile. Needed because breakevens and real yields may require different tactical vs structural windows.

**`breakeven_exact_matched_bond`** — takes specific nominal and linker bonds, settlement date, index ratio, price/yield fields, interpolation choices, and matching method; computes exact matched-bond breakeven; returns breakeven, carry metadata, maturity mismatch, and convention details. This is the production-grade version of the simple generic breakeven.

**`tips_carry_seasonal`** — takes a TIPS identifier, holding horizon, known CPI prints, CPI seasonal model choice, and settlement date; projects index-ratio accrual; returns expected carry in bps decomposed into inflation accrual, real yield carry, roll, and optional financing. Parameters: horizon (1M/3M/6M), seasonal model (trailing 5Y, trailing 10Y, BLS seasonal factors, or custom), and CPI forecast override.

**`linker_carry_seasonal`** — generalized version of TIPS carry for UK, French, and Canadian linkers, with index-family-specific calendars and lag conventions.

**`carry_adjusted_breakeven`** — takes country, tenor/bond pair, holding period, CPI seasonal model, and matching method; adjusts static breakeven for expected carry; returns carry-adjusted breakeven and decomposition. Used to answer whether a breakeven is cheap/rich after carry.

**`breakeven_curve_fitter`** — takes breakeven term structure and model choice (NS, NSS, spline); fits the curve; returns fitted values, parameters, and residuals. Used for linker-vs-nominal rich/cheap screens.

**`real_yield_curve_fitter`** — same as above, applied to real yields.

**`beta_adjusted_breakeven_spread`** — takes two breakeven series, regression window, and frequency; runs rolling OLS; returns beta, residual, residual z-score, and hedge ratio. Bucket 1B if used descriptively for hedge ratios; Bucket 2 if residual is framed as fair value or signal.

**`uk_rpi_cpih_basis_monitor`** — takes UK linker curve, bond-level maturity/cash-flow data, and a curve-fitting/interpolation method; estimates the market-implied RPI-CPIH wedge around the 2030 transition; returns wedge estimate, time series, and methodology caveats.

**`swap_breakeven_basis_simple`** — takes country, tenor, bond-implied breakeven, and zero-coupon inflation swap rate; computes `ZCIS - breakeven`, z-score, and time series; surfaces linker-specific liquidity/supply-demand distortion. The inverse, if required, must be explicitly named `breakeven_minus_swap_basis`. Requires inflation swap data from `05_inflation_swaps.md`.

**`historical_replay_linkers`** — takes real-yield/breakeven position spec, historical window, interpolation/carry assumptions, and optional indexation treatment; replays historical paths; returns P&L path, drawdown, vol, worst day, and P&L decomposition into real-yield, breakeven, carry, and inflation-accrual components. Bucket 1B because the replay is deterministic given user-selected assumptions.

### Planned — Bucket 2

**`pca_real_yield_curve`** — takes country, tenor set, and lookback; computes PCA on real-yield changes; returns loadings, factor time series, variance explained, and current factor values; used in attribution and scenario workflows.

**`pca_breakeven_curve`** — takes country, tenor set, and lookback; computes PCA on breakeven changes; returns loadings and factors; surfaces whether breakeven moves are parallel or term-structure twists.

**`hmm_inflation_regime`** — takes feature vector, number of states, and lookback; features may include breakeven changes, real-yield changes, CPI surprises, energy-price changes, rates vol, and policy-rate changes; fits Gaussian HMM; returns state probabilities, transition matrix, current state, and per-state feature profiles.

**`inflation_risk_premium_decomposition`** — takes breakeven term structure and model spec such as DKW or similar affine model; decomposes breakevens into expected inflation, inflation risk premium, and liquidity premium components; returns model-dependent time series and confidence/diagnostic metadata. Output must be labelled estimated/model-dependent.

**`garch_breakeven_vol`** — takes breakeven series and GARCH specification; estimates conditional volatility; returns volatility forecast and model diagnostics.

**`cointegration_breakeven_swap`** — takes bond-implied breakeven and inflation swap series; tests cointegration; returns p-value, equilibrium relationship, and residual. Statistical inference; not deterministic truth.

**`probabilistic_inflation_stress`** — takes fitted regime or stochastic model plus position spec; generates distribution of real-yield/breakeven/inflation paths; returns VaR, ES, stress quantiles, and scenario paths. This is Bucket 2, distinct from deterministic historical replay.

### Aspirational / data-blocked

**`inflation_swap_curve`** — needs zero-coupon and/or year-on-year inflation swap data. Would provide a smoother inflation-compensation curve less affected by linker-specific liquidity, but with its own collateral, seasonality, and market-technical issues.

**`cpi_nowcast_carry`** — combines high-frequency CPI component data such as gasoline, food, shelter proxies, used-car prices, and nowcast inputs to estimate current-month CPI and improve carry projections. Requires CPI component and high-frequency alternative data.

**`deflation_floor_valuation`** — prices embedded deflation floor option using a stochastic inflation model. Most relevant for newly issued TIPS/OATei with index ratios near 1.0.

**`linker_liquidity_score`** — combines bid/ask, amount outstanding, benchmark status, age, auction/reopening schedule, and observed trade data where available into a liquidity/confidence score for scanner output.

---

## 10. Open questions

- *#parameter-default* — Default z-score lookback is 252d. Breakevens are more volatile and mean-reverting than nominal yields; validate whether 126d or 504d is more appropriate for tactical signals.

- *#parameter-default* — TIPS carry seasonal model: trailing 5Y average, trailing 10Y average, BLS seasonal factors, dealer-published carry tables, or PM override? Which is most common on a macro desk?

- *#convention* — Breakeven computation: should MVP use constant-maturity generic yields, Bloomberg generic yields, or matched specific CUSIP/ISIN pairs? Generic is simpler but introduces maturity and liquidity basis.

- *#convention* — For 5Y5Y forward breakeven, should the tool exactly replicate the FRED formula for US outputs, or use a house-consistent forward formula shared with OIS/nominal tools? If both, UI must display which convention is used.

- *#convention* — UK linker breakevens: how do PMs handle the 3-month vs 8-month lag issue when comparing gilts of different vintages? Do they avoid legacy issues, adjust explicitly, or rely on vendor generics?

- *#convention* — UK RPI-CPIH basis: is there a standard desk convention for isolating the post-2030 wedge, or does each inflation desk build its own model?

- *#workflow-validation* — How actively do discretionary macro PMs trade breakeven curve shape as distinct from breakeven level? Is this mostly specialist inflation-desk territory?

- *#workflow-validation* — Is DKW-style inflation risk premium decomposition actively used by PMs, or mainly by central-bank/research teams?

- *#workflow-validation* — How much does CPI seasonality drive actual linker-vs-nominal timing at macro funds? Is it a primary entry/exit consideration or a carry adjustment after macro view formation?

- *#scope* — Inflation swaps are now a separate module (`05_inflation_swaps.md`). Validate whether the MVP basis workflow should use simple generic `ZCIS - breakeven` first, with exact matched-bond basis later.

- *#scope* — Canadian RRBs: given no new issuance and declining liquidity, should CAD RRB outputs be marked as lower-confidence by default in z-score, scanner, and regime tools?

- *#scope* — France has both OATi and OATei. The current playbook includes OATei only. Is OATi-OATei basis important enough for product scope?

- *#data* — CPI releases and historical unrevised/revised CPI vintages are not in the playbook. What is the ingestion path for BLS, ONS, Eurostat, and StatCan CPI?

- *#data* — Index ratios per bond are required for precise carry and settlement. Should we ingest Bloomberg index-ratio fields or compute internally from CPI histories?

- *#data* — Bid/ask and amount outstanding are needed for liquidity confidence. Are these available in the current Bloomberg entitlement/playbook?

- *#methodology* — Breakeven decomposition: how much methodology should be exposed in the UI? Minimal caveat for 1A, full model card for Bucket 2.

- *#methodology* — For exact matched-bond breakevens, what is the house standard: nearest nominal, interpolated nominal curve, asset-swap matched, or synthetic nominal from fitted curve?

- *#methodology* — For UK RPI-CPIH basis, how should pre-2030 and post-2030 cash-flow blending be represented in the UI without confusing users?

---

## 11. References

- *[US Treasury / TreasuryDirect]* — TreasuryDirect. *Treasury Inflation-Protected Securities (TIPS)*; *TIPS/CPI Data*; history of TIPS issuance including 20Y discontinuation.
- *[UK DMO]* — UK Debt Management Office. *Index-linked Gilts*; *How to calculate cash flows on index-linked gilts*; *Index Ratio Data for Index-linked Gilts with a 3-month Indexation Lag*; yield calculation formulae.
- *[Agence France Trésor]* — AFT. *OATi and OAT€i characteristics*; indexation and redemption mechanics.
- *[Bank of Canada]* — Bank of Canada. *Real Return Bonds* technical documentation; index-ratio and maturity-payment mechanics.
- *[FRED T5YIFR]* — Federal Reserve Bank of St. Louis. *5-Year, 5-Year Forward Inflation Expectation Rate* (T5YIFR). Formula: `(((1+(BC_10YEAR-TC_10YEAR)/100)^10 / (1+(BC_5YEAR-TC_5YEAR)/100)^5)^0.2 - 1) × 100`.
- *[FRED T5YIE, T10YIE]* — Federal Reserve Bank of St. Louis. *5-Year Breakeven Inflation Rate* and *10-Year Breakeven Inflation Rate*.
- *[Government of Canada 2022 FES / Debt Management Report]* — Government of Canada. *Fall Economic Statement 2022* and *Debt Management Report 2022-2023*, documenting cessation of new RRB issuance.
- *[C.D. Howe Institute]* — Robson, W., & Laurin, A. (2024). *Cancel the RRB Cancellation*. C.D. Howe Institute e-Brief.
- *[CIBC Asset Management]* — CIBC Asset Management (2023). *The Impact of Ceasing Issuance of Real Return Bonds*.
- *[ACPM]* — Association of Canadian Pension Management (2022). *What You Need to Know About the Recent Real Return Bond Announcement*.
- *[LCP]* — Lane Clark & Peacock (2020). *RPI will be aligned to CPIH from 2030 with no compensation for holders of index-linked gilts*.
- *[Russell Investments]* — Russell Investments (2020). *RPI Consultation — The mist clears*.
- *[Economic History Society]* — Oliver, M. J., & Rutterford, J. (2020). *Index-linked gilts and the end of RPI*. Economic History Review.
- *[BBH]* — Brown Brothers Harriman. *TIPS: More than meets the eye*. Useful return-history framing for TIPS in 2020-2022.
- *[BlackRock / iShares]* — BlackRock. *Mechanics of TIPS and TIPS ETFs*.
- *[NISA]* — NISA Investment Advisors. *TIPS Primer*. Useful practitioner reference for TIPS mechanics, indexation lag, and carry.
- *[PIMCO]* — PIMCO. *Understanding Treasury Inflation-Protected Securities (TIPS)*.
- *[Brookings]* — Brookings Institution. *Understanding Inflation Expectations and Their Importance*.
- *[D'Amico, Kim & Wei 2018]* — D'Amico, S., Kim, D. H., & Wei, M. (2018). “Tips from TIPS: The Informational Content of Treasury Inflation-Protected Security Prices.” *Journal of Financial and Quantitative Analysis*, 53(1), 395-436. Canonical US TIPS inflation-risk-premium and liquidity-premium decomposition model.
- *[Federal Reserve DKW update]* — Federal Reserve notes on TIPS inflation compensation and the DKW model; useful for distinguishing expected inflation, inflation risk premium, and TIPS liquidity premium.
- *[Hördahl & Tristani 2014]* — Hördahl, P., & Tristani, O. (2014). “Inflation Risk Premia in the Euro Area and the United States.” *International Journal of Central Banking*, 10(3), 1-47.
- *[Nelson & Siegel 1987]* — Nelson, C. R., & Siegel, A. F. (1987). “Parsimonious Modeling of Yield Curves.” *Journal of Business*, 60(4), 473-489.
- *[Svensson 1994]* — Svensson, L. E. O. (1994). “Estimating and Interpreting Forward Interest Rates: Sweden 1992-1994.” NBER Working Paper No. 4871.
- *[Tuckman, Ch. 22]* — Tuckman, B., & Serrat, A. (2022). *Fixed Income Securities: Tools for Today's Markets* (4th ed.). Wiley. Chapter on inflation-indexed bonds.
- *[Hamilton 1989]* — Hamilton, J. D. (1989). “A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle.” *Econometrica*.
- *[Hamilton 1994]* — Hamilton, J. D. (1994). *Time Series Analysis*. Princeton University Press.
- *[Bollerslev 1986]* — Bollerslev, T. (1986). “Generalized Autoregressive Conditional Heteroskedasticity.” *Journal of Econometrics*, 31(3), 307-327.
