# MACRO COPILOT — TECHNICAL DEBT REGISTER
# Last updated: April 2026
# Status: All patch-level defects resolved. Items below are architectural
#         improvements, not bugs in the current code.


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

### 6. Persistent LangGraph checkpointer
WHERE: orchestrator/graph.py (MemorySaver)
WHAT: Conversation state is in-memory only. Lost on restart. Single
      hardcoded thread_id.
FIX: Replace MemorySaver with PostgresSaver or RedisSaver. Add per-user
     thread IDs. Add message trimming/summarization for long conversations.
EFFORT: Low-Medium for PostgresSaver swap, Medium for trimming logic.
WHEN: Before any frontend, multi-user traffic, or supervisor node.

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