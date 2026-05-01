# Government Bond Futures

**Status:** v1 draft — to be revised against external review and feedback from Brevan and advisory contacts.
**Scope:** 19 contracts across 9 countries currently in `/playbooks/bond_futures.yml`. US (TU/FV/TY/UXY/US/WN), Germany (Schatz/Bobl/Bund/Buxl), UK (Long Gilt), Japan (10Y JGB), France (OAT), Italy (Short BTP, Long BTP), Spain (Bono), Canada (CGB), Australia (YM 3Y, XM 10Y).
**Cross-references:** Cash bond CTD identity is jointly defined with `01_sovereign_bonds.md`. Futures DV01 onto an OIS curve depends on `02_ois_swaps.md` (`zero_curve_bootstrap`). STIR futures (SOFR / SONIA / Euribor / SARON / ASX 90-day Bills / CRA) live in `03_money_markets_and_cb_pricing.md` (when written) — bond futures and STIR futures are deliberately separated. Cross-country bond-future spreads (e.g., RX-TY) cross to `07_cross_currency_basis.md` for FX adjustment.

---

## 1. What it is

A government bond future is an exchange-traded standardized contract giving the holder of the short position the obligation, on a defined delivery day, to deliver a deliverable cash bond chosen from a basket of eligible issues, against a payment from the long equal to the futures settlement price multiplied by a *conversion factor* for the chosen bond, plus accrued interest. Most contracts in our universe are physically deliverable; the ASX 3Y and 10Y futures are the major exception, settling in cash against an average yield of a basket of bonds (covered separately in Section 4).

The contract specifies a *notional* underlying — a hypothetical bond with a stated coupon (typically 6% for legacy CME and Eurex contracts; 4% for Long Gilt and for Buxl) and a stated maturity bucket. The *deliverable basket* is the set of real outstanding bonds whose remaining maturity at delivery falls within a defined window (e.g., 6.5-10 years for the CME 10Y TY classic; 8.5-10.5 years for the Eurex Bund). The seller has the *quality option* of choosing which deliverable bond to actually deliver, and almost always picks the one that maximizes their economic return — the *cheapest-to-deliver* (CTD).

In scope for this module, by exchange and contract:

- **CME (CBOT) — physically deliverable, $100K face except TU/3Y at $200K, prices in 1/32nds:** TU (2Y), FV (5Y), TY (10Y classic), UXY (10Y ultra), US (30Y classic), WN (30Y ultra). All 6% notional coupon. [CME contract specifications]
- **Eurex — physically deliverable, EUR 100K face, prices in % of par:** Schatz/FGBS (2Y, 1.75-2.25y basket, 6% notional), Bobl/FGBM (5Y, 4.5-5.5y, 6%), Bund/FGBL (10Y, 8.5-10.5y, 6%), Buxl/FGBX (30Y, 24.0-35.0y, **4% notional** — the exception), BTP-Short/FBTS (3Y bucket, 2.0-3.25y, 6%), BTP-Long/FBTP (10Y, 8.5-11y, 6%), OAT/FOAT (10Y, 8.5-10.5y, 6%), Bono/FBON (10Y, 8.5-10.5y, 6%).
- **ICE — physically deliverable, GBP 100K face, prices per £100, ticks £10:** Long Gilt/R (10Y bucket, 8y9m-13y, **4% notional** post-Dec 2010). [ICE Long Gilt contract specs]
- **Osaka Exchange / JPX — physically deliverable, JPY 100M face:** JGB 10Y (7y-11y, 6% notional). Trades on OSE in Tokyo session and SGX in Asian/European session. [JPX JGB Futures Contract Specifications]
- **Montreal Exchange — physically deliverable, CAD 100K face:** CGB / 10Y CGB (8y-10y6m bucket, 6% notional). [TMX CGB specifications]
- **ASX — *cash-settled*, AUD 100K face, *quoted as 100 minus yield with variable tick value*:** YM (3Y, basket of CGS), XM (10Y, basket of CGS). Cash-settled against the average of bid/offer yield quotes for the basket bonds at expiry. [ASX 3 and 10 Year Treasury Bond Futures fact sheet]

The Bloomberg ticker convention in our playbook follows the exchange short-name pattern: `TU1 Comdty` is the CME 2Y futures rolling front-month; `RX1 Comdty` is the Eurex Bund rolling front-month; `G 1 Comdty` is ICE Long Gilt rolling front-month; etc. The trailing `1` denotes "first generic / front contract" — Bloomberg automatically rolls these series at standardized roll dates. Section 8 details the rolling-ticker mechanics and data lineage caveats.

## 2. Why it exists / role in the system

Bond futures sit at the intersection of three desks: dealer flow trading, asset manager duration management, and macro / RV hedge-fund leverage. Each uses them differently.

**Most-liquid expression of duration.** For nearly every developed-market sovereign curve, the relevant bond future at the canonical tenor is *the* most liquid duration vehicle — typically multiples deeper than any single cash bond and almost always tighter bid-asks. TY (10Y UST) and RX (10Y Bund) are among the most liquid fixed income instruments in the world. For a macro PM expressing a 10Y duration view in USD or EUR, the natural execution venue is the relevant future, not the cash bond. The same applies to 5Y (FV / Bobl), front-end (TU / Schatz), and long-end (US, WN, Buxl) — each future is the natural focal point of liquidity at its tenor. The exceptions are tenors where futures don't cover the curve well (e.g., UK has only the 10Y Long Gilt as a deeply liquid contract — short Gilt and Ultra Long Gilt exist but are thinner; France only has 10Y OAT among our universe).

**Capital efficiency vs cash bonds.** Cash bond positions require repo financing for leverage; futures positions only require initial and variation margin. For a leveraged macro fund, this is the difference between needing a prime broker repo line at every tenor and being able to clear duration risk through the exchange. The leverage profile depends on margin model (SPAN, SPAN 2, VaR-based at LCH), but for a $100K-face note the IM is typically 1-3% of notional, which translates to 30-100x leverage — comparable to a haircut-adjusted repo trade but operationally far simpler. ([CME, *Spreading Ultra 10 Against Foreign Sovereign Bond Futures*])

**Cheapest-to-deliver (CTD) makes the future track a specific cash bond, not the curve.** A bond future is *not* a bet on "the 10Y yield"; it's a bet on the price (and through that, the yield) of whichever cash bond is currently the CTD of the basket. The CTD is determined by jointly minimizing the cost-of-carry-adjusted invoice price across the basket — at low yields (below the 6% notional coupon), the shortest-duration eligible bond tends to be CTD; at high yields (above 6%) the longest-duration bond wins; near 6% the basket is "close to indifference" and CTD switching becomes more frequent. [CME, *Delivery Basket for 10-Year Treasury futures*; Actrix Financial Technology, *Bond Futures Explained*] This means that in a low-yield regime (the 2009-2021 era for most DM sovereigns), the TY future actually tracks closer to a 6.5-7Y maturity bond than to a "10Y" headline.

**The basis trade.** Hedge funds — especially relative-value funds — run the *cash-futures basis trade*: long the CTD cash bond, short the future, financed in the repo market. The trade earns the spread between the implied repo rate (the rate at which the futures price implies the CTD can be financed) and the actual term repo rate paid in the cash market. The trade is highly leveraged (often 50-to-1) and accounted for as much as half of all hedge fund Treasury positions and ~25% of dealer repo lending pre-March 2020 [Office of Financial Research, *Hedge Funds and the Treasury Cash-Futures Disconnect*, 2021]. The March 2020 unwind — discussed in Section 3 — was a defining stress event for the Treasury market and remains the canonical "what can go wrong with futures" reference case.

**Curve and cross-country expression.** Calendar spreads (front vs next contract), inter-commodity spreads (e.g., FV-TY for US 5s10s, RX-Bund vs OAT for Bund-OAT), and cross-country spreads (RX-TY DV01-weighted with FX adjustment for the German-US 10Y differential) are all standard RV expressions. CME, Eurex, ICE, and TMX all offer margin offsets for inter-commodity spreads on their venues, materially reducing capital cost vs gross positions. [CME inter-commodity spread credits; Eurex cross-product margin]

**Positioning data and macro signaling.** CFTC Commitments of Traders reports for CME futures, and similar data from Eurex and ICE, give one of the few near-real-time windows on macro fund and dealer positioning in rates. Net leveraged-fund short Treasury futures positions hit over $1 trillion in March 2025 and were ~$660 billion in February 2020 [NY Fed speech, May 2025]. PMs treat large positioning extremes as an input — typically as a contrarian signal at extremes, or as a confirmation/momentum signal when positioning is moving in the same direction as price.

## 3. What its movements signal (macro context)

Bond-future moves carry the same macro information as the underlying cash bonds *plus* their own technical layer (CTD, repo, basis, roll dynamics). For a macro PM, the central practical fact is: the front-month bond future is the *price* of duration in that currency at that tenor, scaled by the CTD's price sensitivity. So everything that drives the underlying sovereign curve drives the future, with one extra layer.

### Decomposition

Roughly, the change in a bond future's price over a period decomposes as:

```
Δfuture_price ≈ -DV01_CTD × Δyield_CTD     (the bulk of any move, especially mid-cycle)
              + Δnet_basis                  (basis convergence to zero by delivery)
              + Δquality-option-value       (changes in CTD-switching probability)
              + roll/calendar effects       (front-back month basis dynamics)
              + CFTC-positioning unwind     (e.g., March 2020 basis-trade unwind)
```

The first term dominates day-to-day moves. The second through fifth terms matter at events (delivery month, big yield moves through 6%, basis stress).

### Curve shape via futures

Futures-implied curve trades — TU/FV (2s5s), FV/TY (5s10s), TY/US (10s30s), and butterflies like TU/FV/TY (2s5s10s) — are the standard exchange-listed expressions of curve views. Same macro reads as cash-bond curve trades (steepener / flattener / bull / bear regime classifications), with two important distinctions:

1. The *DV01-weighted ratio* changes as yields move (because DV01 of CTD is yield-dependent). A 1:1 contract spread is not a clean curve trade; it's leveraged toward whichever leg has higher DV01. PMs run these on DV01-weighted ratios that are recalculated at meaningful yield levels.
2. *CTD changes* mid-trade can break a curve trade's intended exposure. If the CTD on TY shifts from a longer-duration bond to a shorter-duration bond mid-trade (because yields cross the 6% threshold or because the basket rolls), the trade's effective curve exposure changes step-wise.

### Specific historical episodes worth knowing

These are the events you reach for when explaining what bond-future movements have meant.

**March 2020 cash-futures basis unwind.** Beginning in late February 2020 with COVID concerns and accelerating sharply in mid-March, hedge funds were forced to unwind cash-futures basis trades. From 3 March to 17 March, hedge funds reduced short futures positions by approximately $62 billion, matched by simultaneous unwinds of repo-funded cash positions [Brookings paper as reported by The DESK]. Initial margin requirements on Treasury futures more than doubled for certain maturities. The basis spread (gap between cash and futures implied prices) jumped by approximately 100bp at peak; the spread between implied and actual term repo for the June 2020 5Y note future widened by ~50bp before Fed intervention restored functioning [Dallas Fed, July 2025]. Hedge funds with UST exposure averaged ~-7% returns that month [Federal Reserve FEDS WP 2021-038]. This is the canonical reference case for: (a) why basis trade leverage matters, (b) how futures and cash markets can decouple under stress, and (c) why initial margin and repo-funding stability are joint preconditions for the basis trade to function. Fed intervention (asset purchases, repo facilities) restored functioning within days.

**LDI gilt crisis, September-October 2022.** Following the UK "mini-budget" of 23 September 2022, 30Y gilt yields rose ~100bp+ in days. UK pension funds running leveraged LDI strategies — typically long gilt futures and gilt total return swaps to match their long-dated liabilities — faced collateral calls that forced them to unwind, generating a self-reinforcing sell-off. The Bank of England intervened on 28 September with a temporary £65bn long-dated gilt purchase program. The Long Gilt future (G) saw historic moves and the Ultra Long Gilt future (UL) was at the center of the LDI dynamics. [BoE working paper, 2023; BIS]

**Fed hiking cycle, 2022-2023.** Front-end UST futures (TU, FV) showed the fastest repricing in modern memory — TU yields moved from ~0.7% in late 2021 to ~5% by mid-2023. This was also a CTD-dynamics event: as 2Y yields crossed materially above 6% in 2023, the CTD on TU shifted toward longer-duration deliverables in the basket, changing the contract's effective duration mid-cycle in a way that repo books had to accommodate.

**BoJ YCC adjustments, December 2022 and March 2024.** The 10Y JGB future (JB) was directly affected by the BoJ raising the YCC band cap from 0.25% to 0.50% on 20 December 2022, then formally exiting YCC in March 2024 [BoJ]. JGB futures had been exceptionally low-vol in the YCC era because 10Y JGB yields were pinned. The December 2022 widening was the largest single-day move in JGB futures since 2003. Pre-March 2024 JGB futures data is genuinely a different regime — Bucket 2 models that span the YCC era have to handle the structural break.

**Eurozone debt crisis, 2010-2012.** Bund-BTP and Bund-OAT futures spreads widened dramatically. The IK (Long BTP) future was launched September 2009 specifically because the widening Bund-BTP spread made hedging Italian bonds with the Bund future increasingly basis-risky [Eurex circular June 2009]. The 10Y Bund-BTP futures spread reached over 5 percentage points in late 2011 — the canonical reference case for sovereign-credit-driven Eurozone curve trades.

### What futures *don't* signal cleanly

Bond-future yield reads are *CTD yields*, not "10Y yields" in the abstract. When PMs need a clean tenor-anchored yield, they go to the cash curve (`01_sovereign_bonds.md`). When they need execution liquidity or leveraged duration, they use futures. The two are joined by the basis (`net_basis`, `gross_basis`, `implied_repo_rate`, `quality_option_value`) — the bridge tools live in this module's tool inventory.

## 4. Market microstructure (just enough)

**Quoting and tick mechanics.** Conventions differ materially across exchanges and matter for any PnL or DV01 calculation:

- **CME UST futures** quote in points and 1/32nds, with sub-tick resolution varying by contract: TU and 3Y use 1/4 of 1/32 (= $7.8125 per contract per tick at the front-end large-face); FV uses 1/4 of 1/32 ($7.8125); TY and UXY use 1/2 of 1/32 ($15.625); US and WN use 1/32 ($31.25). The 2Y and 3Y contracts have $200K face; the 5Y onwards have $100K face.
- **Eurex contracts** quote in % of par (e.g., 132.45 = 132.45% of par). Tick size is 0.005% for Schatz/FGBS (= EUR 5 per contract); 0.01% for Bobl, Bund, Buxl, BTP-Short, BTP-Mid, BTP-Long, OAT, Bono (= EUR 10 per contract). Buxl has a EUR 5 minimum tick (special). [Eurex Contract Specifications]
- **ICE Long Gilt** quotes per £100 nominal value in increments of 0.01 (= £10 per tick on £100K face).
- **JGB 10Y futures** quote in JPY per JPY 100 par (e.g., 145.21 = 145.21% of par); face value is JPY 100 million.
- **CGB** quotes per CAD 100 face in increments of 0.01 (= CAD 10 per tick on CAD 100K face).
- **ASX YM and XM are different.** They quote as `100 - yield` (e.g., 96.50 = 3.50% yield). The tick is 0.005% (= 0.5 basis points) of yield, not price. *Tick value is variable and yield-dependent* — the dollar value per tick changes as yields move (~AUD 30 per tick at typical levels for the 3Y contract per RBA Bulletin September 2014). They are cash-settled, not deliverable. This is a structural difference, not a quirk.

**Settlement and delivery.** All listed bond futures in scope are listed on the standard quarterly cycle (March / June / September / December) with the front contract being the most liquid. Most are physically deliverable; ASX YM and XM are cash-settled. For deliverable contracts, last trading day and last delivery day vary by exchange:

- **CME 2Y and 5Y (TU, FV):** last trading day = last business day of contract month; last delivery day = third business day of the next month.
- **CME 10Y, ultra-10Y, 30Y, ultra-30Y (TY, UXY, US, WN):** last trading day = 7th business day before last business day of contract month; last delivery day = last business day of contract month.
- **Eurex Schatz, Bobl, Bund, Buxl, BTPs, OAT, Bono:** delivery day = 10th calendar day of the delivery month (or next exchange day if not an exchange day); last trading day = 2 exchange days prior to delivery; trading ends at 12:30 CET. [Eurex contract specs]
- **ICE Long Gilt:** last trading day = 2 business days before last business day of delivery month at 11:00 London; delivery day = any business day in delivery month at seller's choice. [ICE Long Gilt]
- **TMX CGB:** last trading day = 7th business day before last business day of delivery month at 13:00; delivery day during the delivery month at seller's choice. [TMX CGB]
- **JPX JGB 10Y:** last trading day = 7 business days before delivery day; delivery day = 20th of delivery month or following business day. [JPX]

**Approximate proportion of contracts going to physical delivery.** TMX statistics show that approximately 15% of CGB open positions actually result in delivery — the rest roll or close. CME and Eurex figures are similar in magnitude; the vast majority of macro-fund and CTA flow is closed/rolled, not delivered. The minority that go to delivery are largely the basis-trade community and select dealers managing balance-sheet exposures. [TMX]

**Cheapest-to-deliver mechanics.** The CTD is the bond whose net cost of delivery (cash-and-carry to delivery, less futures-leg PnL on the conversion-factor invoice) is minimized. Equivalently, the bond with the highest *implied repo rate* (the rate at which a long-bond / short-future / borrow-cash-via-repo trade breaks even) is the CTD. Two things matter for macro PMs more than the formal definition: which bond is currently CTD, and where the next CTD switch is likely. CME, Eurex, ICE, and TMX all publish official conversion-factor tables for each contract month [Eurex *Notified Bonds | Deliverable Bonds and Conversion Factors*; CME Treasury Analytics], and Bloomberg's `<contract> Comdty DLV` function shows the live basket with implied repo, gross basis, and net basis per deliverable.

**The 6% notional coupon and the CTD-shift threshold.** Most contracts in our universe (all CMEs, all Eurex except Buxl, JGB, CGB, BTPs, OAT, Bono) use a 6% notional coupon for conversion factor calculation. Mathematically, when actual yields are below 6%, the CTD tends to be the *shortest-duration* eligible bond (because the conversion factor more than compensates for its lower coupon); above 6%, the longest-duration bond. The 4%-coupon contracts (Long Gilt, Buxl) shift this threshold to 4%. The 2008-2021 era of sub-2% yields meant the CTD on TY was almost always at the *short* end of the 6.5-10 year basket (effectively a ~6.5-7Y bond) — a fact embedded in the contract's effective duration, basis-trade history, and DV01 calculations. The 2022-2025 hiking cycle moved several contracts toward or above their 6% notional, shifting CTDs and creating regime breaks for any model trained on the prior decade.

**Quality option, end-of-month option, wildcard option.** The short has not just CTD choice but also timing options: at CME, the short can declare delivery at various points within the delivery month (the *end-of-month option*) and even submit delivery notice after the futures market closes on certain days while bonds still trade (the *wildcard option*). These embedded options have small but non-zero value (typically a few cents to a few /32nds) and are why futures prices generally trade slightly *below* their pure cost-of-carry theoretical value. [TMX *CGB Reference Manual*]

**ESG / Green bond exclusions.** Eurex BTP futures explicitly exclude "BTP Futura," "BTP Valore," "BTP PIU," and "BTP Green" from the deliverable basket. Eurex OAT futures exclude OATs explicitly issued as "Green OAT (OAT verte)." Eurex EU Bond Futures exclude "NGEU Green Bonds." [Eurex Contract Terms] These exclusions matter because the CTD calculation must filter the eligible basket; using the full Italian/French/EU debt universe rather than the futures-eligible subset will produce wrong CTDs.

## 5. Key metrics PMs watch

Per contract or contract universe:

- **Front-month price level**, daily change in price and in CTD-implied yield, daily volume, daily open interest. Critical: open-interest changes net of the calendar-roll period are a positioning signal.
- **CTD identity**, **CTD remaining maturity**, **CTD coupon**, **CTD price**.
- **Net basis**, **gross basis**, **implied repo rate**, **quality option value** for the front contract.
- **Calendar spread** (front month minus next-deferred), watched especially during the roll window. A widening calendar spread can reflect roll cost, expected CTD changes, or repo-market squeeze.
- **Inter-commodity spread DV01-weighted ratios**: TU/FV, FV/TY, TY/UXY, TY/US, RX/Bund-OAT (Bund-OAT spread), RX/Bund-BTP (Bund-BTP spread), JB/RX (cross-currency Japan-Germany).
- **Cross-country bond-future spreads**: TY-RX (10Y US-Germany), TY-G (10Y US-UK), all DV01-weighted. These are the canonical macro RV expressions of cross-CB rate divergence.
- **Net positioning** from CFTC Commitments of Traders (Treasury futures), CME large-trader, and similar Eurex / ICE position datasets — particularly the leveraged-fund net short Treasury position and asset-manager net long.
- **CTD basis time series** (gross basis, net basis, implied repo) — under normal conditions converges deterministically to zero by delivery; meaningful deviations are a stress flag (March 2020 reference case).
- **Roll-progression analytics** during the quarterly roll window — what fraction of open interest has migrated from front to next-deferred, vs historical roll patterns.

## 6. Standard workflows

The workflows below are the recurring units of analysis on a rates desk that involve bond futures. Each maps to a workflow file in `/workflows/` (or "TBD").

- **Morning bond-futures dashboard** — front-month price, daily change, volume, open interest, CTD yield, calendar spread, basis snapshot across all 19 contracts. → `workflows/morning_briefing.md`
- **Cross-country macro RV** — DV01-weighted bond-future spreads (TY-RX, TY-G, TY-JB), with FX-aware analogues for FX-hedged versions. → `workflows/relative_value.md`
- **Curve trades via futures** — TU/FV, FV/TY, TY/US (and analogues per country), DV01-weighted, with the CTD-switch warning surfaced explicitly. → TBD
- **Calendar roll analysis** — front-back spread, roll cost, fraction-rolled vs historical pattern, recommended roll window. → TBD
- **CTD identification and delivery basket monitoring** — current CTD, next likely CTD if yields shift by N bp, full basket with conversion factors, gross/net basis, implied repo, quality-option value. → TBD
- **Basis-trade monitoring (RV / stress)** — implied repo rate vs term repo rate; basis convergence vs delivery; stress flag if basis blows out (March 2020 reference). → TBD; cross to repo/funding-stress workflow.
- **Positioning-extremes scanner** — leveraged-fund net positioning across CME contracts vs historical percentile; analogues for Eurex and ICE where data permits. → TBD
- **Stress / historical replay** — apply historical futures price paths (March 2020, Sept 2022 LDI, Dec 2022 BoJ band shift) to current positions. → TBD
- **Curve regime classification on futures** — same regime taxonomy as sovereign / OIS, applied to bond-future curves. → `workflows/regime_classification.md`
- **Cross-domain reconciliation: cash vs futures** — cash-bond CTD yield vs front-month future implied yield; expected delta from carry/basis. → cross to `01_sovereign_bonds.md`.
- **Cross-domain reconciliation: futures vs OIS / vs swap** — the "swap spread vs futures" angle (futures invoice spreads, swap-vs-future basis). → cross to `02_ois_swaps.md`.

## 7. Models and methodologies

**Front-month price level / volume / open interest** (1A) — basic time-series of `last_price`, `volume`, `open_interest` from the rolling ticker.

**CTD-implied yield** (1B) — given the front-month future, CTD identification, conversion factor, and the CTD's coupon/maturity, solve for the yield that prices the CTD at `future × conversion_factor + accrued`. Yield-solver convention (semi-annual vs annual compounding) is jurisdiction-dependent. [Tuckman, Ch. 21; CME *Understand Treasuries Contract Specifications*]

**Implied repo rate** (1B) — the rate at which buying the CTD bond, financing in repo, and short-selling the future breaks even at delivery. Highest implied repo across the basket identifies the CTD. [Tuckman, Ch. 21; TMX CGB Reference Manual]

**Gross basis** (1B) — `cash_bond_price - future_price × conversion_factor`. Pure price difference, ignoring carry.

**Net basis** (1B) — gross basis adjusted for the bond's coupon income earned and repo cost paid over the life of the trade. Net basis ≈ 0 for the CTD by delivery, modulo quality-option and timing-option premia. [Tuckman]

**Quality option / wildcard option valuation** (Bucket 2) — the embedded options held by the short (CTD-switch right, end-of-month timing, wildcard timing) have model-dependent values. Standard approach: simulate yield-curve paths under a calibrated short-rate model, evaluate optimal CTD selection at each delivery date. Output value is sensitive to model choice and volatility input. [Practitioner standard: Hull and White or Gaussian short-rate model fitted to swaption surface]

**DV01 of the future** (1B) — there are two common conventions: (a) "CTD DV01" = DV01 of the CTD bond divided by its conversion factor; (b) "futures DV01" = direct numerical bump of the futures price for a 1bp shift in the CTD-implied yield. They are nearly identical for non-CTD-switching cases. Required for nearly every hedging or curve-trade calculation. [Actrix Financial Technology, *Bond Futures Explained*]

**DV01 of the future projected onto an OIS curve** (1B; cross-domain) — for hedging futures against swap exposures, project the CTD cashflows onto the relevant OIS discount curve (`02_ois_swaps.md` `zero_curve_bootstrap`). Returns a DV01 directly comparable to a swap DV01.

**Calendar spread / roll cost** (1A given inputs; 1B for predictive) — front minus next-deferred futures price; expected roll cost computed from term repo rate, expected CTD change, and basket-roll dynamics.

**Inter-commodity spread (single-currency)** (1A weighted by fixed default; 1B with DV01-weighting) — TU/FV, FV/TY etc., 1:1 raw or DV01-weighted.

**Cross-country bond-future spread** (1B) — DV01-weighted, with optional FX-translation. The naive "TY price minus RX price" is meaningless; the DV01-weighted, FX-adjusted spread is the macro-RV expression. [CME, *Spreading Ultra 10 Against Foreign Sovereign Bond Futures*]

**Curve regime classification on futures** (1B) — same heuristic taxonomy as sovereign and OIS, applied to bond-future curve.

**Z-score and percentile rank** — 1A with default 252d window; 1B with custom window/method.

**Positioning-extremes scanner** (1B) — combine CFTC Commitments of Traders / CME large-trader / Eurex positioning data; flag percentile extremes over rolling window; cross-reference with price moves to distinguish "extreme positioning leading reversal" from "extreme positioning confirming trend."

**PCA on bond-future curve** (Bucket 2) — principal components of bond-future-price changes (or CTD-yield changes) across tenors of one country's complex (e.g., TU/FV/TY/US). Same level/slope/curvature factor structure as cash-bond PCA, with the practical advantage of tradable instruments per pillar. [Litterman-Scheinkman 1991]

**HMM regime classifier on futures** (Bucket 2) — Gaussian HMM on a feature vector (price changes, vol, basis, positioning).

**GARCH on futures-price changes** (Bucket 2) — conditional vol forecasting at chosen contracts. [Bollerslev 1986]

**Multi-country bond-future PCA** (Bucket 2) — joint cross-section across all contracts × tenors; identifies global rate factors and per-country residuals. Calendar synchronization across CME / Eurex / ICE / OSE / TMX / ASX is the engineering challenge.

**Basis-trade implied-repo-vs-actual-term-repo monitoring** (Bucket 2 if predictive; 1B if descriptive) — descriptive monitoring is straightforward. Predictive use (forecast basis blow-out probability) requires a model of dealer-balance-sheet capacity and hedge-fund positioning, which we treat as Bucket 2.

**Stress / historical replay** (1B) — apply historical futures-price paths to current positions; deterministic given (window, position, interpolation/roll method).

**Cointegration / OU half-life on cross-country futures spreads** (Bucket 2) — same classification reasoning as the OIS module: statistical inference, deterministic given parameters but model-fitting in nature.

**Out of scope (deliberately):** trade signal generation, position sizing, tax / accounting treatment of futures vs cash bonds, exchange-rule-level delivery procedures.

## 8. Data requirements

This section is a forward-looking specification of what data each tool in the bond-futures domain needs. For the current ingestion contract, see `/playbooks/bond_futures.yml`.

### 8.1 Data fields referenced

**Time-series fields (per ticker, per business day):**

- `last_price` (Bloomberg `PX_LAST`) — daily closing futures price in the contract's quoting convention.
- `open_interest` (Bloomberg `OPEN_INT`).
- `volume` (Bloomberg `PX_VOLUME`).
- `daily_settlement_price` — for some venues this differs from `PX_LAST` (e.g., Eurex daily settlement is volume-weighted in the minute before 17:15 CET); maintain as a separate field if available.
- `bid` / `ask` / `bid_ask_spread` — market depth indicator; not always populated for back-history.
- `realized_vol` — rolling realized volatility of price changes (computable from `last_price`).

**Reference / static fields (per ticker):**

- `security_name` (Bloomberg `SECURITY_DES`).
- `expiry_date` (Bloomberg `LAST_TRADEABLE_DT`) — last trading day for the active contract that the rolling ticker currently points to.
- `maturity_date` (Bloomberg `FUT_DLV_DT_LAST`) — last delivery day for the current contract.
- `contract_size` (Bloomberg `FUT_CONT_SIZE`) — face value (e.g., 100,000 for USD/EUR/GBP/CAD majors; 200,000 for TU and 3Y; 100,000,000 for JGB).
- `quote_units` (Bloomberg `QUOTE_UNITS`) — quoting convention (% of par, points, 100-yield, etc.).
- `contract_code` — short symbol (TU1, RX1, etc.).
- `bucket_label` — semantic tenor label (2Y, 10Y_CLASSIC, 10Y_ULTRA, BUND, BUXL, LONG_GILT, JGB_10Y, ACGB_3Y, ACGB_10Y, etc.).
- `curve_family` — exchange/country grouping (UST_FUT, DE_FUT, UK_FUT, etc.).
- `country`, `currency`, `tenor`.
- `is_rolling_contract` — True for the front-month ticker series.
- `bloomberg_ticker` — full identifier (e.g., `TU1 Comdty`).
- `exchange` — CME, Eurex, ICE, OSE, TMX, ASX.
- `notional_coupon` — 6% for most; 4% for Long Gilt and Buxl.
- `tick_size` and `tick_value` — for fixed-tick-value contracts; for ASX YM/XM, store the formula and the inputs (face value, current yield) needed to compute it.
- `quoting_basis` — "price_per_par" (most), "100_minus_yield" (ASX), "1/32nds" (CME).
- `is_cash_settled` — False for all in scope except ASX YM/XM.
- `first_live_date` — the date from which this ticker has live (not vendor-proxied or absent) quotes.
- `vendor_backfill_flag` — boolean: is pre-`first_live_date` history vendor-constructed?
- `contract_launch_date` — when the contract was launched at the exchange. Notable values to flag: FBTP (Long BTP) launched September 2009; FBTS (Short BTP) launched October 2010; UXY (Ultra 10Y) launched January 2016; WN (Ultra T-Bond) launched January 2010; Long Gilt notional changed from 6% to 4% for December 2011 delivery onwards.
- `regime_break_dates` — known structural breaks (e.g., Long Gilt 6%-to-4% notional, BoJ YCC introduction Sept 2016 / exit March 2024, COVID basis-trade unwind March 2020, UK LDI crisis Sept-Oct 2022).
- `exchange_session_calendar` — needed for synchronization (Eurex session, CME RTH/ETH, ICE London, OSE Tokyo, etc.).

**Per-contract static (not per ticker, per actual contract month):**

- `delivery_basket` — list of eligible cash bonds (cusip / ISIN, coupon, maturity, outstanding size).
- `conversion_factor` — per deliverable bond per contract month (published by exchange).
- `cheapest_to_deliver` — daily snapshot of the current CTD identity and its remaining maturity.
- `last_trading_day`, `first_delivery_day`, `last_delivery_day` — per actual contract month.

**External / cross-domain inputs:**

- Cash bond yields (CTD and full basket) — owned by `01_sovereign_bonds.md`.
- OIS / swap zero curves — owned by `02_ois_swaps.md`. Required for futures-DV01-onto-OIS calculations and for pricing the basis trade in matched-tenor terms.
- Term repo rates — owned by `03_money_markets_and_cb_pricing.md` (when written) or stored at a domain-shared level. Required for net basis and implied repo rate.
- Central bank meeting calendars and policy rates — same domain.
- FX spot rates — required for cross-country FX-adjusted spreads.
- CFTC Commitments of Traders / CME large-trader / Eurex / ICE positioning data — external data sources, not in playbook.
- Swaption volatility surface — required for quality-option-value pricing. Owned by `06_swaptions_and_rates_vol.md`.

### 8.2 Per-tool data requirements

Format: `tool_name` — primary fields needed; key universe / history needs; cross-domain dependencies.

#### Bucket 1A — built (assuming ingestion of the playbook)

**`futures_price_level`** — `last_price`; per (curve_family, contract_code, date); 252+ business days history for z-score.

**`futures_volume_oi`** — `volume`, `open_interest`; per (curve_family, contract_code, date).

**`futures_calendar_spread`** — `last_price` for front and next-deferred contract; needs both rolling tickers per family. *Currently only front-month is in playbook; next-deferred (e.g., `TU2 Comdty`) is a planned playbook extension.*

**`scanner_futures`** — `last_price` across the full (curve_family × contract_code) universe; 252+ business days for z-score per series.

#### Bucket 1A — planned

**`inter_commodity_spread`** — `last_price` at two contracts of the same currency-country complex (e.g., TU/FV); fixed default weight (1:1) for raw, DV01-weighted variant in 1B.

**`positioning_snapshot`** — CFTC COT / CME large-trader / similar Eurex feeds for current positioning; static reference position thresholds for percentile classification.

#### Bucket 1B — planned

**`zscore_custom`** — generic primitive.

**`ctd_identifier`** — for a contract month, the full deliverable basket plus cash-bond prices (cross-domain `01_sovereign_bonds.md`), conversion factors (per-contract reference), and term repo rate (cross-domain). Returns the current CTD by maximum-implied-repo-rate selection. Foundational for nearly every other 1B/2 tool.

**`implied_repo_rate`** — same data as `ctd_identifier`. Returns implied repo for the CTD (and optionally for the full basket as a ranked list).

**`gross_basis`**, **`net_basis`** — same data; arithmetic on cash bond price, futures price, conversion factor, accrued, and repo cost.

**`futures_dv01`** — CTD identity plus cash-bond DV01 (cross-domain) plus conversion factor. Returns DV01 per contract.

**`futures_dv01_on_ois`** — CTD cashflows plus OIS zero curve (cross-domain `02_ois_swaps.md`). Returns DV01 directly comparable to OIS swap DV01.

**`ctd_yield`** — CTD identity plus CTD price (cross-domain) plus the convention-correct yield-solver. Note: for the ASX YM/XM, "CTD yield" is replaced by the basket-average-yield concept since the contracts are cash-settled — the yield is *built into the contract* (`100 - yield = price`).

**`inter_commodity_spread_dv01_weighted`** — needs `futures_dv01` for both legs; produces DV01-neutral curve trades.

**`cross_country_futures_spread_dv01_fx_adjusted`** — needs `futures_dv01` and FX (cross-domain). The macro RV bread-and-butter expression.

**`calendar_roll_analytics`** — front and next-deferred prices; current open-interest split between contracts; historical roll patterns; expected roll cost from term repo and basket dynamics.

**`futures_curve_regime`** — futures-curve PCA-style or threshold-rule regime classification on a country's bond-futures complex.

**`positioning_extremes_scanner`** — positioning data (CFTC, CME, Eurex) plus historical lookback; flags percentile extremes.

**`stress_replay_futures`** — historical futures-price path windows applied to current positions.

**`basis_trade_monitor`** — `implied_repo_rate` for the CTD plus actual term repo (cross-domain); tracks the spread and percentile vs history.

#### Bucket 2 — planned

**`pca_futures_curve`** — per-country bond-future-price-change panel; lookback 1-10Y; returns level/slope/curvature factors.

**`hmm_futures_regime`** — feature vector (price changes, basis, positioning, vol).

**`cointegration_test_futures`** — paired bond-future series across countries (e.g., TY-RX), test choice.

**`half_life_ou_futures`** — futures-spread series; OU fit.

**`garch_futures_vol`** — futures-price-change series; GARCH model.

**`quality_option_valuation`** — basket plus calibrated short-rate model plus swaption surface (cross-domain). Returns option value per contract; full repricing of the future under explicit option valuation.

**`multi_country_futures_pca`** — full panel across all 19 contracts × time; calendar synchronization is the engineering challenge. Output: global rates factors + per-country residuals.

**`futures_basis_predictive_model`** — predicts basis blow-out probability from dealer-balance-sheet capacity proxies, hedge-fund positioning, and repo-funding stress indicators.

#### Aspirational

**`cross_asset_basis_macro_indicator`** — combines basis stress across UST, Bund, gilt, JGB; produces a global "basis-trade health" signal.

**`futures_implied_term_premium`** — extract term-premium-style decomposition from a country's futures complex via affine model on CTD yields; cleaner than cash because of better liquidity, but messier because of CTD/quality-option dynamics.

### 8.3 Cross-domain data dependencies summary

- Cash bond CTD yields, basket pricing → owned by `01_sovereign_bonds.md`. Input to nearly every Bucket 1B/2 tool here.
- OIS / swap zero curves → owned by `02_ois_swaps.md`. Required for `futures_dv01_on_ois` and basis-trade-vs-OIS comparisons.
- Term repo rates and overnight RFR fixings → owned by `03_money_markets_and_cb_pricing.md` (when written). Required for `implied_repo_rate`, `net_basis`, basis-trade monitoring.
- Central bank meeting calendars → same.
- FX spot rates → `07_cross_currency_basis.md`. Required for `cross_country_futures_spread_dv01_fx_adjusted`.
- Swaption vol surface → `06_swaptions_and_rates_vol.md`. Required for `quality_option_valuation`.
- CFTC COT / CME large-trader / Eurex positioning → external data feeds, not in any current playbook. Required for `positioning_snapshot`, `positioning_extremes_scanner`, and the predictive basis-trade model.

## 9. Tool inventory

Format: **`tool_name`** — takes [inputs], runs [model/computation], returns [outputs], used in [workflows] to surface [macro implication].

### Built (Bucket 1A — assuming playbook ingestion)

**`futures_price_level`** — takes a (curve_family, contract_code, date) plus history; queries the daily futures table; returns last price, 1d/5d/22d changes (in points, percent of par, or yield depending on quoting convention), 252d high/low/percentile, 252d z-score; used in `morning_briefing` and any directional analysis.

**`futures_volume_oi`** — takes a (curve_family, contract_code, date) plus history; returns daily volume, open interest, and changes thereof, plus 252d z-score; used to surface positioning-flow signals (volume spike, OI build/unwind) in `morning_briefing`.

**`scanner_futures`** — takes a z-score threshold (default 2.0) and contract universe; iterates `futures_price_level` across the full (curve_family × contract_code) grid; returns ranked list of extremes; used in `morning_briefing` and idea generation.

### Planned (Bucket 1A)

**`inter_commodity_spread`** (raw 1:1) — takes a curve_family and two contract_codes; computes the price differential time series; returns current spread, period changes, 252d z-score; used in `morning_briefing` and curve workflows. Note: the 1:1 raw spread is rarely DV01-neutral; the `_dv01_weighted` 1B sibling is the trade-relevant version.

**`positioning_snapshot`** — takes a contract or contract universe and a positioning data source (CFTC COT, CME, Eurex); returns current net positioning by category (leveraged funds, asset managers, dealers), period changes, 252d percentile rank; used in `morning_briefing` and macro idea generation. Cross-dependency on external positioning data feeds.

### Planned (Bucket 1B)

**`zscore_custom`** — generic primitive (same as in OIS/sovereign).

**`ctd_identifier`** — takes a contract (curve_family, contract_code, contract month), the deliverable basket with conversion factors, cash-bond prices for each deliverable (cross-domain `01_sovereign_bonds.md`), and the term repo rate; computes implied repo rate per deliverable; returns the CTD (highest implied repo) plus the full basket ranked. Foundational for nearly every other 1B/2 tool — every basis, DV01, and quality-option calculation downstream depends on this.

**`implied_repo_rate`** — takes a contract and CTD identity; returns implied repo for the CTD, and optionally for full basket; used in basis-trade monitoring and CTD-stability analysis.

**`gross_basis`** — takes a contract and a deliverable bond; returns `cash_bond_price - future_price × conversion_factor`; used as a building block for basis-trade tools.

**`net_basis`** — takes a contract, a deliverable bond, the term repo rate, and time-to-delivery; returns the net basis (gross basis adjusted for carry); used in `basis_trade_monitor`, ASW workflows, and stress monitoring. Net basis ≈ 0 for the CTD by delivery in normal markets — meaningful deviation is a stress signal.

**`futures_dv01`** — takes a contract and CTD identity; computes DV01 either as `DV01_CTD / conversion_factor` (analytical) or via numerical bump (1bp shift in CTD-implied yield, recompute futures price); returns DV01 per contract; foundational for all hedging and curve-trade calculations.

**`futures_dv01_on_ois`** — takes a contract, CTD identity, and the OIS zero curve (`02_ois_swaps.md` `zero_curve_bootstrap`); projects the CTD cashflows onto the OIS curve; returns DV01 directly comparable to a swap DV01; used in invoice-spread and futures-vs-swap RV workflows.

**`ctd_yield`** — takes a contract; identifies CTD via `ctd_identifier`; solves for yield from CTD price using the convention-correct compounding (semi-annual for USTs, BTPs, OATs; annual conventions for Schatz/Bund/Bobl/Buxl per German convention — verify); returns CTD yield level and changes. *Note: ASX YM/XM are quoted as `100 - yield`, so this tool returns the contract-yield directly without CTD intermediation.*

**`inter_commodity_spread_dv01_weighted`** — takes a curve_family and two contract_codes; uses `futures_dv01` for each leg; returns DV01-neutral spread, current value, period changes, z-score; used in curve trades on a single country's complex.

**`cross_country_futures_spread_dv01_fx_adjusted`** — takes two contracts from different currency complexes plus FX spot (`07_cross_currency_basis.md`); uses `futures_dv01` for each leg, FX-translates to a common currency; returns FX-adjusted DV01-neutral spread; *the canonical macro RV expression for cross-country rate divergence views*. Used in `relative_value` workflow.

**`calendar_roll_analytics`** — takes the front and next-deferred contracts and historical roll patterns; computes current calendar spread, fraction-rolled-vs-historical, expected roll cost from term repo; returns roll diagnostics; used in pre-roll-window planning.

**`futures_curve_regime`** — takes a country's bond-future complex (e.g., TU/FV/TY/UXY/US/WN for US) and lookback windows; classifies the futures-curve move as BULL_STEEPENER, BEAR_FLATTENER, etc., against thresholds; used in `regime_classification` workflows. Same 1B classification reasoning as in OIS module.

**`positioning_extremes_scanner`** — takes positioning data per contract plus historical lookback; flags contracts where current positioning is in the top/bottom percentile; cross-references with price moves; used in `morning_briefing` and macro-flow idea generation.

**`stress_replay_futures`** — takes a position spec (contracts + sizes) and a historical window (e.g., 2020-03-09 to 2020-03-20 for the COVID basis-trade unwind; 2022-09-23 to 2022-10-14 for the LDI crisis; 2022-12-19 to 2022-12-22 for the BoJ YCC band shift); replays futures-price path; returns P&L path, drawdown, vol; used in stress-testing workflows.

**`basis_trade_monitor`** — takes a contract; uses `ctd_identifier`, `implied_repo_rate`, term repo rate (cross-domain); returns time series of (implied repo - actual term repo), 252d percentile, and explicit blow-out flag if outside historical norms; used in funding-stress monitoring; March 2020 reference case is the calibration anchor for "stressed" thresholds.

### Planned (Bucket 2)

**`pca_futures_curve`** — per-country bond-future-price-change panel; lookback 1-10Y configurable; returns level/slope/curvature factor loadings, factor time series, variance explained; used in attribution and scenario workflows. Particularly clean factor structure on TU/FV/TY/UXY/US/WN (six US contracts spanning 2Y to 30Y).

**`hmm_futures_regime`** — takes a contract or country complex, feature vector (price changes, basis, vol, positioning), N states, lookback; fits Gaussian HMM; returns state probability series, transition matrix, current state; used in regime classification workflows.

**`cointegration_test_futures`** — takes paired bond-future series (typically cross-country, e.g., TY-RX or TY-G), lag spec, test choice; returns p-value, equilibrium relationship, residual; used in cross-country RV strategy development. Bucket 2 reasoning per the OIS module.

**`half_life_ou_futures`** — takes a futures-spread series; OU fit; returns half-life and current deviation; used in RV sizing/timing.

**`garch_futures_vol`** — takes a futures-price series at chosen contract; GARCH fit; returns conditional vol forecast; used in vol-targeted sizing and the input to `quality_option_valuation`.

**`quality_option_valuation`** — takes a contract, full deliverable basket, calibrated short-rate model (typically Hull-White or G2++), and swaption vol surface (`06_swaptions_and_rates_vol.md`); simulates yield-curve paths; values the embedded CTD-switch / end-of-month / wildcard options; returns option value per contract. Most relevant near regime transitions when CTD switching is most likely.

**`multi_country_futures_pca`** — takes the full bond-future panel across countries; computes joint cross-section PCA; returns global rates factor loadings and per-country residuals. Calendar synchronization across CME / Eurex / ICE / OSE / TMX / ASX is the engineering challenge — ASX particularly tricky given the quoting-convention difference.

**`futures_basis_predictive_model`** — beyond descriptive `basis_trade_monitor`, fits a model relating basis stress to dealer-balance-sheet capacity, hedge-fund positioning, repo-funding, and volatility. Bucket 2 because predictive and model-dependent.

### Aspirational (data-blocked or further out)

**`cross_asset_basis_macro_indicator`** — would aggregate basis stress signals across UST, Bund, gilt, JGB into a single "global basis health" indicator. Cross-product, requires reliable per-country basis and term-repo data.

**`futures_implied_term_premium`** — would extract term-premium-style decomposition from a country's bond-future complex via an affine model. Liquidity advantage over cash bonds; complicated by CTD and quality-option dynamics.

**`positioning_flow_decomposition`** — beyond raw COT data, would map positioning changes to specific trade types (basis trade, curve trade, outright duration) using auxiliary data on repo borrowing, swap volumes, and futures-options skew. Data-intensive.

## 10. Open questions

These are things to validate at Brevan or with the advisory firm. Each tagged.

- *#parameter-default* — Default z-score lookback is 252d. Validate whether 60d/126d is more common for tactical futures signals around CTD-switch dates and roll windows.
- *#parameter-default* — DV01 calculation: do PMs default to "CTD DV01 / conversion factor" (analytical) or numerical bump? Probably both depending on context.
- *#parameter-default* — Curve trades on futures default to DV01-weighted; verify that's the practitioner standard at Brevan.
- *#convention* — Yield-solver conventions per country: USTs are semi-annual, German Bunds are annual, JGBs are semi-annual, gilts are semi-annual. Build a per-contract convention table; verify against Bloomberg `<contract> Comdty DLV` output or against Tuckman / Veronesi.
- *#convention* — ASX YM/XM cash-settlement basket calculation: which CGS bonds, what weighting, which time-of-day yield. The specifics matter for any simulation. RBA Bulletin September 2014 has the methodology; cross-check against current ASX rule book.
- *#workflow-validation* — How granular is per-contract basis monitoring on a typical macro desk? Daily snapshot of CTD net basis for the front month, or full-basket-implied-repo dashboard?
- *#workflow-validation* — Cross-country RV: how widely is DV01-FX-weighted RX-TY used as a primary trade vehicle vs as a hedge to a sovereign cash position?
- *#workflow-validation* — The roll: does Brevan run a systematic roll-cost analytic, or is it a discretionary call by the trader at roll time?
- *#workflow-validation* — Quality option / wildcard option valuation — is this run regularly by macro PMs or is it left to the basis-trade specialists? My guess is it's mostly specialists but PMs care during regime transitions.
- *#workflow-validation* — Positioning data (CFTC COT, etc.) — how is it actually used? As a contrarian indicator at extremes? As confirmation? As a flow-direction signal?
- *#scope* — ASX YM/XM cash-settlement is structurally different from all other contracts. Does this mean a separate sub-tool family, or do we abstract over it?
- *#scope* — JGB futures: SGX-listed JGB futures vs OSE-listed. Brevan likely uses both depending on session. Bloomberg `JB1 Comdty` is OSE; the SGX one is `JX1 Comdty`. Ingest both, or treat as unified instrument?
- *#scope* — Long Gilt 6%-to-4% notional change for December 2011 onwards is a known regime break. Treat pre-2012 history as pre-transition data? Bucket 2 models that span this should handle it.
- *#scope* — Pre-launch dates: FBTP (Long BTP) Sept 2009, FBTS (Short BTP) Oct 2010, UXY Jan 2016, WN Jan 2010. These contracts have shorter usable history than the 2005 playbook start date — flag in `first_live_date`.
- *#data* — Conversion factors are per-contract reference data, not currently in the playbook. Where should they live — in a separate reference store, or computed on-the-fly from cash-bond reference data?
- *#data* — Deliverable basket per contract month: same question. The basket is published by the exchange; we need a process to ingest it.
- *#data* — CFTC COT data and Eurex / ICE positioning equivalents — how do we ingest? CFTC publishes weekly; need to confirm Eurex / ICE / TMX positioning data availability.
- *#methodology* — `quality_option_valuation`: what's the practitioner standard short-rate model? Hull-White is common at sell-side; G2++ at some macro funds; full Heath-Jarrow-Morton at others. Verify Brevan house standard.
- *#methodology* — Ultra contracts (UXY, WN) have narrower deliverable baskets than classic counterparts (TY, US), which means less CTD-switch optionality and tighter tracking to the canonical tenor. PMs probably know which to prefer for which trade — formalize.

## 11. References

- *[CME contract specifications]* — CME Group. Treasury futures contract specifications pages (TU, FV, TY, UXY, US, WN); *The basics of U.S. Treasury futures*; *Understand Treasuries Contract Specifications*; *Delivery Basket for 10-Year Treasury futures* (2022 discussion paper).
- *[Eurex contract specifications]* — Eurex. Contract specifications for FGBS (Schatz), FGBM (Bobl), FGBL (Bund), FGBX (Buxl), FBTS (Short BTP), FBTM (Mid BTP), FBTP (Long BTP), FOAT (OAT), FBON (Bono); *Notified Bonds | Deliverable Bonds and Conversion Factors*; *Contract Specifications for Futures Contracts and Options at Eurex*.
- *[ICE Long Gilt]* — ICE. Long Gilt Future and Medium Gilt Future product specifications.
- *[JPX]* — Japan Exchange Group. JGB Futures Contract Specifications, Delivery, Deliverable bonds / Conversion Factors; *Introduction to Japanese Government Bond (JGB) Futures*.
- *[TMX CGB]* — Montréal Exchange / TMX Group. Ten-Year Government of Canada Bond Futures (CGB) specs; *CGB™ Ten-year Government of Canada Bond Futures Reference Manual*; *30-Year Government of Canada Bond Futures (LGB): A Primer for CGB Users*.
- *[ASX]* — ASX. *ASX 3 and 10 Year Treasury Bonds Futures and Options* fact sheet; *What's unique about Australian bond futures?*.
- *[CME Spreading Ultra 10]* — CME Group. *Spreading Ultra 10 Against Foreign Sovereign Bond Futures* — practitioner reference for cross-country DV01/FX-weighted bond-future spreads.
- *[OFR 2021]* — Office of Financial Research / Barth & Kahn. *Hedge Funds and the Treasury Cash-Futures Disconnect* (Working Paper 21-01, 2021). Canonical reference for the cash-futures basis trade and March 2020 unwind.
- *[Federal Reserve FEDS WP 2021-038]* — Kruttli, Monin, Petrasek, Watugala. *Hedge Fund Treasury Trading and Funding Fragility: Evidence from the COVID-19 Crisis* (FEDS 2021-038).
- *[NY Fed speech 2025]* — Federal Reserve Bank of New York speech, 9 May 2025, on Treasury market liquidity and funding conditions. Useful for current state of basis-trade positioning and the April 2025 contrast with March 2020.
- *[Dallas Fed July 2025]* — Federal Reserve Bank of Dallas. *How sensitive is the Treasury cash-futures basis trade to funding condition shifts?* (July 2025).
- *[BoE 2022 LDI]* — Bank of England working paper on the September-October 2022 gilt market intervention; intervention timeline and rationale.
- *[BoJ]* — Bank of Japan. *New Framework for Strengthening Monetary Easing* (September 2016, YCC introduction); 20 December 2022 monetary policy decision (YCC band widened); March 2024 monetary policy decision (YCC and NIRP exit).
- *[RBA Bulletin Sept 2014]* — Reserve Bank of Australia. *Trading in Treasury Bond Futures Contracts and Bonds in Australia* (September 2014). Useful reference on ASX bond futures cash settlement, EFP, and DV01 mechanics.
- *[Tuckman]* — Tuckman, B., & Serrat, A. (2022). *Fixed Income Securities: Tools for Today's Markets* (4th ed.). Wiley. Chapter 21 covers bond futures, CTD, conversion factors, basis, implied repo.
- *[Veronesi]* — Veronesi, P. (2010). *Fixed Income Securities: Valuation, Risk, and Risk Management*. Wiley.
- *[Actrix Financial Technology]* — *Bond Futures Explained: From DV01 to the Cheapest to Deliver* (practitioner blog, 2024). Useful walk-through of conversion-factor mechanics and DV01 for the Long Gilt.
- *[Litterman & Scheinkman 1991]* — Litterman, R., & Scheinkman, J. (1991). "Common Factors Affecting Bond Returns." *Journal of Fixed Income*, 1(1), 54–61.
- *[Hamilton 1989]* — Hamilton, J. D. (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle." *Econometrica*. Foundational regime-switching paper.
- *[Hamilton 1994]* — Hamilton, J. D. (1994). *Time Series Analysis*. Princeton University Press.
- *[Rabiner 1989]* — Rabiner, L. R. (1989). "A tutorial on hidden Markov models and selected applications in speech recognition." *Proceedings of the IEEE*, 77(2), 257–286.
- *[Bollerslev 1986]* — Bollerslev, T. (1986). "Generalized Autoregressive Conditional Heteroskedasticity." *Journal of Econometrics*, 31(3), 307–327.
- *[Hull-White]* — Hull, J., & White, A. (1990). "Pricing interest-rate-derivative securities." *Review of Financial Studies*, 3(4), 573–592. Practitioner-standard short-rate model for embedded-option valuation.
- *[Eurex BTP launch circulars]* — Eurex circulars announcing Long-Term Euro-BTP Futures (June 2009, effective September 2009) and Short-Term Euro-BTP Futures (September 2010, effective October 2010).
- *[Brookings 2025]* — Brookings Papers on Economic Activity, March 2025. Discussion of central-bank backstop proposals for the Treasury basis trade.
