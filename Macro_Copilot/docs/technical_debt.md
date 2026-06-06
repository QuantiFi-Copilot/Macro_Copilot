# MACRO COPILOT — TECHNICAL DEBT REGISTER
# Last updated: May 2026 (Phase 0 close)
# Status: All patch-level defects resolved. Items below are architectural
#         improvements, not bugs in the current code.
#
# Phase 0 close (PR 11) — three CRITICAL items resolved:
#   #1  Non-atomic delete+upsert in ingestion (Phase 0 PR 3)
#   #6  Persistent LangGraph checkpointer        (Phase 0 PR 5)
#   #20 Normalized data hash stability           (Phase 0 PR 2)
# The substrate (artifact store, lineage, methodology pinning,
# workspace persistence, working set, turn lifecycle) that PR 11
# ships closes Phase 0.  Remaining items are non-blocking for the
# Phase 1 backtest archetype.


## CRITICAL — Fix before production traffic

### 1. Non-atomic delete + upsert in ingestion — [RESOLVED, Phase 0 PR 3]
WHERE (was): ingest_parquet.py (lines 493, 529), database.py (helpers)
WHAT (was):  The overlap delete and market_data_daily upsert ran in
             separate transactions.  If the insert failed after the
             delete committed, the DB was left partially refreshed
             until a retry repaired it.
RESOLUTION:  Four database helpers now accept a ``Connectable`` (either
             an ``Engine`` for self-managed transactions or a
             ``Connection`` for caller-managed transactions):

               - ``database.upsert_market_data_daily``
               - ``database.update_load_audit_status``
               - ``ingest_parquet._delete_existing_playbook_scope``
               - ``ingest_parquet._delete_existing_playbook_window``

             ``database._txn`` is the shared dispatch context manager:
             if handed an Engine it opens ``engine.begin()`` (legacy
             behavior); if handed a Connection it yields it as-is
             (caller owns the txn boundary).

             ``ingest_parquet.run_ingestion_pipeline`` now wraps the
             critical DELETE → UPSERT → audit-flip section in a single
             ``with engine.begin() as conn:`` block, passing ``conn`` to
             every helper.  A failure between any two operations rolls
             back the whole transaction; the outer ``except`` handler
             then flips the audit row to FAILED on a SEPARATE
             transaction.

             ``instrument_master`` upsert remains OUTSIDE the critical
             section because it is idempotent on (vendor, vendor_ticker)
             and including it would extend the lock window without
             correctness benefit.

             Test coverage: ``tests/state/test_transactional_ingestion.py``
             pins the dispatch contract (Engine vs Connection paths),
             the composition contract (helpers do NOT open
             sub-transactions when given a Connection), and the
             rollback contract (an exception in the critical block
             causes ``__exit__`` to receive the exception, which in
             production SQLAlchemy issues ROLLBACK).

### 2. Rolling contract metadata (instrument_master SCD2)
WHERE: schema.sql (instrument_master), database.py (upsert_instrument_master)
WHAT: instrument_master is keyed by (vendor, vendor_ticker) and overwrites
      expiry_date, maturity_date, security_name on each upsert. For rolling
      contracts (43 futures tickers: TY1, FV1, SFR1-8, ER1-8, etc.),
      historical market_data_daily rows join to current contract metadata.
IMPACT: Time-series price data is correct. Only reference metadata is stale.
        Does NOT affect current sovereign bond tools. WILL affect futures
        tools when built.
FIX: Either (a) add an instrument_metadata_history table with effective_from
     dating, or (b) implement SCD2 on instrument_master itself with
     effective_from / effective_to columns.
EFFORT: Medium-High — schema migration + upsert logic + tool query changes.
WHEN: Before building any futures-specific analytics tools.

### 3. Incremental ingestion date-window validation
WHERE: ingest_parquet.py (sanity gate, line 468)
WHAT: The current sanity gate compares incoming instrument count against
      the previous load's instrument count. It does not verify that the
      incoming parquet covers the full requested_start_date to
      requested_end_date window per instrument.
RISK: A parquet with all tickers but missing recent dates could pass the
      gate and erase good overlap rows for those missing dates.
MITIGATED BY: Extractor-side 90% coverage gate catches most partial
      failures before they reach ingestion.
FIX: Add per-instrument date-window completeness check: for each instrument
     in the incoming parquet, verify that at least N% of expected trading
     days in the requested window have data before allowing the delete.
EFFORT: Medium — new query logic in ingest_parquet.py, needs a trading
        calendar or heuristic for expected business days.


## HIGH — Fix within first month

### 4. Concurrent ingestion run protection
WHERE: ingest_parquet.py
WHAT: No advisory lock, blob lease, or run coordination. Two workers
      processing the same GCS blob could delete each other's work.
MITIGATED BY: Current deployment is single-worker (docker-compose restart: no).
FIX: Add a Postgres advisory lock per playbook_name at the start of each
     blob's processing, or use GCS generation preconditions to claim blobs.
EFFORT: Low — ~20 lines of advisory lock code.
WHEN: Before any multi-worker or scheduled deployment.

### 5. Hybrid refresh model (daily + periodic deep refresh)
WHERE: incremental_extractor.py, historical_extractor.py
WHAT: Currently either full-history reload (historical) or 14-day rolling
      window (incremental). No middle ground.
FIX: Implement three tiers:
     - Daily: 14-day incremental with existing gates
     - Weekly: 60-90 day rebake to catch revisions
     - Monthly: full historical reload for integrity
EFFORT: Low — mostly scheduling config, the extractors already support
        configurable windows.

### 6. Persistent LangGraph checkpointer — [RESOLVED, Phase 0 PR 5]
WHERE (was): orchestrator/graph.py (MemorySaver)
WHAT (was):  Conversation state was in-memory only.  Lost on restart.
             Single hardcoded thread_id.
RESOLUTION:  Replaced ``MemorySaver`` with ``AsyncPostgresSaver``
             backed by a dedicated psycopg3 ``AsyncConnectionPool``.
             The pool's kwargs match LangGraph upstream defaults
             (``autocommit=True``, ``prepare_threshold=0``,
             ``row_factory=dict_row``, ``options=-c search_path=
             langgraph_checkpoint,public``) — pinned in
             ``api.dependencies.init_checkpointer_pool``.

             Thread ids are now stable per-(session, domain) when
             ``stateless=False`` (the new default after PR 5):
             ``{session_id}-{domain.value}`` (vs the legacy per-turn
             ``{session_id}-{turn_label}-{domain.value}`` retained
             for tests via ``stateless=True``).  Phase 0 PR 8
             added the explicit session-level ``copilot_state.sessions``
             row + ``copilot_state.turns`` lifecycle on top.

             Degraded operation: pool init failure logs an error
             and the API serves without durability rather than
             refusing to start; the WebSocket handler surfaces a
             warning to the client at handshake time.

             Test coverage: ``tests/state/test_postgres_checkpointer.py``
             (pool setup idempotence, thread isolation, multi-checkpoint
             history, state survives pool close+reopen),
             ``tests/state/test_session_restart.py`` (a real LangGraph
             counter resumes byte-identically after a simulated
             process restart).

### 7. Data freshness check in tools
WHERE: rates_agent/tools/yield_levels.py, curve_spread.py
WHAT: Both tools return the last available data point without flagging
      if it's stale. If Bloomberg extraction fails for 3 days, the tools
      happily return Friday's data on Wednesday without any warning.
FIX: Add an as_of_date check: if the latest trade_date is more than
     N business days before today, include a staleness warning in the
     tool output (e.g. "WARNING: data is 3 trading days old").
EFFORT: Low — ~10 lines per tool.

### 8. Secrets management
WHERE: database.py (hardcoded defaults), docker-compose.yml, secure_keys/
WHAT: DB credentials have hardcoded defaults (quantuser/myStrongPass),
      GCP key file may be in git history, .env mounted broadly.
MITIGATED BY: Local-only development environment, not deployed to cloud.
FIX: Rotate credentials, purge any committed secrets from git history,
     move to Docker secrets or a secret manager (Vault, AWS SM, GCP SM)
     before any shared/cloud deployment.
EFFORT: Low-Medium.
WHEN: Before deploying beyond local Docker.

### 9. Automated evaluation suite
WHERE: tests/
WHAT: Current tests are manual smoke scripts, not automated assertions.
      No CI/CD integration. No regression coverage.
FIX: Build three tiers:
     - Unit tests: tool math correctness (known inputs → known outputs)
     - Integration tests: ingestion pipeline with fixture parquets
     - LLM eval: prompt regression suite (the 10-prompt gauntlet with
       expected SQL-verified answers)
EFFORT: Medium — the gauntlet SQL already exists, needs pytest harness.
WHEN: ChatGPT's audit flagged this as "more urgent than you think."
      Should be treated as a launch requirement.

### 31. fomc_surprise_label primitive deferred — intraday OIS + statement_classification gap
WHERE: rates_agent/ois/tools/ (primitive does NOT exist; planned as
       primitive 6 of the 8-primitive easy-win batch).
       rates_agent/playbooks/central_bank_meetings.yml
       (docstring mentions a "Phase-3 fomc_surprise primitive" — that
       reference now points here.)
WHAT:  A categorical hawk/dove/neutral regime tag per FOMC meeting
       cannot be honestly shipped today.  Two independent metadata
       gaps each block the primitive:
       (1) NO PRE-CLASSIFIED PLAYBOOK FIELD.  central_bank_meetings.yml
           stores only the FACTUAL rate decision (`actual` policy rate,
           `prior` policy rate, identity-derived hike/hold/cut classifier
           in attributes).  It does NOT contain a hawk/dove statement-
           sentiment classification.  The playbook's docstring's
           "Phase-3 fomc_surprise primitive" wording was aspirational.
       (2) NO INTRADAY MARKET DATA.  macro_data.market_data_daily holds
           Bloomberg PX_LAST at daily close only — there is no
           market_data_intraday table and no minute-resolution feed.
           The desk-textbook methodology (Nakamura-Steinsson 2018 /
           Bauer-Swanson 2023) measures the bp-change in 2y OIS in a
           tight intraday window (typically 30 minutes) around the
           statement.  That window cannot be reproduced from daily
           closes.

       Shipping a daily-close-to-close proxy on 2y OIS would
       systematically conflate the policy-decision component with
       overnight macro flow and would violate P12 (Bloomberg Accuracy
       Boundary) + PR6 (refusal-when-metadata-absent) — the desk would
       have to caveat every output that the signal is materially
       different from the published methodology.
IMPACT: The MCP catalog has no hawk/dove FOMC tag.  Workflows that
        would condition on FOMC surprise regime (the event-study
        template's FOMC variant) must wait for this primitive or
        accept a coarser hike/hold/cut categorical from the existing
        playbook attributes.
FIX:   Two independent unblock paths, either of which suffices:
       (a) Ingest intraday 2y OIS data (Bloomberg intraday tick query
           on USOSFR2 Curncy + sibling tickers for ECB/BOE/BOJ
           central banks, new market_data_intraday table, ADR for the
           intraday substrate).  Then build the primitive as
           originally specified with the 30-minute window.
       (b) Add an LLM-classified statement_classification field to
           central_bank_meetings.yml (separate ingestion increment
           that reads the official statement text + classifies via
           a sentiment model).  Then YAML-lock the hawk/dove threshold
           and build the primitive with the playbook-field source.
       Path (a) is desk-canonical; path (b) is cheaper and faster
       but introduces a new LLM-dependency in the ingestion pipeline.
WHEN:  Out of scope for the 8-primitive easy-win batch.  Revisit
       when EITHER intraday OIS ingestion lands OR a statement-
       classification ingest increment is prioritised.
EFFORT: Large — neither unblock path is a single-PR change.  Both
       require new ingestion plumbing AND, only after that lands,
       the primitive build itself (four files + test triplet + parity
       fixture).

### 32. linker_carry_with_seasonals primitive deferred — three independent metadata gaps
WHERE: rates_agent/inflation_indexed_bonds/tools/ (primitive does
       NOT exist; planned as primitive 7 of the 8-primitive easy-win
       batch).
       rates_agent/playbooks/inflation_indexed_bonds.yml
       rates_agent/playbooks/inflation_references.yml
WHAT:  A carry primitive that honestly decomposes linker P&L over a
       horizon (coupon accrual + index-ratio appreciation +
       price-pull-to-par) cannot be shipped today.  THREE independent
       metadata gaps each, alone, block the as-written brief:
       (1) PER-BOND CARRY FIELDS NOT INGESTED.
           inflation_indexed_bonds.yml carries only YLD_YTM_MID +
           MATURITY + SECURITY_DES + inflation_index_family.  No
           CUR_CPN (coupon), no PX_DIRTY_MID (dirty price), no
           DAY_CNT_DES (day count), no IDX_RATIO (current index
           ratio), no INFLATION_LAG (per-bond indexation lag — TD #25
           SECTION 3 verified it IS exposed on the bond as the
           Bloomberg reference field INFLATION_LAG, but it has NOT
           been added to the playbook).  The non-linker
           sovereign_cash_bonds.yml playbook carries CPN + PX_DIRTY_MID
           + ISSUE_DT richly, but its universe is nominal bonds only —
           the linker tickers are not duplicated there.  Without these
           fields the three carry components each require proxies
           (duration ≈ tenor, coupon ≈ current yield, dirty price ≈ par,
           index ratio ≈ ratio-of-monthly-prints) — exactly the
           opinionated-proxy pattern that kept TD #27's sovereign
           carry+roll out of the build.
       (2) DAILY-INDEX INTERPOLATION RULE ABSENT (TD #25).
           The index_ratio appreciation component needs the daily
           reference index between two monthly CPI prints.  TD #25
           explicitly defers the per-market interpolation rule
           ("never guessed").  Any primitive that "computes a daily
           reference index, projects linker cash flows, or prices an
           inflation-linked bond off the reference indices" is
           blocked at TD #25's boundary — this primitive is exactly
           that case.
       (3) SEASONAL FACTORS DELIBERATELY ABSENT.
           inflation_references.yml §1 documents "SEASONAL FACTORS
           [are] DEFERRED to a non-Bloomberg follow-up; they cannot
           be sourced uniformly here" (the statistical-agency probe
           found no Bloomberg surface for them outside US SA-CPI).
           The brief's "with seasonals" wording cannot be honored
           without these factors.

       Shipping a degraded-proxy version would violate P12 (Bloomberg
       Accuracy Boundary) + PR6 (refusal-when-metadata-absent) — the
       carry decomposition would be three stacked guesses, none of
       which the desk could defend against a Bloomberg PORT screen.
       The brief's §5 bail-out explicitly named this case ("If you
       find the inflation-references data is not at the granularity
       the carry methodology requires (e.g. you'd need daily index
       interpolation that exceeds TD #25's stated boundary), STOP and
       ask me (AC8).  Do NOT proxy.")  User confirmed deferral.
IMPACT: The MCP catalog has no linker-carry primitive.  Linker RV
        analysis must compose the rougher tools that exist (real
        yield levels, breakeven primitives) without a horizon
        decomposition.  Transitively blocks TD #33 (primitive 8 —
        carry-adjusted breakeven, which composes this primitive under
        strict PR9).
FIX:   Single combined ingestion + standards workstream that lands
       ALL THREE substrate pieces, after which this primitive can be
       built as a single small four-file PR:
       (a) Ingestion increment on inflation_indexed_bonds.yml:
           add CUR_CPN / PX_DIRTY_MID / DAY_CNT_DES / IDX_RATIO /
           INFLATION_LAG to target_metrics + reference_metrics; re-
           verify against Bloomberg per the playbook's
           verification-probe pattern; backfill historical series.
       (b) Resolve TD #25 — source the per-market interpolation rule
           from each debt office's index-linked-bond prospectus
           (US Treasury, UK DMO, AFT, BoC, JMOF) and encode one
           VERIFIED `interpolation` attribute per bond row.
       (c) Seasonality ingest: source statistical-agency seasonal
           factors (FRB / BLS / Eurostat / ONS) via a non-Bloomberg
           ingest path — a new playbook or extension to
           inflation_references.yml.
WHEN:  Out of scope for the 8-primitive easy-win batch.  Revisit
       when the combined substrate workstream above is prioritised.
       Per user direction, "do it as a separate ingestion/substrate
       workstream first, then return to primitive 7."
EFFORT: Very large — three independent ingestion / standards
       extensions, each with its own verification + backfill;
       primitive itself is small once substrate lands.

### 33. carry_adjusted_breakeven primitive deferred — PR9 composition cascade from TD #32
WHERE: rates_agent/inflation_indexed_bonds/tools/ (primitive does
       NOT exist; planned as primitive 8 of the 8-primitive easy-win
       batch).
WHAT:  carry_adjusted_breakeven is a strict-PR9-composition primitive
       that consumes the OUTPUT of linker_carry_with_seasonals
       (primitive 7) plus a nominal-bond carry equivalent.  It cannot
       be built honestly until its underlying primitive exists:
         - the linker leg requires calculate_linker_carry_with_
           seasonals() — TRANSITIVELY blocked by TD #32 above.
         - the nominal leg requires per-bond CUR_CPN / DAY_CNT_DES /
           PX_DIRTY_MID / MOD_DUR_MID on the nominal sovereign-bond
           universe.  TD #27 already documents this as the open
           blocker for the sibling sovereign carry+roll primitive.

       Building primitive 8 against degraded proxies on either leg
       would compound the proxy error — and PR9 explicitly forbids a
       composition primitive that masks its dependencies' weaknesses.
IMPACT: The MCP catalog has no carry-adjusted breakeven primitive.
        Breakeven RV screens see only the raw nominal − real yield
        spread without horizon-carry adjustment.
FIX:   Compound of TD #32 (resolve all three linker gaps) AND
       TD #27 (extend nominal sovereign bonds with carry fields).
       After both land, this primitive becomes a small four-file PR
       that demonstrates strict PR9 composition (explicit `config=`
       pass-through of both inner primitives' configurations + the
       provenance echo PR10 mandates).
WHEN:  After TD #32 and TD #27 both close.  Per user direction, the
       8-primitive easy-win batch closes at 5/8 — this primitive
       returns only after the substrate workstream lands.
EFFORT: Small for the primitive itself once both substrate gaps are
       closed; the underlying ingestion work is captured under
       TD #32 + TD #27.
SEE ALSO: TD #34 (FWCV-as-Bloomberg-Terminal-screen) is a separate but
       reinforcing block on any carry-flavoured primitive that would
       INGEST forwards/carry directly from Bloomberg rather than
       composing them from substrate.


### 34. FWCV forward/carry — not a Bloomberg data-API field; INGEST primitives blocked
WHERE: rates_agent/sovereign_bonds/tools/ + rates_agent/ois/tools/
       (three planned INGEST primitives do NOT exist; tracked in
       tmp/primitive_expansion/phase3.md Step 3).
WHAT:  Phase 3 Step 3 enumerates three INGEST primitives that must
       read Bloomberg FWCV-derived forward rates and carry/roll fields
       per sovereign + OIS curve:
         - sovereign_forward_rate    (rates_agent/sovereign_bonds/)
         - sovereign_carry_and_rolldown (rates_agent/sovereign_bonds/)
         - ois_forward_carry         (rates_agent/ois/)

       Per P12 these MUST be INGEST primitives — recomputing the FWCV
       output from substrate yield pillars + a constructed financing
       curve would be a proxy under the desk-recognised name, banned
       outright. The Phase 3 plan therefore depends on FWCV values
       being reachable through the Bloomberg data API (bdh / bdp /
       bds).

       A4 verification on the sovereign benchmark playbook
       (sovereign_bonds.yml v1.3, header note quoted verbatim):

         "FWCV forward/carry is likewise not a Bloomberg data-API
         field — a separate access-pattern investigation, not part of
         this playbook."

       That probe established that FWCV is a Bloomberg Terminal screen
       function (an interactive curve workspace), not a published bdh
       / bdp / bds field. The standard ingestion pipeline
       (utils/historical_extractor.py + utils/incremental_extractor.py
       → parquet → GCS → ingestion/ingest_parquet.py) cannot reach it
       under the current adapter shape.
IMPACT: Three Phase 3 INGEST primitives are blocked at the data-
       access layer, not at the schema, code, or methodology layer:
         - sovereign_forward_rate — N-period forward rate at any
           tenor anchor, source-of-record bootstrapped curve.
         - sovereign_carry_and_rolldown — horizon carry + roll-down
           per benchmark tenor, source-of-record Bloomberg FWCV.
         - ois_forward_carry — analogue on the OIS leg per RFR family.

       Downstream consequences:
         - Phase 2's D-tenor-mesh (denser benchmark pillars) does NOT
           unlock carry/roll on its own (Codex correction documented in
           phase2.md §Step 6) — without FWCV the substrate has no
           source-of-record path to ingest the carry values, so the
           denser pillars improve only PCA precision + cross-sectional
           density, not carry-trade analytics.
         - TD #33's carry_adjusted_breakeven primitive (deferred
           PR9-composition) inherits this block via its nominal leg:
           the nominal-bond carry it consumes would itself be FWCV-
           derived if INGEST were available.
         - Phase 4's realistic-financing backtest (Step 5) is
           independently blocked by D-repo (TD #29 + TD #30); FWCV is
           a parallel block on the carry-side honesty story, not the
           financing-side.
FIX:   Investigation, not implementation. Four candidate unblock
       paths to evaluate before any new primitive PR is opened:

         (a) Bloomberg BCurveStrip API — the Bloomberg-provided curve-
             strip endpoint (BLP-side equivalent of the FWCV screen).
             If reachable from the bbg-api wheel currently used by
             ingestion/, this is the lowest-friction path: a new
             extractor mode + ingester route + carry/forward
             time-series table. Per P7 / P12 the read still happens
             only at L1 (no vendor-shape leakage to L2+).

         (b) Third-party forwards feed — ICAP / Tullett Prebon / ICE
             curve services that publish bootstrapped forward + carry
             curves daily. License + adapter cost; introduces a
             second L1 source for the same logical fact (per P7,
             allowed iff the adapter contract is held identical).

         (c) Terminal-export pipeline — manual / scheduled Bloomberg
             Terminal export of the FWCV screen contents to a CSV or
             parquet artifact that the existing ingester can pick up
             from GCS. Highest operational fragility (a human-in-the-
             loop step on the Bloomberg PC each cycle); only
             acceptable as a stop-gap with a documented retire-by
             date.

         (d) Computed-with-disclosure path (NON-DEFAULT) — recompute
             forwards / carry from substrate yields + a chosen
             financing convention, expose ONLY behind an explicit
             `analyst_override` knob per the P12 narrow allowance,
             with full P5 disclosure on the methodology card. This is
             explicitly NOT a substitute for INGEST under the
             desk-recognised primitive name; it would ship as a
             differently-named override primitive (e.g.
             `compute_forward_from_pillars_override`) so a PM cannot
             consume it on the wrong assumption.

       The expected output of the investigation is an ADR in
       docs_revamped/05_decisions/ that picks one of (a)/(b)/(c) as
       the production path (with (d) reserved for the narrow
       analyst-override case), then unblocks the three Phase 3
       INGEST primitives.
WHEN:  Before any Phase 3 Step 3 INGEST primitive PR is opened.
       Until then, sovereign_forward_rate, sovereign_carry_and_rolldown
       and ois_forward_carry stay deferred (and so does
       TD #33's carry_adjusted_breakeven on its nominal leg).
EFFORT: Investigation: Small (~1 day to enumerate the bbg-api surface
       and probe BCurveStrip; ~1 day to evaluate vendor alternatives).
       Production path: Medium-High (any of (a)/(b)/(c) is a new
       extractor mode + new ingester route + new SCD2-or-time-series
       schema for the carry/forward values + a small INGEST primitive
       per consumer).


### 35. attribution_decomposition V1 workflow template deferred — workflow-bridge gaps
WHERE: shared/artifacts/adapters/from_time_series.py +
       shared/workflow/executor.py (the workflow-bridge dispatch
       layer that converts primitive output dicts into closed-family
       artifact types for inter-node hand-off).
WHAT:  The Round 3 Stage 2 A5 work item (per
       tmp/primitive_expansion/phase2.md Step 4) called for a
       canonical attribution_decomposition workflow template that
       composes three named building blocks as DAG nodes:

         pca_yield_curve  +  yield_change_attribution_pca  +  series_arithmetic

       That literal composition is NOT satisfiable in the current
       substrate.  The workflow bridge dispatches only TWO primitive
       output shapes (see
       shared/artifacts/adapters/from_time_series.py:357 — the
       ``isinstance(ts_obj, TimeSeries)`` gate, and the matching
       Panel adapter at ``tool_output_to_artifact_panel``):

         (1) Single canonical ``TimeSeries`` field → ``Series``
             artifact via ``tool_output_to_artifact_series``.
         (2) Single ``Panel`` field → ``Panel`` artifact via
             ``tool_output_to_artifact_panel`` (with the primitive's
             ``PrimitiveSpec.output_artifact_type = "Panel"``).

       The A5 building blocks emit shapes outside that pair:

         - ``pca_yield_curve.time_series_factors`` is
           ``List[TimeSeries]`` (one per principal component).  The
           ``TimeSeries`` bridge rejects it
           (``isinstance(ts_obj, TimeSeries)`` is False for a list).
           No Panel re-shaping exists either — the components are
           independent series, not a single multi-column panel.  The
           natural closed-family artifact for this would be
           ``SeriesSet`` (which already exists in
           ``ARTIFACT_TYPE_NAMES``), but no
           ``tool_output_to_artifact_series_set`` adapter is wired
           into the executor.

         - ``yield_change_attribution_pca.current_metrics`` is a
           snapshot Pydantic model (per-PC contribution_bps,
           residual_bps, loadings provenance, etc.).  There is no
           canonical ``TimeSeries`` field — the snapshot dict has
           no bridge-supported shape at all today.  The natural
           closed-family artifact would be ``ScalarMetric`` (or a
           new ``SnapshotResult`` artifact type — closed-family
           extension per P8).

       Consequence: a workflow template cannot legally include
       either primitive as a DAG node.  Both can still be invoked
       directly via the per-primitive MCP tools
       (``calculate_pca_yield_curve_tool``,
       ``calculate_yield_change_attribution_pca_tool``) — the gap
       is workflow-level chaining only.

       A V1 thin substitute template (cross-benchmark subtraction:
       target_yield - benchmark_yield = residual) was prototyped on
       branch round3-stage2-a5-attribution-decomposition (closed PR
       #197); shipping it under the canonical
       ``attribution_decomposition`` archetype was rejected on
       Codex code review as a P1 / AC7 / AC8 violation — substituting
       a nearby concept under the requested concept's name silently
       teaches the LLM router that the substitute IS the canonical
       attribution shape, and "decomposition sums to the input
       change within tolerance" (the work order's A5 acceptance
       criterion) holds only trivially for subtraction.  The honest
       path is to defer the template entirely until the substrate
       can carry the requested composition.
IMPACT: The ``attribution_decomposition`` archetype slot remains
       reserved-but-empty in ``WORKFLOW_ARCHETYPES`` until this
       block clears.  No regression vs the pre-Round-3 state — the
       slot was reserved-and-empty before too.  Downstream A5
       deliverables that depended on the template (Library card,
       per-template MCP wrapper, workflow gauntlet case) are also
       deferred.  The two affected primitives remain individually
       routable via the per-domain MCP servers.
FIX:   Two substrate extensions, either of which would partially
       unblock A5:

         (a) ``tool_output_to_artifact_series_set`` adapter +
             ``output_artifact_type = "SeriesSet"`` support on
             ``PrimitiveSpec``.  Lifts ``pca_yield_curve``'s
             ``time_series_factors`` into a closed-family
             ``SeriesSet`` artifact with per-PC member names.
             Downstream ``select_from_series_set`` then picks one PC
             at a time; ``series_arithmetic`` operates on the
             selected Series.  This unblocks "factor time series with
             arithmetic transform" attribution shapes but does NOT
             unblock the snapshot-decomposition shape.

         (b) Snapshot-shape adapter (likely a new ``SnapshotResult``
             ARTIFACT_TYPE per P8 closed-family extension) + matching
             ``ScalarMetric``-or-similar artifact + an operator that
             can consume snapshot decompositions (e.g. a new
             ``compose_attribution`` operator that takes a snapshot's
             component_contributions + a residual_bps scalar and
             produces a downstream artifact).  Unblocks the canonical
             yield_change_attribution_pca snapshot composition.

       Either (a) or (b) is a non-trivial substrate change.  (a) is
       smaller; (b) needs an ADR per P8.  A5's literal spec needs
       BOTH (a) AND (b) to be fully satisfied.  The canonical V1
       template after both extensions land would be a 5–7-node DAG
       composing ``pca_yield_curve`` (via SeriesSet bridge) →
       ``yield_change_attribution_pca`` (via Snapshot bridge) →
       ``series_arithmetic`` (residual reconstruction +
       sum-back-invariant check).

       The work order's "decomposition sums to the input change
       within tolerance" acceptance criterion can then be verified
       honestly: total_change_bps == sum(per-PC contribution_bps) +
       residual_bps within ``yield_change_attribution_pca``'s
       declared tolerance.

       Until (a) and (b) both land, the attribution_decomposition
       archetype stays empty.  ``yield_change_attribution_pca``
       remains fully usable as a standalone MCP tool.
WHEN:  Before any future ``attribution_decomposition`` template
       PR is opened.  Order: (a) first (smaller, no closed-family
       extension); then ADR for the new artifact type; then (b);
       then the canonical V1 template.
EFFORT: (a) Medium (~3-5 days: new adapter in
       shared/artifacts/adapters/, executor dispatch update, bridge
       tests, primitive-spec wire-up on pca_yield_curve, end-to-end
       template smoke).
       (b) Medium-High (~1 week: ADR for new closed-family
       artifact type, bridge adapter, executor dispatch, new operator
       to consume snapshot, primitive-spec wire-up on
       yield_change_attribution_pca, tests).
       Canonical V1 template after both: Small (~1 day).
SEE ALSO: The Stage 1 manifest-backfill PR (TD #34 sibling, merged
       in #194) already lists the four event-primitive sub-agents'
       manifests including the spot where the future
       attribution_decomposition_workflow MCP tool wrapper will
       land.


## MEDIUM — Fix within first quarter

### 10. Message history accumulation in orchestrator
WHERE: orchestrator/graph.py
WHAT: Message history grows unboundedly across turns. Old tool outputs
      stay in context. No trimming or summarization.
FIX: Add a max-message window or summarization step that compresses
     older messages before they push out the system prompt.
EFFORT: Low-Medium.

### 11. Duplicate (curve_family, tenor) in bond_futures playbook
WHERE: bond_futures.yml (TY1/UXY1 both UST_FUT 10Y, US1/WN1 both UST_FUT 30Y)
WHAT: Tools query by curve_family + tenor. Two instruments sharing the
      same pair would produce nondeterministic results via drop_duplicates.
IMPACT: Does not affect current sovereign bond tools (different curve
        families). Will affect futures tools.
FIX: Use contract_code or bucket_label as disambiguator in futures tool
     queries, or assign distinct identifiers (e.g. tenor: "10Y_ULTRA").
WHEN: Before building futures tools.

### 12. Tool lookup uniqueness not enforced in schema
WHERE: schema.sql, curve_spread.py, yield_levels.py
WHAT: Tools assume (curve_family, tenor) is unique per instrument_type,
      but the schema has no constraint enforcing this.
FIX: Add a partial unique index or compound constraint, or change tool
     queries to include instrument_type in the WHERE clause.
EFFORT: Low.

### 13. Cross-market spread tool
WHERE: rates_agent/tools/ (does not exist yet)
WHAT: PMs ask "BTP vs Bund 10Y" and the LLM manually subtracts two
      yield_levels calls. This works but violates the "deterministic
      numbers only" design principle — the LLM is doing arithmetic.
FIX: Build a dedicated cross-market spread tool that computes the spread
     deterministically in Python.
EFFORT: Low — similar structure to curve_spread.py.

### 14. MCP error sanitization
WHERE: rates_agent/mcp_server.py
WHAT: Raw exception text (including potential DB connection strings) is
      returned to the LLM in error responses.
FIX: Catch exceptions in each tool function and return sanitized error
     messages (e.g. "Database query failed" instead of the full traceback).
EFFORT: Low.

### 15. Pipeline observability (logging, alerting, metrics)
WHERE: All scripts
WHAT: All output is print statements. No structured logging, no metrics
      emission, no alerting on failure or staleness.
FIX: Replace print with structured logging (Python logging module or
     structlog). Add Prefect task failure notifications. Add a daily
     freshness check that alerts if any playbook's latest trade_date
     is more than N days old.
EFFORT: Medium.

### 16. Streaming LLM responses
WHERE: orchestrator/graph.py
WHAT: The full response appears at once after all tool calls complete.
      For the 6-tool global briefing, this can take several seconds.
FIX: Enable LangGraph streaming to show partial responses as tool calls
     complete.
EFFORT: Low-Medium.
WHEN: Safe to defer until frontend exists.


## LOW — Nice to have

### 17. Materialized view for v_market_data_daily_enriched
WHERE: schema.sql
WHAT: The enriched view is a simple JOIN. At high data volumes it could
      become slow.
FIX: Convert to a materialized view with a refresh schedule, or add
     covering indexes. Benchmark before changing — may not be needed
     for current data volumes.
WHEN: When query latency becomes measurable.

### 18. Forward-fill semantics across market holidays
WHERE: yield_levels.py (line 175), curve_spread.py
WHAT: ffill(limit=5) bridges gaps across holidays. Weekly/monthly changes
      use position-based indexing (iloc[-6], iloc[-22]) which is
      approximately but not exactly "5/21 trading days ago."
FIX: Use date-aware lookback (find the nearest trading date to
     today - 5 business days) instead of position-based.
EFFORT: Low.

### 19. Coverage metadata stamped into parquet
WHERE: historical_extractor.py, incremental_extractor.py
WHAT: The coverage ratio (extracted/expected tickers) is computed but not
      saved into the parquet. Ingestion can't independently verify
      extractor-side coverage decisions.
FIX: Add columns like `extraction_coverage_ratio` and
     `extraction_expected_ticker_count` to the parquet output.
EFFORT: Low.

### 20. Normalized data hash stability — [RESOLVED, Phase 0 PR 2]
WHERE (was): ingest_parquet.py (_compute_normalized_data_hash)
              shared/artifacts/lineage.py (_canonical_json + _compute_step_hash)
WHAT (was):  The dedup hash was computed from CSV serialization of the
             DataFrame; float formatting could vary across Python/Pandas
             versions.  The lineage step hash used json.dumps(default=str),
             which silently called str() on NumPy scalars / Pandas
             Timestamps with version-dependent output.
RESOLUTION:  Both hash sites now use an explicit canonicalization pass
             that converts inputs to a strict allowlist (None / bool /
             int / float / str / list / tuple / dict / date / datetime /
             numpy scalar via .item() / .isoformat()-capable) and
             rejects everything else with a clear TypeError.  NaN /
             Infinity are rejected explicitly.

             Ingestion: `ingestion/hashing.py` (extracted from
             `ingest_parquet.py`) hashes a pure-Python `{columns, rows}`
             JSON of the normalized DataFrame; no CSV, no NumPy in the
             serialization path.

             Lineage: `shared/artifacts/lineage._canonical_json` uses
             `_canonicalize_for_hash` upfront and `allow_nan=False` on
             the JSON encoder.

             Pinned cross-version test vectors live in
             `tests/state/test_hash_stability.py`, gated by a Python
             3.11 + 3.12 CI matrix.  Any drift between Python versions
             flips the pinned-hash assertions on the affected matrix
             leg.

Now let me address each concern that's still being raised:
"Per-ticker failures don't set any_failures" (ChatGPT + Codex)
This is the threshold-based partial success policy. All three AIs now acknowledge it's deliberate. ChatGPT says "that means the claim 'every failure path now sets the flag' is false." Fair point on the wording — I should not have said "every failure path." The accurate statement is: "every run-level failure path now sets the flag; per-ticker failures are governed by the 90% coverage threshold." The code is correct for this policy.
"Non-atomic delete/upsert" (all three)
Real architectural limitation. Documented as TECHNICAL_DEBT item #1. Requires refactoring database.py function signatures. Not patchable.
"Incremental window validation" (ChatGPT + Codex)
Real v2 enhancement. Documented as TECHNICAL_DEBT item #3. Requires new query infrastructure.
"Field-level completeness" (Codex)
Codex raises a genuinely important scenario I should spell out: if a futures ticker extracts with PX_LAST but Bloomberg fails to return OPEN_INT, the historical reload deletes ALL old rows for that instrument (including old OPEN_INT data) and only re-inserts PX_LAST. The old OPEN_INT history is gone. This doesn't affect your current sovereign bond tools (they only use YLD_YTM_MID), but it WILL matter for futures tools. Worth adding to TECHNICAL_DEBT.
"count_instruments_in_load will be slow at scale" (Gemini)
Gemini caught something genuinely new. The query filters by load_id only, with no date bounds. Since market_data_daily is a TimescaleDB hypertable partitioned by trade_date, Postgres must scan all time-chunks to find instruments for that load. Fine now, will be slow at 500M+ rows. Worth noting for v2.
"80% gate blocks intentional universe reductions" (ChatGPT + Codex)
Valid operational concern. If you intentionally remove 5 tickers from a playbook, the next run will be blocked until you either lower the threshold or do a manual first load. This is the conservative direction — better to block and investigate than to silently delete data for removed tickers.
"sys.exit(1) in finally makes extractors non-importable" (Codex)
True — if you ever try to from historical_extractor import run_autonomous_extraction in another script, a failure would kill the caller's process. Fine today since they're CLI entry points. If you ever need them as library functions, move the exit logic to the __main__ block.
One cosmetic issue I spotted: ingest_parquet.py has duplicate step numbering — lines 496 and 527 are both labelled "step 6." Harmless but sloppy.

### 21. FIX GCP BUCKET ISSUE!!!!!!! 



### 22. Swap Spread - IN OIS TOOLS 
this should maybe not live under the OIS domain namespace, because it is cross-domain - architectural decision to be made

### 23. Operator (summarize_series)
Might not be standard!! 
Probably just a temporary workaround: It is basically a temporary bridge because the artifact layer is still missing the right scalar output type.

UPDATE (2026-06): the SINGLE-scalar half is RESOLVED — ``summarize_series``
now emits a real ``ScalarMetric`` (the Series→ScalarMetric migration; the
sentinel-date 1-row Series hack is gone).  The MULTI-scalar half (the
``dispersion`` knob smuggling a 2nd number into lineage; "mean and std"
returns only the mean) is the still-open gap — see TD #36.


### 24. Phase 1 deferred 1B desk-critical tools (data-infrastructure-gated)

The original Phase 1 Week 7-8 plan called for four "desk-critical 1B
tools" alongside the backtest archetype.  PR 21's stress-test against
the "no metadata proxies" design principle blocked three of them on
data infrastructure we do not currently have.  None are hard
architectural blockers — they're all enabled by specific data
ingestion work.  Logged here so future PRs can see the exact
prerequisites.

**`per_meeting_pricing` (OIS)** — DEFERRED to Phase 2

  - **What it would do**: given a central-bank meeting date, decompose
    the OIS curve to extract the cuts/hikes priced for that specific
    meeting.
  - **Why deferred**: per-meeting decomposition requires WIRP-style
    data (the market's actual implied path step-function), not
    smooth-curve interpolation.  An earlier implementation that
    linearly interpolated par OIS rates to derive per-meeting moves
    drifted visibly from Bloomberg WIRP and was REMOVED (see
    ``rates_agent/ois/mcp_server.py`` docstring lines 18-23).
    Rebuilding without WIRP data would commit the same sin.
  - **Data prerequisite**: Bloomberg WIRP feed (or equivalent
    market-implied-path data) ingested as a daily snapshot.
  - **Effort**: medium — adapter to WIRP, output schema, 3-test pattern.

**`policy_path_since_event` (OIS)** — DEFERRED to Phase 2

  - **What it would do**: "How has the implied policy path moved
    since the SVB event?" — show the change in cumulative implied
    cuts/hikes between two dates.
  - **Why deferred**: two viable framings.
    (a) Meeting-decomposition framing — same WIRP blocker as above.
    (b) Generic-OIS-metric-change framing — ``(OIS_rate_today −
        OIS_rate_at_event_date)`` at some tenor.  But this is a thin
        wrapper over ``get_ois_rate_level`` + arithmetic that the LLM
        can compose; fails the "defensibly unique" stress test.
  - **Data prerequisite**: same as ``per_meeting_pricing`` for (a).
  - **Effort**: dependent on framing — (a) is medium; (b) shouldn't be
    a separate primitive.

**`carry_and_roll` (sovereign)** — DEFERRED to Phase 2

  - **What it would do**: compute per-bond carry + roll-down P&L over
    a holding horizon, for RV screens.
  - **Why deferred**: the carry+roll formula requires per-bond
    modified duration, coupon, day-count, and accrued interest.  Our
    sovereign-bonds playbook ingests ONLY yield (``YLD_YTM_MID``) +
    ``maturity_date`` + ``security_name``.  Computing carry+roll
    without the rest would force proxies — duration ≈ tenor (20-50%
    error for non-zero-coupon bonds), coupon ≈ current yield (par-bond
    assumption, off by 50-200bp for seasoned bonds), etc.  Each proxy
    violates the "no opinionated proxies for missing metadata"
    principle the same way the dropped per_meeting_pricing did.
  - **Data prerequisite**: Bloomberg ``MOD_DUR_MID`` + ``CUR_CPN`` +
    ``DAY_CNT_DES`` + ``PX_DIRTY`` per instrument added to the
    sovereign benchmarks playbook + ingestion run.
  - **Effort**: medium for the analytics; medium for the ingestion
    extension (adds ~5 fields × ~90 instruments × ~5000 days = ~2M new
    rows in market_data_daily, plus a small instrument-master
    extension for the static fields).

**`asset_swap_spread` (sovereign side)** — ALREADY EXISTS in OIS folder
The cross-domain ``swap_spread`` primitive at
``rates_agent/ois/tools/swap_spread/`` already computes the sovereign-
vs-OIS ASW using ``fetch_cross_domain_pair``.  Lives in OIS folder
per the "owner of the cross-domain concept" convention.  PR 21 adds
an explicit par-par-approximation disclosure to its config.yaml so
the workspace methodology card surfaces the true-ASW gap.

### 25. Inflation-linker daily-index interpolation convention not sourced

WHERE: rates_agent/playbooks/inflation_references.yml (work order B3),
       inflation_indexed_bonds.yml, inflation_swaps.yml — the per-row
       `interpolation` attribute.
WHAT: An inflation-linked bond settles against a DAILY reference index
      interpolated from monthly CPI prints. Two facts govern that daily
      reference index: (a) the indexation LAG, and (b) the INTERPOLATION
      RULE that maps the two bracketing monthly prints onto a given
      settlement date. The lag (a) IS verified — Bloomberg exposes it as
      the reference field INFLATION_LAG on the linker bond, and B3 encodes
      it per row. The interpolation rule (b) could NOT be located on
      Bloomberg: it is exposed as a reference field on neither the CPI
      index ticker nor the linker bond, and the B3 verification script's
      terminal run found no mnemonic carrying it.
IMPACT: index_lag alone is enough to ship inflation_references in B3 (index
      levels + the verified lag). It is NOT enough to compute an exact
      daily reference index for an arbitrary settlement date — that needs
      the interpolation rule. Markets do not share one rule: US TIPS,
      OATi/OAT€i and new-style UK gilts use the canonical day-count linear
      interpolation between the two monthly prints; old-style UK RPI
      linkers use an 8-month lag with NO interpolation (the bare monthly
      index). Hard-coding a single guessed rule across markets would
      corrupt any daily-reference-index or cash-flow-projection primitive
      built on top — the same data-corruption risk that kept index_lag
      out of the playbook until it was verified.
FIX: Source the per-market interpolation rule from an authoritative
      non-Bloomberg reference (each debt office's index-linked-bond
      prospectus / technical specification — US Treasury, UK DMO, Agence
      France Trésor, Bank of Canada, Japan MOF) and encode one VERIFIED
      `interpolation` attribute per playbook row, exactly the way index_lag
      is encoded from INFLATION_LAG. Until then the attribute is
      deliberately absent — never guessed.
EFFORT: Low-Medium — no schema or code change; per-market manual
      verification (~6 markets) plus a one-line attribute per playbook row.
WHEN: Before any primitive that computes a daily reference index, projects
      linker cash flows, or prices an inflation-linked bond off the
      reference indices. NOT needed for B3's index-level ingestion, nor for
      primitives that consume the monthly index directly.

### 26. Primitive/fetch-layer outlier filtering for vendor bad prints

WHERE: shared analytics fetchers and rates primitives that consume
       `macro_data.v_market_data_daily_enriched` / `market_data_daily`
       directly (for example sovereign yield PCA, z-scores, curve spreads,
       cross-market spreads, and future bid/ask-spread primitives).
WHAT: The ingestion layer stores source-of-record Bloomberg values as raw
      observations. That is the correct lineage behavior, but primitives
      must not blindly compute over impossible vendor sentinels or bad
      prints. A4 sovereign-benchmark bid/ask validation found one concrete
      example in a successful, otherwise-clean load:

        - `GTCAD1Y Govt`, `YLD_YTM_ASK`, `2008-05-09`
        - stored value: `2147484.00000000`
        - surrounding fields: `YLD_YTM_MID = 2.662`, `YLD_YTM_BID = 2.662`

      This is not an economically possible sovereign yield. It behaves like
      a Bloomberg missing/sentinel/bad-print value that passed through the
      raw data path because the extractor/ingester currently only normalise
      scalars and drop nulls; they do not apply domain-specific plausibility
      filters. The same validation found a small number of benign-looking
      bid/ask and mid-between-bid/ask inconsistencies in older benchmark
      histories, which should be surfaced as data-quality warnings rather
      than silently rewritten.
IMPACT: A single impossible value can dominate downstream analytics:
      z-scores, PCA, volatility, cross-market spreads, bid/ask-spread
      statistics, regression inputs, and trade triggers. This is not a
      schema or ingestion-atomicity problem — the load can be successful
      and still contain vendor-source anomalies that primitives must guard
      against.
POLICY: Preserve raw vendor observations in `market_data_daily` unless a
      dedicated raw-vs-clean storage model is introduced. Do not hand-edit
      individual Bloomberg values silently. Primitive/fetch code should
      apply explicit, documented, field-aware plausibility screens and
      expose what was filtered in methodology/output metadata (P5), while
      keeping the source-of-record value auditable (P2/P12).
FIX: Add a shared data-quality/filtering layer used by rates fetchers before
      primitives compute. The first rules should cover sovereign benchmark
      yield fields (`YLD_YTM_MID`, `YLD_YTM_BID`, `YLD_YTM_ASK`):

        - reject or mask yields outside a defensible range (for example
          `[-50, 100]`, with the exact threshold documented);
        - flag bid/ask inversions separately from hard outliers;
        - flag cases where mid is outside the bid/ask range, but treat them
          as warnings unless the spread/magnitude is impossible;
        - include filtered-row counts and representative examples in the
          primitive methodology/output payload.

      The implementation should be configurable by field family rather than
      hard-coded per primitive, and should have unit tests using the
      `GTCAD1Y Govt` `2147484` bad-print case as a regression fixture.
EFFORT: Medium — shared fetch/cleaning helper, primitive wiring, output
      disclosure, and tests. No schema migration required unless the project
      later chooses to store clean series alongside raw series.
WHEN: Before using A4 sovereign bid/ask fields in production PCA/z-score/
      spread/trade-trigger primitives, and before any future primitive that
      consumes newly added vendor fields without a manual quality screen.

### 27. OTR resolver is forward-only — no historical backfill, detection-date dating

WHERE: rates_agent/playbooks/sovereign_cash_bonds.yml (the `otr_resolution`
       block), utils/incremental_extractor.py (`resolve_otr`),
       ingestion/otr_resolution.py, ingestion/ingest_parquet.py
       (`_process_otr_resolution_blob`), macro_data.otr_history.
WHAT: The A4-4 on-the-run resolver (ADR 0007) records OTR rolls FORWARD ONLY —
      from its first run onward. Two deliberate limitations:
      (a) NO historical OTR backfill. `otr_history` is empty until the first
          resolver run; OTR windows that existed before the resolver went live
          are not reconstructed. The A4-4 probe (`a4_ofr_resolver_probe.py`)
          proved `bdh` of a reference field does NOT historise the OTR chain —
          there is no Bloomberg mechanism to recover past OTR windows, and the
          manual 1st-off-the-run seed was deliberately skipped rather than
          guessed (P2 — accuracy or refuse).
      (b) DETECTION-DATE effective dating. A roll's `effective_from` is the
          resolver's FIRST confirmed observation date, not the bond's true
          auction / benchmark-roll date. At a daily incremental cadence this is
          accurate to ~1-2 days; the gap widens if the extractor runs less
          often. The two-run confirmation gate trades one extra run of latency
          for false-roll protection.
IMPACT: Point-in-time OTR queries (`get_otr_at`) are correct from the first
      resolver run forward. For any date before that, `get_otr_at` returns
      `None` for every slot — honest absence (P5), not a wrong answer. Any
      OTR-history-dependent analytic (on-the-run / off-the-run RV, OTR-roll
      carry) is valid only over the resolver-covered window, and roll dates may
      sit a day or two after the true auction date.
FIX: (a) is fixable only with an authoritative non-Bloomberg history of past
      auctions / benchmark rolls per (country, tenor) — debt-office auction
      calendars — encoded as VERIFIED `otr_history` rows with verified
      effective dates. Until verified, do not fabricate historical windows.
      (b) tighten by running the incremental extractor (hence the resolver)
      daily, and/or by later cross-referencing the auction calendar to correct
      `effective_from` to the true roll date.
EFFORT: (a) Medium — per-market auction-history sourcing + a verified backfill
      loader. (b) Low — a cadence/ops change, or a calendar cross-reference.
WHEN: Before any primitive relies on OTR history PRE-DATING the resolver's
      first run, or needs roll effective dates accurate to the exact auction
      date. NOT needed for forward-looking OTR / off-the-run analytics over the
      resolver-covered window.

### 28. D-auctions (sovereign auction calendar / results) deferred from B2 v1

WHERE: work order B2 (event data); macro_data.event_calendar — the typed
       auction-result columns `high_yield`, `bid_to_cover`, `tail_bps`,
       `indirect_pct` (landed empty by B1 / ADR 0004); a future
       `scripts/` auction-discovery probe + a D-auctions event-playbook
       increment.
WHAT: B2 ships THREE of its four event families — economic releases,
      central-bank meetings, and WIRP. The fourth — sovereign auctions
      (D-auctions) — is deferred. Two Bloomberg verification rounds
      (`scripts/event_data_bloomberg_check.py`) confirmed the clean auction
      fields (`YLD_CNV_FROM_HIGH` = auction high yield, verified by the
      tenor-ordered curve 4.04/4.55/5.08 across 2Y/10Y/30Y;
      `MOST_RECENT_BID_COVER_RATIO`; the bidder-amount fields; announcement
      / issue dates) but left three gaps unresolved:
        (a) the auction DATE — `PRE_ANNOUNCED_AUCTION_DATE` is NULL on
            settled auctions; no clean settled-auction-date field found.
        (b) the TAIL — the only candidate, `MOST_REC_DEBT_AUCTION_STO_YIELD`,
            returns tail-magnitude values (0.002–0.006, tenor-increasing)
            but is NAMED "stop yield" — a name/value contradiction; shipping
            `tail_bps` off it is unsafe (P2).
        (c) the bidder PERCENTAGES — Bloomberg exposes only absolute dollar
            amounts (indirect / primary-dealer / total issued); `indirect_pct`
            would have to be COMPUTED, which is a P12 judgement (total-issued
            ≠ total-accepted exactly), not a clean ingest.
IMPACT: The `event_calendar` auction-result columns stay empty — B2 v1 writes
      no `auction` rows. The sole consumer, the Phase-3 `auction_tail`
      primitive, does NOT exist yet, and ITS core inputs (`tail_bps` +
      `indirect_pct`) are exactly the two unresolved fields — so deferring
      costs nothing today. The three shipped families (economic releases,
      central-bank meetings, WIRP — feeding cpi_surprise / nfp_surprise /
      fomc_surprise + the WIRP layer) are unaffected.
FIX: A focused auction-discovery round — operator FLDS-hunts an unambiguous
      settled-auction-date field and a literally-named tail field on a settled
      UST; resolve the `indirect_pct` P12 question (ingest the amounts and
      compute the % downstream with disclosure, or find a real percentage
      field). Then a D-auctions increment: an event playbook
      (`event_category = auction`), the extractor's auction handling, ingest.
      The `event_calendar` table already carries the typed auction columns
      (ADR 0004) — no schema change when D-auctions is picked up.
EFFORT: Medium — one operator discovery round + one event-playbook increment.
WHEN: Before the Phase-3 `auction_tail` primitive or the auction event-study
      template is built — best done WITH that primitive, so the exact field
      needs are concrete. NOT needed for B2's economic-release / central-bank /
      WIRP families.


### 29. C2b — Term GC repo curves data PR deferred from Track C v1

WHERE: ADR 0010 §Rollout item 3 (the repo-financing substrate ADR); a future
       `rates_agent/playbooks/repo_term_gc.yml` (`ois.yml`-shaped: curve
       points at ON / 1W / 1M / 3M per currency); a future
       `scripts/repo_term_gc_bloomberg_check.py` operator-side verification
       script; the `financing_rate.term_repo_curve()` method on
       `rates_agent/ois/tools/financing_rate/` (currently `NotImplementedError`).
WHAT: ADR 0010 (C1, substrate-only) ships the substrate decision: term GC
      repo is time-series, lands in `macro_data.market_data_daily` under
      the existing `data/` route, registered with `instrument_type: gc_repo`
      — NO new table / extractor mode / ingester route / DB helper. The
      substrate is in place after the ADR landed and no further C2b code is
      required to unblock the data PR.

      C2b is the data PR that would: write `repo_term_gc.yml` listing the
      term-GC tickers per currency (the ADR predicts Bloomberg coverage
      will be "partial"), run the operator-side Bloomberg verification
      script to pin VERIFIED / DEFERRED per ticker per market under the
      project's two-phase data-PR cycle, ingest the verified subset to
      `market_data_daily`, and disclose the per-market coverage in the
      PR's results section. NONE of this work has been done — no playbook
      drafted, no verification script written, no Bloomberg probe run, no
      data ingested. The downstream consumer `financing_rate.term_repo_curve()`
      remains `NotImplementedError`.

      The deferral is by PRIORITY, not by empirical coverage failure (no
      probe has been run; Bloomberg coverage is unknown beyond ADR 0010's
      a-priori "partial" prediction). Track C just shipped four PRs in
      sequence (ADR 0010 / C2a overnight-RFR / C3 deliverables substrate /
      C4 deliverables data) with substantial Codex-iteration cost across
      five rounds; deferring C2b/C2c lets the project move on to higher-
      priority work without leaving partial substrate or half-finished
      verification artifacts.
IMPACT: Zero downstream breakage. `financing_rate.term_repo_curve()` was
      already `NotImplementedError` before Track C started and remains so
      — Track C's promise was to LAND THE DATA, not to implement the
      primitive. No Phase-3 / Phase-4 work currently in flight depends on
      term-GC data. The bond-futures RV stack's `implied_repo_rate`
      primitive (a future Phase-4 candidate) would benefit from term-GC
      data but is not yet scheduled.

      Substrate is unaffected: `market_data_daily` accepts `gc_repo`-typed
      instruments today with zero schema change required when C2b is
      picked up.
FIX: When picked up — (a) write `rates_agent/playbooks/repo_term_gc.yml`
      with CANDIDATE Bloomberg tickers per market (likely starting with
      USD GC ON / 1W / 1M / 3M; major-market expansion as Bloomberg
      coverage permits); (b) build `scripts/repo_term_gc_bloomberg_check.py`
      mirroring `scripts/overnight_rfr_bloomberg_check.py` from C2a;
      (c) operator runs the verification probe on the BBG PC; (d) agent
      finalises the playbook to VERIFIED markers and documents per-market
      DEFERRED entries here for any ticker without clean Bloomberg
      coverage; (e) operator pushes the playbook to the GCS bucket and
      runs the `--mode time-series` extractor against it; (f) ingester's
      existing `data/` route handles the rest; (g) open the data PR with
      the operator-run coverage report.
EFFORT: Small to medium — the substrate, extractor mode, ingester route,
      and ingestion-side helpers all exist and are battle-tested by C2a.
      The new work is one YAML playbook + one verification script + one
      operator probe round + one ingest. Mirrors C2a's effort profile
      exactly. Estimated 1-2 working sessions assuming Bloomberg coverage
      doesn't surface surprises.
WHEN: When a Phase-3 / Phase-4 primitive that genuinely needs term-GC
      repo data is scheduled — best done WITH that primitive's design so
      the curve-point granularity matches the primitive's joins. Until
      then, no value in landing partial term-GC reference data that has
      no consumer.

### 30. C2c — CUSIP-level repo specials data PR deferred from Track C v1

WHERE: ADR 0010 §Rollout item 4 + §Decision 4 (the `otr_history`-bounded
       universe); a future `rates_agent/playbooks/repo_cusip_specials.yml`
       (per-CUSIP, bounded to `macro_data.otr_history`); a future
       `scripts/repo_cusip_specials_bloomberg_check.py`; the
       `financing_rate.gc_special_blend()` method on
       `rates_agent/ois/tools/financing_rate/` (currently
       `NotImplementedError`).
WHAT: ADR 0010 §Decision 4 explicitly anticipated this entry: "if clean
      CUSIP-specials data is not available from Bloomberg for even that
      bounded universe, C2c is deferred and documented rather than
      shipped partial or guessed." ADR 0010 v1 named C2c "the most at
      risk of a documented deferral" before any probe.

      Deferred by PRIORITY (not yet by empirical Bloomberg-coverage
      failure — no probe has been run). The substrate (ADR 0010 +
      `market_data_daily` + `instrument_type: repo_special` +
      `otr_history`-bounded universe rule) is in place; C2c is a pure
      data PR when picked up.

      Same rationale as #29 (Track C just shipped four PRs across the
      C1 / C2a / C3 / C4 sequence with substantial Codex-iteration cost
      across five rounds; the project is moving on to higher-priority
      work). C2c-specific risk: the ADR a-priori expects this is the
      shape most likely to need a non-Bloomberg vendor (DTCC GCF / ICAP),
      which would mean a second L1 adapter — a P7-governed major
      architectural workstream explicitly out of Track C scope. The
      probe round will resolve this empirically when scheduled.
IMPACT: Zero downstream breakage. `financing_rate.gc_special_blend()` was
      `NotImplementedError` before Track C and remains so. No Phase-3 /
      Phase-4 work in flight depends on CUSIP-specials data. The
      bond-futures RV stack's `implied_repo_rate` primitive (future
      Phase-4 candidate) is partially blocked by both C2b and C2c when
      it is eventually scheduled.

      Substrate is unaffected: `market_data_daily` accepts `repo_special`-
      typed instruments today; `otr_history` (ADR 0007) is current and
      provides the universe bounds when C2c is picked up.
FIX: When picked up — (a) operator FLDS-hunts the CUSIP-specials field on
      Bloomberg for a representative `otr_history` CUSIP; if the field
      exists and is populated, build the standard data-PR artifacts
      (playbook + verification script + operator probe + ingest, mirroring
      C2a's flow); (b) if Bloomberg does NOT carry clean CUSIP-specials
      data even for the bounded universe — close C2c as a permanent
      DEFERRED entry here citing the empirical failure, and the
      `financing_rate.gc_special_blend()` method stays `NotImplementedError`
      pending either (i) a separate ADR opening the second-L1-adapter
      question (DTCC GCF or ICAP — P7-governed, out of Track C scope) or
      (ii) accepting that CUSIP-specials data is structurally unavailable
      to this project and re-scoping any primitive that needs it.
EFFORT: One operator FLDS-discovery round to resolve the empirical
      coverage question, then either a small data PR (if Bloomberg
      covers) or a permanent-deferral update to this entry (if not).
      Total worst case: similar to C2a / C2b effort profile if coverage
      exists.
WHEN: Same trigger as #29 — when a Phase-3 / Phase-4 primitive that
      genuinely needs CUSIP-specials data is scheduled. Sensible to bundle
      the C2b discovery round with C2c since both are repo-financing data
      and both unblock the same `financing_rate` primitive methods.


### 31. Bloomberg generic-ticker roll artifacts (linker front-end specifically)

WHERE: `macro_data.market_data_daily` rows for any Bloomberg "generic"
       benchmark ticker (e.g. `GTGBPII1Y Govt`, `GTII5 Govt`, etc.) in
       the final ~2 weeks before the underlying bond rolls.  Most
       visible on `inflation_linker` 1Y series (`GBP_LINKER`,
       `EUR_FR_LINKER`, `CAD_RRB` short-end) because UK / Euro / CAD
       linker generics track a single front-end bond whose YTM
       computation becomes unstable as it approaches maturity.
       Sibling under TD #26's umbrella; the cleaning-pipeline FIX
       documented there subsumes the FIX needed here.
WHAT: Bloomberg generic tickers always point at "the closest <tenor> bond
      right now".  When the underlying bond gets within ~3 months of
      maturity it stops trading liquidly and its yield-to-maturity
      becomes mathematically unstable (tiny price moves divided by
      tiny remaining maturity → huge YTM swings).  Bloomberg keeps
      publishing the quote because the vendor contract is "raw feed,
      not curated" — the cleaning is the consumer's responsibility.
      When Bloomberg rolls the generic to a new underlying bond the
      yield snaps back to normal.  Net pattern: ~5-10 days of
      escalating bad values, then a step back to truth on roll.

      Confirmed occurrence captured during the
      `get_real_yield_level_tool` Phase-1 pilot:

        - Ticker: `GTGBPII1Y Govt` (UK 1Y index-linked gilt generic)
        - Rows: 2026-02-20 → 2026-02-27 (6 consecutive trading days)
        - Values: 6.62% → 7.07% → 7.60% → 8.12% → 8.69% → 10.13%
        - Snap-back on 2026-03-02: -0.16% (normal 1Y linker yield range)
        - Load_ids producing the bad rows: 100 (Apr 9), 166 (May 24)
          — i.e. BOTH ingestion runs faithfully replicated Bloomberg's
          published values; this is NOT an ingestion bug.

      This is a STRUCTURAL recurring pattern, not a one-time bad row.
      Expect the same shape every roll cycle (roughly annually for 1Y
      generics).  Sibling classes — TD #26 documents an INT32_MAX
      sentinel leak (different root cause, same outlier-filtering
      umbrella).  Both deserve the same shared FIX: a documented
      plausibility screen in the fetch/cleaning layer.
IMPACT: Downstream primitives that consume linker series naively (z-score
      tools, percentile tools, future regression / PCA tools on linker
      curves) silently render impossible values.  The
      `get_real_yield_level_tool` chart visualised this on
      2026-05-28 — the chart's y-axis stretched to fit the 10% spike,
      making the actual ~1.5% real-yield trend look like a flat line
      at the bottom of the chart.  Z-score + percentile + trailing-
      range outputs were similarly distorted (the +10% value blew up
      the trailing-range mean / std).

      No DOWNSTREAM data corruption — the spike is a display + stats
      artifact only.  But it directly undermines user trust in the
      product, since a 10% real yield in any G7 market is implausible
      on its face.
POLICY: Same as TD #26 — preserve raw vendor observations in
      `market_data_daily`; do NOT hand-edit individual Bloomberg
      values silently.  Cleaning happens at the fetch/primitive layer
      with explicit, documented, field-aware plausibility screens and
      filtered-row counts surfaced in methodology metadata (P5).
TEMPORARY FIX APPLIED (2026-05-28):
      Two narrow remediations to unblock the `get_real_yield_level_tool`
      Phase-1 pilot:

      (a) **One-off DB cleanup for the unambiguous sibling case** — the
          single `GTCAD1Y Govt YLD_YTM_ASK = 2147484` row originally
          cited in TD #26 was NULLed:

              UPDATE macro_data.market_data_daily
              SET field_value = NULL
              WHERE field_name = 'YLD_YTM_ASK'
                AND field_value > 1000;

          (1 row affected; the load_id chain is preserved so the audit
          trail is intact.)  The GBP_LINKER 1Y rows were NOT cleaned —
          they are 6 contiguous rows representing a STRUCTURAL pattern
          (not a single bad row), and the user's instruction was to
          "tell me if it's a deeper issue" rather than auto-fix.  Those
          rows remain in the DB pending either (i) explicit one-off
          cleanup authorization OR (ii) the shared cleaning-pipeline
          fix per TD #26.

      (b) **Per-tool frontend sanity filter** at
          `UI/.../src/modules/primitives/get_real_yield_level_tool/surfaces/realYieldShared.ts`
          (`sanitiseTimeSeries` + `REAL_YIELD_SANITY_MIN/MAX = ±6%`).
          Out-of-bound values are nulled at the per-tool data-prep
          layer so the chart + reference-band computation cannot be
          distorted.  This is a DEFENSIVE PER-TOOL layer; it does NOT
          remediate the underlying data and does NOT generalise to
          other tools.

      Both fixes are TEMPORARY.  They block the user-visible chart
      regression but do not address the structural class.  The proper
      fix is TD #26's shared cleaning-pipeline layer, which subsumes
      this case automatically.
FIX (permanent — subsumed by TD #26):
      The shared cleaning-layer FIX documented in TD #26 ("Add a
      shared data-quality/filtering layer used by rates fetchers
      before primitives compute") covers both this manifestation and
      the original sentinel-leak case.  Specific additions this entry
      surfaces for the umbrella FIX:

        - The cleaning layer should be aware of the GENERIC-ROLL
          pattern (escalating outliers near a known maturity / roll
          date), not just static range bounds.  Simplest first cut:
          a rolling-window median-deviation filter (reject points
          where |value - rolling_median(N)| > k × rolling_MAD(N))
          with N = 21 trading days, k = 6.  This catches both
          sentinel leaks AND generic-roll artifacts without needing
          a per-ticker bond-master.
        - Per-tool output should expose a `data_quality_warnings`
          field listing rejected rows + the reason (P5 honest
          disclosure — the user sees that 6 days were filtered, not
          a silent gap).
        - Regression-fixture coverage for both the
          `GTCAD1Y Govt 2147484` sentinel case (TD #26's example) AND
          the `GTGBPII1Y Govt 2026-02-20-27` generic-roll case (this
          entry's example).

EFFORT: Medium (per TD #26's existing assessment).  This entry adds
      generic-roll-pattern detection on top of static range bounds —
      adds ~1 working session to TD #26's scope.

WHEN: Same trigger as TD #26 — before any new linker / inflation /
      bond-futures primitive that consumes vendor data without a
      manual quality screen.  Until then the per-tool sanity filter
      in `get_real_yield_level_tool` is the workaround; copy the
      pattern to sibling tools that hit the same class (the
      `calculate_breakeven_inflation_simple_tool` pilot is the
      immediate next consumer).

CROSS-REF:
      - TD #26 (parent / shared-infrastructure fix; same outlier-
        filtering umbrella)
      - `docs_revamped/03_standards/methodology_disclosure.md`
        (P5 disclosure obligation when filtering)
      - `tests/test_real_yield_level_compute.py::TestCanonicalTimeSeries`
        (place to add the per-tool regression test once the bad rows
        are cleaned)


### 32. Template-lane router disabled to isolate-test the open-DAG lane (DISABLE_TEMPLATE_ROUTER flag) — TESTING SCAFFOLDING; CLEAR BEFORE PROD
WHERE:
      - `orchestrator/session.py` — env-var gate block right above the
        OPEN-DAG PRE-ROUTER section (look for the comment block
        "PR-11 testing: ``DISABLE_TEMPLATE_ROUTER=1`` skips the
        template-lane gate entirely so every turn drops into the
        open-DAG lane below.").
      - `docker-compose.yml` — api-server service's ``environment:``
        block, the line ``- DISABLE_TEMPLATE_ROUTER=1`` (look for
        the comment "PR-11 isolation flag: skip the template-lane
        gate so every chat turn drops into the open-DAG composer/
        executor.")
      - Also closely related (NOT the same flag but same testing
        wake): `orchestrator/session.py` around the
        ``OPEN_DAG_PRE_ROUTER`` block — when the open-DAG pipeline
        returns ``status=PIPELINE_ERROR`` we now surface that
        honestly via the structured markdown + emit
        ``workflow_status: error``, instead of falling through to
        the legacy supervisor lane.  See the comment
        "Codex Round 5 corrective: handle EVERY non-None outcome
        here (including PIPELINE_ERROR) and return without falling
        through to the legacy supervisor lane."  Reverting this
        WITHOUT also clearing the flag below would restore
        silent-legacy-fallback masking — keep the two reverts
        bundled.

WHAT:
      Two coupled testing scaffolds added during the live-LLM
      bring-up of the open-DAG pipeline:

      1. The ``DISABLE_TEMPLATE_ROUTER`` env var, when truthy
         (default ``"0"``; we ship ``"1"`` in docker-compose),
         tells ``CopilotSession`` to SKIP the call to
         ``_maybe_run_workflow`` at the top of each turn.  The
         template-lane gate (which inspects the user prompt against
         the registered ``WorkflowTemplate`` recipes — event_study,
         regime_conditioned_relationship, paused backtest) never
         fires.  Every turn falls through to the open-DAG
         pre-router below it.

         Code shape (paraphrased — the canonical text is in
         ``orchestrator/session.py``):

           import os as _tmpl_router_env
           _template_router_disabled = (
               _tmpl_router_env.environ.get(
                   "DISABLE_TEMPLATE_ROUTER", "0",
               ).strip() in ("1", "true", "True", "yes")
           )

           if (
               self._workflow_router is not None
               and not _template_router_disabled
           ):
               workflow_handled = await self._maybe_run_workflow(...)
               if workflow_handled:
                   return
           elif _template_router_disabled:
               logger.info(
                   "[%s] %s DISABLE_TEMPLATE_ROUTER=1 — skipping "
                   "template-lane gate; routing directly to open-DAG.",
                   self.thread_id, turn_label,
               )

         Default behaviour (flag unset or ``"0"``) is unchanged
         from pre-flag baseline: template-router-first, open-DAG
         as fallback.  Flag set to ``"1"`` is the isolation-test
         mode.

      2. The open-DAG block immediately below was modified so
         ``PIPELINE_ERROR`` outcomes surface their structured
         markdown directly + emit ``workflow_status: error`` (or
         ``complete`` for ``PASS_DRYRUN``), instead of falling
         through to the legacy supervisor.  Pre-fix, a
         ``PIPELINE_ERROR`` quietly handed the turn back to the
         supervisor, which would re-run the bound primitives and
         emit a misleading synthesis (the "out_of_scope" answer we
         saw during canonical-query bring-up).

      Why both: while testing the open-DAG lane in isolation, we
      need the template lane OUT OF THE WAY (#1) AND the legacy
      supervisor lane OUT OF THE FALLBACK PATH on open-DAG failure
      (#2) — otherwise either path masks the open-DAG behaviour.

WHY THIS IS DEBT (not a permanent design choice):
      - Both changes were authored as TESTING scaffolds, not as the
        eventual production routing contract.  Plan §PR-10 line 762
        states: "if a template-router would match cleanly, use the
        template lane; otherwise route to open-DAG."  The template
        lane is the cheaper / faster / more-deterministic path
        when a template matches a turn cleanly; disabling it
        permanently is wasteful for templated archetypes.
      - The PIPELINE_ERROR no-fallback behaviour is correct for
        bring-up (we want to see open-DAG failures honestly), but
        the legacy supervisor remains a useful graceful-degradation
        path in production.  Once open-DAG is stable, the fallback
        should resume.

WHEN TO CLEAR (decision triggers — any ONE of these is enough):
      a) The open-DAG lane has been live for ≥1 week with no
         PIPELINE_ERROR outcomes against a representative prompt
         suite (correlation, rolling stats, regime queries,
         out-of-scope refusals).
      b) The Phase-1 frontend factory hits the next 9 standardized
         tools and we want to validate template-lane regressions
         haven't piled up.
      c) Production go-live cutover.

HOW TO CLEAR (two surgical edits + optional code removal):

      MINIMAL CLEAR (recommended first step — flips behaviour
      without touching code):

        1. ``docker-compose.yml``: change
             ``- DISABLE_TEMPLATE_ROUTER=1``
           to
             ``- DISABLE_TEMPLATE_ROUTER=0``
           (or delete the line entirely — the gate defaults to
           "0"/false when the env is unset).
        2. ``docker compose up -d --force-recreate api-server``
           to pick up the env change.
        3. Confirm in the api-server logs: the
           ``DISABLE_TEMPLATE_ROUTER=1 — skipping ...`` info line
           should NO LONGER appear on chat turns; the
           ``_maybe_run_workflow`` call fires as it did before
           PR-11 testing.
        4. Sanity-test that template-matching prompts still route
           to the template lane (e.g. "event study of US 2s10s
           around CPI surprise prints" should land on
           ``event_study``; "regime-conditioned relationship..."
           should land on ``regime_conditioned_relationship``).
           See the prompt list in ``tmp/frontend_orchestration.md``
           "Tier 4" for canonical template-matching queries.

      FULL CLEAR (remove the testing scaffolding entirely — do
      this once the minimal clear has been live for ≥1 week
      without incident):

        a) Delete the ``_template_router_disabled`` env-var gate
           block in ``orchestrator/session.py`` and restore the
           pre-PR-11 shape (the inner ``if self._workflow_router
           is not None: ...`` stays; just the surrounding gate
           goes).
        b) Remove the ``- DISABLE_TEMPLATE_ROUTER=`` line from
           ``docker-compose.yml`` entirely.
        c) Revisit the PIPELINE_ERROR fallback decision: if you
           want the legacy supervisor to resume serving as the
           degraded-mode fallback, restore the pre-Codex-Round-5
           shape (the ``if open_dag_outcome is not None and
           outcome.status != "PIPELINE_ERROR":`` gate around the
           emit + return block).  If you'd rather keep
           PIPELINE_ERROR surfacing honestly (recommended; it
           prevents the misleading "out_of_scope" failure mode),
           leave the post-Round-5 shape in place — that path is
           NOT testing scaffolding, it's a real UX fix.

      Decision summary:
        - Step 1-4 (minimal clear) = restore template-first
          routing; ~5 min, no code change.
        - Step a-c (full clear) = remove the env-var gate + decide
          on PIPELINE_ERROR fallback policy; ~30 min including
          regression test.

EFFORT:
      Trivial (minimal clear: 5 minutes).  Full clear ~30 minutes
      including a regression sweep on template-matching prompts.

CROSS-REF:
      - `tmp/frontend_orchestration.md` — the PR-11 plan that
        triggered the testing scaffolding.  Specifically §C.4
        ("Tab semantics for template_id=null") and the §D.2 backend
        integration test pattern that pre-mocks the template lane
        out of the picture.
      - `orchestrator/session.py:run_open_dag` — the open-DAG
        entry point.  The flag only affects the routing-time gate;
        it does NOT affect ``run_open_dag`` itself.
      - `orchestrator/open_dag/pipeline.py::PipelineOutcome.status`
        — the closed set of statuses (PASS / PASS_DRYRUN /
        GATE_REFUSE / GATE_CLARIFY / COMPOSER_REFUSE /
        ASSEMBLY_REFUSE / ROUTER_CLARIFY / PIPELINE_ERROR).  The
        no-fallback decision applies uniformly across all of these.
      - The git diff that introduced the flag is small (~30 LOC in
        ``session.py`` + 5 LOC in ``docker-compose.yml``).  No new
        dependencies, no schema changes.


### 36. Multi-scalar response prompts do not work — no first-class "set of numbers" output (architecture gap)

WHERE: shared/artifacts/types.py + shared/artifacts/registry.py (the
       closed artifact family); shared/operators/summarize_series/
       (the ``dispersion`` knob); orchestrator/open_dag/composer.py +
       orchestrator/prompts.py (composer + L1 shape contract);
       orchestrator/open_dag/answer.py (L6 renderer).  Related to TD #23.

WHAT:  Any query that asks for MORE THAN ONE number does not return all
       of them.  Confirmed live (real-backend + frontend review, 2026-06):
         - "mean AND std of US 2s10s" → PARTIAL.  The composer correctly
           builds ``summarize_series(statistic=mean, dispersion=std)`` and
           the std IS computed — but the terminal artifact is a single
           ``ScalarMetric`` that carries ONLY the mean (9.2 bps).  The std
           is recorded in the lineage step's ``dispersion_value`` and never
           rendered, so the answer prose + the ScalarMetric widget show the
           mean only.  The std silently vanishes from the user's answer.
         - "mean, median AND std of X" (3+ stats) → REFUSED entirely.  The
           composer knows it cannot bundle three co-equal scalars into one
           terminal, so it declines (COMPOSER_REFUSE) — no answer at all.
         - "correlation AND beta of X and Y", "the 25th/50th/75th
           percentile of X", etc. → same class.  No way to return N numbers.

WHY:   Three structural facts collide:
         1. The scalar answer type ``ScalarMetric`` holds EXACTLY ONE value
            by design ({metric_key, value, units, lineage}).  It is the
            honest "one number" type.
         2. The open-DAG output contract is SINGLE-TERMINAL: one ShapeSpec
            → one terminal node → one artifact.  There is no slot for a
            second co-equal number in the answer.
         3. ``summarize_series``'s ``dispersion`` knob is a SMUGGLE, not an
            output: it computes the std and stashes it in lineage params
            (diagnostic side-record), not as a returned value.  Nothing
            renders lineage side-records.
       Net: the system has ``SeriesSet`` (a bundle of N time-series) but NO
       scalar analog — there is no first-class "bundle of N named scalars"
       artifact.  So "give me N numbers" has no representation.  (TD #23
       already flagged ``summarize_series`` as "a temporary bridge because
       the artifact layer is still missing the right scalar output type."
       The SINGLE-scalar half of #23 is now RESOLVED — ``summarize_series``
       emits a real ``ScalarMetric`` since the Series→ScalarMetric
       migration.  The MULTI-scalar half is this entry, still open.)

IMPACT: Affects every multi-number prompt class, inconsistently: 2-number
        prompts answer partially (one number shown, the rest hidden);
        3+-number prompts refuse outright.  A PM asking "mean and std"
        gets a confidently-incomplete answer (worse than a refusal, because
        nothing signals the std was dropped).  Does NOT affect single-number
        prompts (average / current / correlation / cointegration — all
        correct) or series prompts (z-score / rolling-corr — all correct).

FIX:   GENERAL fix — do NOT hardcode std-surfacing into ``summarize_series``
       (that only helps std-of-a-summary and nothing else).  Give the
       architecture a proper multi-scalar OUTPUT, mirroring ``SeriesSet``:
         (a) New closed-family artifact type ``ScalarMetricSet`` — an
             ordered bundle of N named scalars (e.g. {mean: 9.2 bps,
             std: 59.4 bps}).  One ADR; registration-clean (types.py +
             ArtifactTypeName enum + ARTIFACT_CLASS_TO_NAME + the closed-
             family lockstep test + executor bridge + store codec).
         (b) One GENERIC operator ``combine_scalars`` (finance-blind) that
             fans in N ``ScalarMetric`` inputs and emits a
             ``ScalarMetricSet``.  Works for ANY scalars from ANY operators.
         (c) Then every multi-number query is the SAME shape: N scalar
             sub-DAGs (summarize_series(mean), summarize_series(std), …, or
             correlation + rolling_regression→last, …) → combine_scalars →
             ScalarMetricSet terminal.  General by construction.
         (d) Plumb through the Phase-A-E machinery already in place: add a
             ``scalar_set`` member to ``AnswerShape``; L1 declares
             ``["scalar_set"]`` for multi-number prompts; the deterministic
             verifier checks the terminal is a ScalarMetricSet; the composer
             gets ONE guidance rule ("N requested statistics → N scalar
             nodes → combine_scalars").  Retire the ``dispersion`` knob's
             double-duty (keep it as lineage detail; route real multi-number
             answers through the bundle).
         (e) Frontend: a ``ScalarMetricSet`` widget rendering a small
             named-number table (the scalar analog of the SeriesSet widget).
       This respects the non-negotiables: closed-family extension (one type
       + one operator, both registration-only — no composer/validator/
       executor rewrite), and it makes "give me N numbers" a first-class,
       scalable shape instead of smuggle-or-refuse.

EFFORT: Medium.  One new artifact type (+ its closed-family wiring + store
        codec + lockstep), one new generic operator, the shape-vocab +
        composer-rule additions, and one frontend widget.  No schema
        migration; no change to the single-terminal contract.

WHEN:   When multi-statistic / multi-metric answers become desk-relevant.
        Until then, the system is HONEST on single-number prompts and the
        only user-facing harm is the partial "mean and std" answer — which
        should at minimum be made to REFUSE-or-clarify ("I can return one
        summary statistic per run today") rather than silently drop the std,
        if this entry is not picked up first.

EVIDENCE: tmp/prompt_tests/FRONTEND_SCORECARD.md (T6 mean+std — mean shown,
          std absent; DAG inspector confirms Dispersion=std was computed);
          tmp/prompt_tests/sessionE/diagnosis_E.md (E3 #5 "mean, median and
          std" → COMPOSER_REFUSE); GAP_LEDGER.md G02.


## Phase 1 closure punch list (for reference)

Per the original Phase 1 Week 7-8 plan:
- ✅ TIPS-vs-2Y thesis runs end-to-end via natural language (PR 19/20)
- ✅ Workspace persists, URL replays byte-identical (Phase 0 PR 11 pattern)
- ✅ Methodology card on every node shows assumptions (per-primitive YAML; PR 21 adds the par-par + yield-change + financing-proxy disclosures)
- ⚠️  4 desk-critical 1B tools — REVISED: 3 of 4 deferred above with explicit data prerequisites; ``asset_swap_spread`` already exists
- ⏳ External practitioner sign-off — process gate, not engineering

Phase 1 is engineering-complete after PR 21 lands.  External
practitioner sign-off + UI work (workspace renderer for the
methodology disclosures) are Phase 3 concerns.
