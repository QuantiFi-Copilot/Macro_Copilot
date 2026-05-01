# Policy Futures / STIR Futures

**Status:** v2 internal draft — built from `/playbooks/policy_futures.yml`, with serial/quarterly contract caveats, convention caveats, data-lineage requirements, and bucket-classification discipline. To be validated against live Bloomberg metadata, exchange contract specs, dealer/advisory feedback, and Brevan workflow observations.

**Scope:** 24 rolling short-rate futures benchmark series across 3 curve families currently in `/playbooks/policy_futures.yml`: SOFR futures (`SOFR_FUT`), Euribor futures (`EUR_SHORT_RATE_FUT`), and SONIA futures (`SONIA_FUT`). Each curve contains the first 8 rolling Bloomberg generic contracts: `SFR1`-`SFR8`, `ER1`-`ER8`, and `SFI1`-`SFI8` `Comdty`. The playbook stores `PX_LAST` as `last_price`, plus `OPEN_INT` and `PX_VOLUME`, and reference metadata including `LAST_TRADEABLE_DT`, `FUT_CONT_SIZE`, `FUT_TICK_SIZE`, `FUT_TICK_VAL`, and `UNDL_SPOT_TICKER`.

**Important naming caveat:** this module uses the product name **policy futures** because the internal playbook is named `policy_futures`, but the market-standard instrument family is **STIR futures**: exchange-traded short-term interest-rate futures. These instruments are not pure central-bank-policy futures. SOFR and SONIA futures reference compounded overnight RFRs; Euribor futures reference a 3-month unsecured term benchmark. Translating them into central-bank meeting probabilities is a Bucket 1B workflow with assumptions, not a Bucket 1A identity.

**Cross-references:** OIS forwards, cross-currency OIS spreads, and OIS curve analytics live in `02_ois_swaps.md`. This module owns exchange-traded futures strip analytics, futures-implied rates, volume/open-interest screens, futures-vs-OIS basis, and futures-derived policy-path workflows. Central-bank meeting calendars, policy-rate targets, and event calendars are shared static/reference data used by both this module and `02_ois_swaps.md`. Bond futures and CTD/basis analytics are out of scope and should live in a separate bond-futures module when written.

---

## 1. What it is

A policy future / STIR future is an exchange-traded, cash-settled futures contract whose settlement value is linked to a short-term interest-rate benchmark over a future accrual or fixing period. The price is typically quoted as an index:

```text
futures price = 100 - implied reference rate
implied reference rate = 100 - futures price
```

Because of this inverse pricing, a **higher futures price means a lower implied rate** and is usually interpreted as dovish / rallying front-end pricing. A **lower futures price means a higher implied rate** and is usually interpreted as hawkish / selling off front-end pricing.

In scope for this module:

- **United States — Three-Month SOFR futures.** Internal curve family: `SOFR_FUT`. Bloomberg rolling generics: `SFR1`-`SFR8 Comdty`. Exchange product mapping to verify against Bloomberg metadata: CME Three-Month SOFR futures, commonly associated with exchange product code `SR3`. CME defines the contract price as `100 - R`, where `R` is the business-day compounded SOFR per annum during the contract reference quarter. The reference quarter runs from the third Wednesday of the third month preceding delivery month to the third Wednesday of the delivery month. [CME Three-Month SOFR Futures]

- **Euro area — Three-Month Euribor futures.** Internal curve family: `EUR_SHORT_RATE_FUT`, but this label should be treated as a raw-playbook shorthand. The instrument is **Euribor futures**, not an €STR/RFR future. Bloomberg rolling generics: `ER1`-`ER8 Comdty`. ICE defines the contract as a Three Month Euro (Euribor) future, quoted as `100.00 minus the numerical value of the rate of interest`; final settlement is based on the EMMI 3-month Euribor fixing on the last trading day. [ICE Three Month Euribor Futures; EMMI Euribor]

- **United Kingdom — Three-Month SONIA futures.** Internal curve family: `SONIA_FUT`. Bloomberg rolling generics: `SFI1`-`SFI8 Comdty`. Exchange product mapping to verify against Bloomberg metadata: ICE Three Month SONIA Index futures, exchange contract symbol `SO3`. ICE defines the contract as cash-settled and quoted as `100.00 minus the numerical value of the rate of interest`; the EDSP rate represents the effective rate achieved by reinvesting at SONIA for each day of the accrual period. [ICE Three Month SONIA Index Futures; Bank of England SONIA]

These are **rolling generic futures series**, not individual contract-month histories. `SFR1` is “front Three-Month SOFR future,” not one permanent maturity. When the front contract expires, the generic rolls to the next listed contract. This matters for z-scores, historical backtests, volume/open-interest analysis, and event attribution. It also matters for pack/bundle language: SOFR and Euribor listed universes include serial as well as quarterly delivery months, so “whites/reds” labels should only be applied after confirming whether a Bloomberg generic sequence is quarterly-only or includes serial months.

---

## 2. Why it exists / role in the system

Policy futures serve five economic functions for a macro rates product.

**Exchange-traded policy-path expression.** STIR futures are the most liquid standardized exchange-traded instruments for expressing views on the front end of rates curves. A PM can buy or sell front contracts, calendar spreads, packs, bundles, and butterflies to express views on central-bank timing, terminal-rate repricing, or front-end curve shape. The exchange-traded structure gives transparent prices, margining, volume, and open interest.

**Central-bank-pricing workflow input.** Futures strips are one of the raw materials for answering: “How many hikes or cuts are priced?” The answer is not direct from the futures price alone. SOFR futures price compounded SOFR over IMM-style reference quarters; SONIA futures price compounded SONIA over accrual periods; Euribor futures price 3-month unsecured Euribor. A meeting-pricing tool must map those contract periods to central-bank meeting dates, policy target rates, reference-rate basis, known fixings, stubs, turns, and holiday calendars.

**Liquidity and price discovery.** In USD, Three-Month SOFR futures are a primary liquidity pool for hedging short-term dollar rates. CME describes them as providing price discovery along the forward curve, with quarterly contracts reflecting SOFR expectations between IMM dates and listings extending out to 10 years. In Europe and the UK, Euribor and SONIA futures serve analogous front-end price-discovery roles, though the benchmark mechanics are different.

**Positioning / flow proxy.** Unlike OTC OIS curves, futures have observable volume and open interest. This makes them useful for positioning proxies, roll diagnostics, contract concentration, and event-driven flow analysis. Volume and open interest do not reveal whether the marginal flow is speculative, hedging, or market-making, but they provide a systematic starting point for detecting crowded periods and liquidity migration across the strip.

**Bridge between OIS, money markets, and macro events.** Futures sit between the OIS module and the central-bank workflow module. For example, a Fed-pricing workflow may compare SOFR futures, Fed Funds futures, SOFR OIS, and meeting-dated OIS. An ECB workflow may compare Euribor futures, €STR OIS, Euribor-OIS basis, and ECB meeting dates. A BoE workflow may compare SONIA futures, SONIA OIS, and Bank Rate expectations. The futures layer is where exchange-traded pricing, volume, and open interest enter the morning briefing.

---

## 3. What its movements signal (macro context)

### The futures-implied-rate decomposition

A STIR futures-implied rate is best described as **market-implied short-rate compensation for a contract period**, not a pure forecast of a central-bank policy rate. A useful first-pass decomposition is:

```text
futures-implied reference rate
≈ expected average benchmark fixing over the contract period
+ risk / term / convexity premium
+ benchmark basis to the central-bank policy target
+ liquidity / margin / technical effects
+ known-fixing, calendar, turn, and settlement effects
```

For SOFR and SONIA futures, the benchmark fixing is an overnight RFR compounded over the relevant accrual/reference period. For Euribor futures, the benchmark fixing is 3-month Euribor, an unsecured term bank-funding benchmark. This distinction is central: **Euribor futures are not a clean €STR/ECB deposit-rate future.** They embed the Euribor-€STR / unsecured-term-funding basis.

### Price direction

Because the contracts are inverse-priced:

```text
price up   → implied rate down → easier policy / lower short-rate pricing
price down → implied rate up   → tighter policy / higher short-rate pricing
```

Tooling should always show both the price move and the rate-equivalent move. A `+10bp` futures price move is a `-10bp` implied-rate move. If the UI reports only “futures up 10bp,” a PM will understand it, but a product trace should explicitly show the sign conversion.

### Front contracts vs back contracts

The first few contracts are dominated by near-term central-bank meetings, known fixings, CPI/labour-market surprises, and event risk. Contracts farther out the strip are more about terminal-rate expectations, neutral-rate narratives, risk premia, and medium-term macro views. The first eight rolling contracts in the playbook roughly capture the liquid front and intermediate policy-cycle strip, but they do not cover the full listed SOFR curve.

### Curve shape and packs

The shape of the futures strip is itself a policy-cycle signal.

- A downward-sloping implied-rate strip can indicate expected cuts / easing.
- An upward-sloping implied-rate strip can indicate expected hikes / tightening.
- A hump-shaped strip can indicate a terminal-rate peak followed by later easing.
- A front-end rally concentrated in the first two contracts can mean the market repriced the next meeting or immediate event risk.
- A rally in red contracts / farther-out contracts can mean the market repriced the cycle’s terminal or neutral rate.

In STIR futures vernacular, the first four **quarterly IMM** contracts are often discussed as the “whites,” the next four quarterly IMM contracts as the “reds,” followed by greens/blues/golds farther out. This terminology should not be applied blindly to raw rolling generic tickers. SOFR and Euribor listed universes include serial months as well as quarterlies, so the product must first identify actual contract months and whether each generic maps to a serial or quarterly contract. If the playbook’s first eight generics are quarterly IMM generics, they can support a whites/reds-style dashboard; if they include serial months, whites/reds packs must be built from actual quarterly contract metadata rather than strip position alone.

### Cross-market spreads

Cross-market futures spreads can be useful, but must be interpreted by benchmark:

```text
SOFR futures spread vs SONIA futures → USD secured RFR expectations vs GBP unsecured overnight RFR expectations
SOFR futures spread vs Euribor futures → USD secured RFR expectations vs EUR unsecured 3M bank funding expectations
SONIA futures spread vs Euribor futures → GBP RFR expectations vs EUR unsecured 3M bank funding expectations
```

The SOFR-Euribor futures spread is therefore **not** the same object as SOFR-€STR OIS. The latter is closer to cross-central-bank RFR divergence. The former includes Euribor bank-credit/term-funding basis. This is a feature, not a bug, but the product must label it correctly.

### Volume and open interest

Open interest and volume are not directional alpha by themselves. They answer different questions:

- **Volume**: where trading activity is occurring today / this week.
- **Open interest**: where risk remains outstanding after trades are netted through clearing.
- **Volume z-score**: whether an event produced unusual activity.
- **Open-interest change**: whether risk is being added or reduced in a contract area.
- **Roll concentration**: whether liquidity has migrated from the expiring contract to the next contract.

These metrics are most useful when combined with price/rate moves. A front contract rally on very high volume and rising open interest has different market meaning from a similar rally on thin volume and falling open interest.

### Specific historical episodes worth knowing

**LIBOR transition and the rise of SOFR/SONIA futures.** SOFR futures launched in 2018 as part of the USD transition away from LIBOR, and Three-Month SOFR futures became the central exchange-traded USD short-rate futures product after Eurodollar futures were phased out. SONIA futures serve the equivalent role in sterling after the move away from LIBOR. This matters because long historical windows in `SFR` and `SFI` generics are not comparable to old Eurodollar or short-sterling histories unless the product explicitly models the transition.

**2022 global hiking cycle.** The 2022 inflation shock and rapid central-bank tightening cycle turned STIR futures into the cleanest live dashboard for policy-path repricing. Front contracts sold off as markets priced larger and faster hikes; later contracts moved as the market repriced terminal rates and recession/easing risk. This is the canonical stress case for a policy-path-repricing workflow.

**March 2023 banking stress.** Front-end futures rallied violently as banking-system stress caused markets to price out hikes and price in more aggressive future easing. This is the canonical example of how STIR futures combine central-bank reaction-function pricing with financial-stability risk.

**Euribor vs €STR distinction.** Euribor remained a major reformed benchmark using EMMI’s hybrid methodology, while €STR became the euro RFR used in OIS discounting and RFR swaps. Euribor futures remain deeply important, but they are not pure €STR futures. Any ECB-pricing workflow that uses `ER1`-`ER8` must explicitly control for Euribor-€STR basis or at least label the output as Euribor-implied, not €STR-implied.

**UK LDI / gilt crisis spillover.** The September-October 2022 UK gilt crisis primarily lived in gilts, linker markets, and pension-fund hedging dynamics, but front-end SONIA pricing also reflected expectations for BoE reaction, liquidity support, and policy credibility. This is a useful reminder that futures-derived policy pricing is never isolated from broader market plumbing.

---

## 4. Market microstructure (just enough)

**Quoting convention.** All three futures families in the playbook are inverse-priced: `price = 100 - rate`. The canonical analytics field should therefore be `implied_rate = 100 - last_price`. For clarity, the UI should display both price and rate-equivalent change:

```text
Δimplied_rate_bps = -Δprice_bps
```

**Three-Month SOFR futures.** CME specifies the contract unit as `$2,500 × contract-grade IMM Index`, with price quotation equal to `100 - R`, where `R` is the business-day compounded SOFR per annum during the contract reference quarter. The reference quarter runs from the third Wednesday of the third month preceding delivery month to the third Wednesday of delivery month. Product code mapping should be verified against Bloomberg, because the playbook uses Bloomberg rolling generics `SFR1`-`SFR8`, while exchange product code is commonly `SR3`. [CME Three-Month SOFR Futures]

**Three-Month Euribor futures.** ICE specifies the product as Three Month Euro (Euribor) futures, with quotation `100.00 minus the numerical value of the rate of interest`. The final settlement price is based on the EMMI 3-month Euribor fixing on the last trading day, rounded per ICE methodology. ICE specifies a minimum price fluctuation of `0.005`, equivalent to `€12.50` per contract. [ICE Three Month Euribor Futures]

**Three-Month SONIA futures.** ICE specifies Three Month SONIA Index futures as cash-settled, with unit of trading `£2,500 × Rate Index`, quotation `100.00 minus the numerical value of rate of interest`, and EDSP based on the effective rate achieved by reinvesting at SONIA for each day of the accrual period. ICE lists minimum price fluctuations of `0.0025` (`£6.25`) for the front delivery month during the specified near-expiry period and `0.005` (`£12.50`) otherwise. [ICE Three Month SONIA Index Futures]

**Rolling generics vs actual contracts.** The playbook stores rolling Bloomberg generics: `SFR1`, `ER1`, `SFI1`, etc. These are useful for dashboards, but they are not ideal for historical backtests unless roll mechanics are explicit. A front generic series mixes different contract months over time. Production analytics should store both:

```text
rolling_generic_ticker = SFR1 Comdty
actual_contract_ticker = specific contract month/year
roll_date              = Bloomberg generic roll date
contract_accrual_start = actual contract period start
contract_accrual_end   = actual contract period end
```

**Known fixings and accrual periods.** For contracts whose accrual/reference period has already begun, part of the final settlement rate may be known through realized fixings. The remaining unknown period is what the market is pricing. This matters heavily for front contracts near expiry. A policy-pricing tool that ignores known fixings will overstate the amount of forward-looking policy information in the front contract.

**Futures vs OIS.** Futures and OIS are related but not interchangeable. Futures are exchange-traded and daily-margined; OIS is OTC/cleared swap exposure with different collateral and discounting mechanics. Futures may require convexity/margining adjustments when compared with OIS forwards. For many morning-dashboard purposes, the raw futures-implied rate is sufficient. For precise curve construction or futures-OIS basis, the adjustment is a Bucket 1B assumption.

**Euribor benchmark mechanics.** Euribor is calculated by EMMI using a hybrid methodology designed to remain robust and representative under the EU Benchmark Regulation. It is not an overnight RFR. Its underlying market is unsecured euro wholesale funding, so Euribor futures include bank-credit/term-funding components that do not belong in €STR OIS. [EMMI Euribor]

**SONIA benchmark mechanics.** SONIA is administered by the Bank of England and based on actual overnight sterling transactions; the Bank of England describes it as the risk-free rate for sterling markets. SONIA futures therefore map more closely to the sterling RFR curve than old short-sterling/LIBOR futures did. [Bank of England SONIA]

---

## 5. Key metrics PMs watch

Per curve family in scope:

- **Last price by rolling contract:** `SFR1`-`SFR8`, `ER1`-`ER8`, `SFI1`-`SFI8`.
- **Implied rate by rolling contract:** `100 - last_price`, displayed in percent and bps.
- **Period changes:** 1d, 5d, 22d, 63d, YTD in both price terms and rate-equivalent terms.
- **Strip shape:** first-vs-eighth contract spread, whites/reds averages, front-vs-back strip slope.
- **Calendar spreads:** contract-to-contract rate differentials such as `SFR2 - SFR1`, `SFR4 - SFR1`, `ER4 - ER1`, `SFI4 - SFI1`.
- **Butterflies:** e.g., `2 × SFR2 - SFR1 - SFR3` in implied-rate space, with explicit sign convention.
- **Packs and bundles:** average implied rates across consecutive contracts. Whites/reds are useful dashboard examples only after isolating the relevant quarterly IMM contracts; raw strip positions may include serial months depending on Bloomberg generic mapping.
- **Terminal contract proxy:** the contract with the highest implied rate in a hiking cycle or lowest implied rate in an easing cycle, subject to curve shape and contract coverage.
- **Cross-market futures spreads:** SOFR vs SONIA, SOFR vs Euribor, SONIA vs Euribor at matched strip positions.
- **Futures vs OIS basis:** futures-implied rate minus corresponding OIS-derived forward rate for matched period, after convention/calendar adjustment.
- **Volume:** daily contract volume and volume z-score.
- **Open interest:** level, change, z-score, and concentration across the strip.
- **Roll metrics:** front-to-next open-interest migration and volume migration near expiry.
- **Meeting-proximity metrics:** number of central-bank meetings inside each contract’s reference/accrual period.
- **Event repricing:** change in implied rates and strip shape since CPI, payrolls, FOMC/ECB/BoE decision, minutes, or major speech.

---

## 6. Standard workflows

The workflows below are the recurring units of analysis on a macro rates desk that involve policy futures / STIR data.

- **Morning futures strip check** — pull price, implied rate, period changes, volume, and open interest across SOFR, Euribor, and SONIA strips. → `workflows/morning_briefing.md`
- **Central-bank pricing read** — translate front-end futures and OIS into expected hikes/cuts by meeting, with assumptions visible. → `workflows/central_bank_pricing.md`
- **Policy repricing since event** — show how the futures strip changed since CPI, payrolls, FOMC/ECB/BoE, or a financial-stability shock. → `workflows/central_bank_pricing.md`
- **Futures-vs-OIS reconciliation** — compare exchange-traded futures-implied rates with OIS forwards for matched periods; surface basis and convention differences. → `workflows/relative_value.md`
- **Cross-CB divergence** — compare SOFR, Euribor, and SONIA futures strips by matched contract position or matched accrual period, with benchmark-basis caveats. → `workflows/relative_value.md`
- **Packs/bundles monitor** — summarize whites/reds and contract clusters to show whether repricing is concentrated in the near meetings or farther out the cycle. → TBD
- **Positioning / flow monitor** — scan volume and open-interest changes for unusual activity across contracts. → TBD
- **Curve trades on the futures strip** — calendar spreads, butterflies, pack spreads, and strip slope trades. → TBD
- **Historical replay / stress** — replay 2022 hiking cycle, March 2023 banking stress, or UK 2022 stress through a futures-strip position. → TBD
- **Regime classification** — classify front-end pricing regimes: aggressive hiking, terminal plateau, easing cycle, financial-stability cut pricing, or uncertainty/volatility spike. → `workflows/regime_classification.md`

---

## 7. Models and methodologies

**Price-to-implied-rate conversion** (1A) — fixed arithmetic: `implied_rate = 100 - last_price` for inverse-priced STIR futures. The tool must also invert signs for rate-equivalent changes.

**Futures level / period change / z-score** (1A with fixed defaults; 1B with custom lookback) — same structure as sovereign/OIS level tools, but applied to futures prices and implied rates. Default z-score window can be 252d, but front-end futures often require 60d/126d tactical windows.

**Calendar spread / strip slope** (1A) — arithmetic difference between implied rates on two contracts, with explicit choice of matched strip positions or matched contract periods.

**Futures butterfly** (1A with fixed weights; 1B with custom/DV01 weighting) — curvature measure on the futures-implied-rate strip. Default 50-50 weights are acceptable for simple screens; risk-weighted versions require contract DV01/tick value and are Bucket 1B.

**Packs and bundles** (1A/1B boundary) — simple average of implied rates across fixed contract groups is 1A; DV01/tick-value-weighted packs or custom groups are 1B.

**Volume / open-interest scanner** (1A with fixed z-score; 1B with custom normalization) — ranks contracts by unusual trading activity or open-interest change. Must distinguish level, change, and roll-period effects.

**Event repricing** (1A for simple before/after; 1B for event-window methodology) — computes change in futures-implied rates from a specified event timestamp/date. Daily playbook data supports close-to-close event repricing; intraday event analysis requires intraday futures data.

**Policy-path extraction from futures** (1B) — translates futures-implied rates into expected policy path by combining contract accrual periods, meeting calendars, known fixings, reference-rate basis, and assumptions about policy-rate steps. This is not one correct answer because basis and meeting allocation assumptions matter.

**Per-meeting pricing from futures** (1B) — given a central-bank meeting calendar and futures strip, estimate expected hike/cut amounts or outcome probabilities. For the Fed, CME FedWatch methodology uses 30-Day Fed Funds futures and explicit assumptions about EFFR target-rate behavior; using SOFR futures requires an additional SOFR-EFFR basis assumption. For ECB, Euribor futures require Euribor-€STR / Euribor-deposit-rate basis handling. For BoE, SONIA futures require Bank Rate-SONIA basis handling.

**Futures-vs-OIS basis** (1B) — compares futures-implied rates with OIS-derived forwards over matched periods. Requires calendar matching, known-fixing treatment, compounding convention, interpolation, and possible convexity/margining adjustment.

**Convexity / margining adjustment** (1B) — adjusts futures-implied rates for the difference between daily-margined futures and OTC/cleared swap/OIS exposure. Materiality depends on horizon, volatility, and product.

**Roll-adjusted generic history** (1B) — constructs continuous futures histories from actual contract months using an explicit roll rule. Bloomberg rolling generics are useful but should not be treated as a backtest-grade continuous series without a roll methodology.

**Positioning signal from volume/open interest** (1B/2 boundary) — descriptive z-scores and changes are 1B; predictive crowding/positioning models are Bucket 2.

**Historical replay** (1B) — deterministic stress engine that applies historical futures-strip moves to a position vector. Bucket 1B because the user chooses the window, positions, roll rule, and interpolation.

**PCA on futures strip** (2) — principal components of futures-implied-rate changes across the strip, typically level/slope/curvature or front-cycle/terminal/repricing factors.

**HMM policy-regime classifier** (2) — Gaussian HMM or related regime model using futures strip level/slope, OIS forwards, realized volatility, and policy-event features.

**Central-bank reaction-function model** (2) — maps macro surprises and inflation/labour-market data to implied policy-path changes. This is model-dependent and should be transparent about features, sample, and refit schedule.

**Probabilistic terminal-rate distribution** (2) — estimates a distribution of terminal-rate outcomes from futures, options, OIS, and/or model-implied dynamics. This is not a deterministic futures-strip read.

**GARCH / stochastic-vol model on front-end futures changes** (2) — conditional volatility model for futures-implied-rate changes, useful for risk sizing and volatility-regime classification.

**Out of scope deliberately:** trade recommendations, automated position sizing, execution algorithms, and futures-options/swaption pricing. Futures options and mid-curve options belong in the rates-vol module.

---

## 8. Data requirements

### 8.1 Current playbook fields

The current playbook provides the following daily fields per rolling generic ticker:

**Time-series fields:**

- `last_price` — Bloomberg `PX_LAST`.
- `open_interest` — Bloomberg `OPEN_INT`.
- `volume` — Bloomberg `PX_VOLUME`.

**Reference/static fields:**

- `expiry_date` — Bloomberg `LAST_TRADEABLE_DT`.
- `security_name` — Bloomberg `SECURITY_DES`.
- `contract_size` — Bloomberg `FUT_CONT_SIZE`.
- `tick_size` — Bloomberg `FUT_TICK_SIZE`.
- `tick_value` — Bloomberg `FUT_TICK_VAL`.
- `underlying_spot` — Bloomberg `UNDL_SPOT_TICKER`.

**Playbook metadata:**

- `instrument_type = policy_future`.
- `curve_family` — `SOFR_FUT`, `EUR_SHORT_RATE_FUT`, `SONIA_FUT`.
- `country`, `currency`, `contract_code`, `strip_position`.
- `inverse_pricing = true` for all current instruments.
- `is_rolling_contract = true` for all current instruments.

### 8.2 Required canonical analytics fields

The canonical analytics layer should derive or store:

```yaml
canonical_fields:
  - implied_rate: 100 - last_price
  - implied_rate_change_1d_bps
  - price_change_1d_bps
  - actual_contract_ticker
  - exchange_product_code
  - contract_month
  - contract_year
  - rolling_generic_ticker
  - strip_position
  - roll_date
  - last_tradeable_date
  - final_settlement_date
  - accrual_start_date
  - accrual_end_date
  - reference_rate_name
  - reference_rate_family
  - benchmark_type: RFR_compounded | unsecured_term
  - day_count
  - compounding_method
  - final_settlement_method
  - tick_size_schedule
  - tick_value
  - contract_unit
  - clearing_house
  - exchange
  - holiday_calendar
  - known_fixing_fraction
  - unknown_forward_fraction
  - meetings_inside_contract_period
  - contract_month_type: serial | quarterly
```

### 8.3 Cross-domain data dependencies

- **Central-bank meeting calendars** — Fed/FOMC, ECB Governing Council, BoE MPC. Critical for `per_meeting_pricing_from_futures`.
- **Policy target rates** — Fed Funds target range / EFFR, ECB deposit facility rate, BoE Bank Rate.
- **Daily fixings** — SOFR, SONIA, Euribor. Needed to separate known fixings from forward-looking pricing in live contracts.
- **OIS curves** — from `02_ois_swaps.md`, needed for futures-vs-OIS basis and cross-validation.
- **Fed Funds futures / EFFR data** — not currently in this playbook, but required for FedWatch-style Fed probabilities without a SOFR-EFFR basis assumption.
- **€STR / Euribor basis** — required to translate Euribor futures into cleaner ECB RFR-policy pricing.
- **Event calendar** — CPI, payrolls, central-bank meetings, minutes, speeches, fiscal announcements, and financial-stability events.
- **Intraday futures data** — required for true event-window analysis; daily close data only supports close-to-close repricing.

### 8.4 Data-lineage notes

- **Rolling generics are not actual contracts.** Production tools should store actual contract-month histories where possible and use rolling generics for dashboard views.
- **SOFR futures live history does not go back to 2005.** The playbook extraction start date is 2005, but Three-Month SOFR futures launched in 2018. Pre-live-date gaps or vendor proxies must be flagged.
- **SONIA futures history has transition/regime issues.** SONIA was reformed in 2018 and became the sterling RFR replacement for LIBOR. Any pre-reform or vendor-stitched history should be marked.
- **Euribor futures are older but benchmark methodology changed.** Euribor’s hybrid methodology and benchmark reforms mean long historical windows should be treated with benchmark-regime awareness.
- **`EUR_SHORT_RATE_FUT` is not a clean name.** The current playbook label should be interpreted as Euribor futures. Consider renaming the canonical curve family to `EURIBOR_FUT`, with `EUR_SHORT_RATE_FUT` retained as a legacy alias.
- **Serial-vs-quarterly identification is required.** SOFR and Euribor listed universes include serial months as well as quarterly contracts. Generic tickers must be mapped to actual contract months before applying whites/reds labels, pack/bundle analytics, or quarterly IMM curve logic.
- **Volume/open interest roll effects are large.** Volume and OI z-scores around expiry can reflect mechanical roll rather than new macro information. Tools must either flag roll windows or normalize by roll calendar.

---

## 9. Tool inventory

Format: **`tool_name`** — takes [inputs], runs [model/computation], returns [outputs], used in [workflows] to surface [macro implication].

### Built

*None yet built specifically for policy futures / STIR futures. The playbook provides ingestible daily data; the tools below are the proposed futures-domain builds.*

### Planned (Bucket 1A)

**`futures_price_level`** — takes a `curve_family`, `strip_position`, and date; returns last price, 1d/5d/22d changes, 252d percentile, z-score, volume, and open interest. Used in `morning_briefing`.

**`futures_implied_rate`** — takes a futures price and `inverse_pricing` flag; computes `implied_rate = 100 - price`; returns rate level and rate-equivalent changes. Used everywhere futures are displayed.

**`futures_strip_snapshot`** — takes a curve family and date; returns first 8 contracts with price, implied rate, volume, open interest, expiry date, and strip position. Used in morning strip dashboards.

**`futures_calendar_spread`** — takes a curve family and two strip positions; computes implied-rate spread and period changes. Used to surface front-end curve slope.

**`futures_butterfly_simple`** — takes three strip positions with default 50-50 weights; computes strip curvature in implied-rate space. Used in curve-trade screens.

**`futures_pack_average_simple`** — takes a fixed contract group, e.g. first 4 or next 4 contracts; computes simple average implied rate. Used for whites/reds dashboard views.

**`futures_cross_market_spread`** — takes two curve families and matched strip positions; computes cross-market implied-rate differential with clear benchmark labels. Used in cross-CB divergence screens.

**`volume_open_interest_snapshot`** — returns daily volume, open interest, and 1d changes by contract. Used in positioning/flow monitor.

**`futures_scanner`** — scans price moves, implied-rate moves, volume z-scores, and OI z-scores across all 24 contracts; returns ranked extremes. Used in morning briefing.

### Planned (Bucket 1B)

**`zscore_custom`** — generic primitive allowing lookback, standardization method, and roll-window exclusion.

**`roll_adjusted_futures_history`** — takes an actual-contract history and roll rule; constructs a continuous series. Used for backtests and stable z-score histories.

**`futures_strip_with_contract_metadata`** — joins rolling generics to actual contract-month metadata, accrual periods, last trading dates, final settlement dates, and tick schedules. Foundational for meeting-pricing and futures-OIS basis.

**`policy_path_from_futures`** — takes a futures strip, meeting calendar, policy target series, benchmark-basis assumptions, and known-fixing data; returns implied average path and terminal proxy. Used in central-bank pricing workflows.

**`per_meeting_pricing_from_futures`** — decomposes futures-implied rates into expected per-meeting hike/cut amounts or probabilities. Must expose assumptions: policy step size, reference-rate basis, known fixings, contract-period mapping, and whether OIS or futures are the preferred source.

**`event_repricing_futures`** — takes an event date/time and curve family; computes pre/post changes by contract, pack, and strip slope. Daily version is close-to-close; intraday version requires intraday futures data.

**`futures_ois_basis`** — compares futures-implied rates with OIS-derived forward rates over matched contract periods. Returns basis level, period changes, z-score, and methodology metadata.

**`convexity_adjusted_futures_forward`** — applies a selected convexity/margining adjustment to futures-implied forwards. Used when comparing futures to OTC OIS or constructing a precise forward curve.

**`pack_bundle_analytics`** — creates custom packs/bundles with simple, DV01/tick-value, or user-specified weights. Returns average implied rate, DV01/tick-value exposure, and P&L per bp. Must use actual contract-month metadata to distinguish serial from quarterly contracts before applying whites/reds/greens labels.

**`positioning_zscore`** — computes volume/open-interest z-scores with roll-window handling and contract-normalization. Used to distinguish true unusual activity from expiry mechanics.

**`historical_replay_policy_futures`** — takes a futures position vector and historical window; replays strip moves through the position. Deterministic given window, roll rule, and positions.

### Planned (Bucket 2)

**`pca_policy_futures_strip`** — computes PCA on implied-rate changes across the futures strip. Returns factor loadings, variance explained, and current factor shocks.

**`hmm_policy_pricing_regime`** — fits a regime model on strip levels/slopes, OIS forwards, realized volatility, event features, and cross-market spreads. Returns state probabilities and transition matrix.

**`central_bank_reaction_function_model`** — estimates how futures-implied policy paths respond to macro surprises, inflation data, labour-market data, and central-bank communication. Model-dependent and later-stage.

**`terminal_rate_distribution`** — combines futures, OIS, and optionally options to estimate a distribution of terminal-rate outcomes. Output is probabilistic, not a deterministic futures read.

**`positioning_crowding_model`** — uses volume, open interest, price action, and roll behavior to infer crowding/stress risk. This is fragile and should be treated as exploratory Bucket 2.

**`garch_front_end_futures_vol`** — estimates conditional volatility for front-end futures-implied-rate changes.

**`multi_market_policy_pca`** — PCA across SOFR, Euribor, and SONIA futures panels to identify global policy-cycle factors and idiosyncratic central-bank residuals.

### Aspirational (data-blocked or further out)

**`fedwatch_style_probabilities`** — true FedWatch-style probabilities require 30-Day Fed Funds futures, not just SOFR futures. This should be a separate extension or joint tool with Fed Funds futures ingestion.

**`intraday_event_repricing`** — requires intraday futures data around CPI, payrolls, and central-bank events.

**`futures_options_terminal_distribution`** — requires SOFR/SONIA/Euribor futures options or mid-curve options data.

**`dealer_positioning_inference`** — would require richer positioning data, CFTC-style reports, client-flow data, or exchange participant categories not in the current playbook.

---

## 10. Open questions

- *#playbook-naming* — Rename canonical `EUR_SHORT_RATE_FUT` to `EURIBOR_FUT`? Current name risks implying €STR/RFR exposure, but the tickers are Euribor futures.
- *#metadata* — Confirm Bloomberg `SFR1 Comdty` maps to CME Three-Month SOFR futures (`SR3`) rather than One-Month SOFR futures or another Bloomberg generic construction.
- *#metadata* — Confirm Bloomberg `SFI1 Comdty` maps to ICE Three Month SONIA Index futures (`SO3`) and not a legacy short-sterling or alternate SONIA contract.
- *#metadata* — Confirm actual tick-size schedules from Bloomberg `FUT_TICK_SIZE` and `FUT_TICK_VAL`, especially for near-expiry SONIA and SOFR contracts where ticks may vary.
- *#metadata* — Do Bloomberg `SFR1`-`SFR8` and `ER1`-`ER8` generics include serial months in the front of the curve, or do they track quarterly IMM contracts only? This must be verified before using whites/reds pack labels.
- *#roll-methodology* — Does Bloomberg’s rolling generic roll on last trade date, first notice, volume/open-interest switch, or a fixed schedule? Need to verify before using generic histories for z-scores/backtests.
- *#policy-pricing* — For Fed meeting pricing, should the product ingest 30-Day Fed Funds futures and treat SOFR futures as a supplementary curve, or should it approximate Fed probabilities from SOFR with a SOFR-EFFR basis assumption?
- *#policy-pricing* — For ECB meeting pricing, do PMs prefer €STR OIS, Euribor futures, or both? Euribor futures are liquid but include term/unsecured basis.
- *#policy-pricing* — For BoE meeting pricing, do desks prefer SONIA OIS or SONIA futures for meeting-level decomposition?
- *#basis* — What is the standard desk convention for futures-vs-OIS basis: futures-implied minus OIS-forward, or OIS-forward minus futures-implied? We should choose and document one sign convention.
- *#parameter-default* — Default z-score lookback: 60d/126d may be more useful than 252d for front contracts around policy cycles. Validate with users.
- *#workflow-validation* — How much do PMs use volume/open interest as a live signal versus as context only?
- *#workflow-validation* — Are whites/reds pack averages sufficient for the MVP, or do PMs immediately expect custom packs/bundles and mid-curve option links?
- *#data* — Do we need actual contract-month histories immediately, or are rolling generics enough for the first morning briefing page?
- *#data* — Do we need intraday data for the product to be credible around CPI/payrolls/FOMC, or is daily close-to-close sufficient for MVP?
- *#scope* — Should Fed Funds futures be added to this playbook as a separate USD curve family for proper FedWatch-style meeting probabilities?
- *#scope* — Should €STR futures be added if liquidity/availability supports them, so euro RFR policy pricing is not forced through Euribor futures?
- *#scope* — Should one-month SOFR / one-month SONIA futures be included for near-meeting granularity?

---

## 11. References

- *[CME Three-Month SOFR Futures]* — CME Group. Three-Month SOFR Futures contract specifications and product overview. Contract price is `100 - R`; `R` is business-day compounded SOFR over the reference quarter. Contract unit is `$2,500 × IMM Index`. Listed contracts include quarterly contracts and nearby serial contract months.
- *[CME FedWatch Methodology]* — CME Group. Understanding the CME FedWatch Tool methodology. Fed probabilities are based on 30-Day Fed Funds futures, with assumptions about EFFR, target ranges, and 25bp step sizes.
- *[ICE Three Month Euribor Futures]* — ICE Futures Europe. Three Month Euribor Futures contract specifications. Quotation is `100.00 minus the numerical value of the rate of interest`; settlement is based on the EMMI 3-month Euribor fixing. Listed delivery months include March/June/September/December quarterlies and serial months.
- *[EMMI Euribor]* — European Money Markets Institute. Euribor methodology. Euribor uses a hybrid methodology and measures euro unsecured wholesale funding for defined maturities.
- *[ICE Three Month SONIA Index Futures]* — ICE Futures Europe. Three Month SONIA Index Futures contract specifications. Quotation is `100.00 minus rate`; EDSP rate is based on reinvesting at SONIA over the accrual period.
- *[Bank of England SONIA]* — Bank of England. SONIA benchmark overview and key features. SONIA is administered by the Bank of England and based on actual overnight sterling transactions.
- *[ARRC / NY Fed SOFR transition]* — Alternative Reference Rates Committee / New York Fed SOFR transition material. CME launched one-month and three-month SOFR futures in May 2018.
- *[ISDA Benchmark Reform / RFR transition]* — ISDA and related benchmark-transition documentation for the move from LIBOR benchmarks to RFR-based markets.
- *[Tuckman]* — Tuckman, B., & Serrat, A. (2022). *Fixed Income Securities: Tools for Today's Markets* (4th ed.). Wiley. Relevant for futures, convexity, and short-rate derivatives foundations.
- *[Veronesi]* — Veronesi, P. (2010). *Fixed Income Securities: Valuation, Risk, and Risk Management*. Wiley. Relevant for futures, forwards, and curve-risk framework.
