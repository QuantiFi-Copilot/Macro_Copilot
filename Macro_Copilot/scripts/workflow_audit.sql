-- ============================================================================
-- workflow_audit.sql — full-workflow integrity audit of the macro_data schema.
--
-- Covers everything built across this task:
--   A4  — the on-the-run resolver (ADR 0007): macro_data.otr_history + the
--         sovereign_cash_bond instruments it tracks.
--   B2  — the macro event layer (ADR 0004 / 0008 / 0009): macro_data.event_calendar
--         (D-econ economic releases, D-cb central-bank meetings) and D-wirp
--         (WIRP per-meeting implied rates — wirp_meeting instruments + their
--         market_data_daily fields + the event_calendar.related_instrument_id link).
--   B3  — the inflation layer: inflation_reference / inflation_linker /
--         inflation_swap instruments + their market_data_daily.
-- Plus the shared substrate: instrument_master, market_data_daily, load_audit,
-- instrument_metadata_history, and the macro_data views.
--
-- RUN:
--   docker exec -i macro-tsdb psql -U quantuser -d macrodata < scripts/workflow_audit.sql
--
-- READING IT: every query is labelled with its EXPECTED result. Integrity
-- checks are phrased so the right answer is 0 (or empty). Section 10 is a
-- one-screen RED-FLAG ROLLUP — every row there must read 0.
--
-- D-wirp note: WIRP extraction may not have run yet. Section 5 then shows 0 /
-- empty — that is correct PRE-extraction. Re-run this whole file AFTER the
-- WIRP extract+ingest+backfill cycle and Section 5 must show 73 instruments,
-- 4 fields each, and 73 linked central-bank-meeting rows.
-- ============================================================================
\timing off
\pset pager off

\echo ''
\echo '############################################################'
\echo '### 0. SCHEMA INVENTORY'
\echo '############################################################'

-- Expect: 6 base tables, 3 views.
SELECT 'base tables' AS kind, count(*) FROM information_schema.tables
  WHERE table_schema='macro_data' AND table_type='BASE TABLE'
UNION ALL
SELECT 'views', count(*) FROM information_schema.views WHERE table_schema='macro_data';

-- Exact row count per table. Sanity baseline for everything below.
SELECT 'instrument_master'           AS tbl, count(*) AS rows FROM macro_data.instrument_master
UNION ALL SELECT 'market_data_daily',           count(*) FROM macro_data.market_data_daily
UNION ALL SELECT 'instrument_metadata_history', count(*) FROM macro_data.instrument_metadata_history
UNION ALL SELECT 'otr_history',                 count(*) FROM macro_data.otr_history
UNION ALL SELECT 'event_calendar',              count(*) FROM macro_data.event_calendar
UNION ALL SELECT 'load_audit',                  count(*) FROM macro_data.load_audit
ORDER BY tbl;


\echo ''
\echo '############################################################'
\echo '### 1. LOAD_AUDIT  — the lineage backbone of every load'
\echo '############################################################'

-- Status breakdown. Expect every load SUCCESS (FAILED rows are kept history;
-- investigate any that are not expected).
SELECT status, count(*) FROM macro_data.load_audit GROUP BY status ORDER BY status;

-- STUCK loads — RUNNING means an ingest crashed mid-flight. EXPECT 0 ROWS.
SELECT load_id, playbook_name, dataset_name, status, extracted_at, ingested_at
FROM macro_data.load_audit WHERE status = 'RUNNING';

-- FAILED loads — list them (a failed sanity/coverage gate is a real signal).
SELECT load_id, playbook_name, status, left(notes, 90) AS notes
FROM macro_data.load_audit WHERE status = 'FAILED' ORDER BY load_id;

-- Per-playbook load history + latest status.
SELECT playbook_name,
       count(*) AS loads,
       count(*) FILTER (WHERE status='SUCCESS') AS ok,
       max(ingested_at) AS latest_ingest,
       (array_agg(status ORDER BY load_id DESC))[1] AS latest_status
FROM macro_data.load_audit GROUP BY playbook_name ORDER BY playbook_name;

-- Lineage-hash coverage across SUCCESS loads. playbook_hash + source_file_hash
-- should be populated on every recent load; git_commit_hash is a known,
-- pre-existing gap (the extractor does not populate it — every load, not
-- specific to A4/B2/B3).
SELECT count(*) AS success_loads,
       count(*) FILTER (WHERE playbook_hash    IS NULL) AS missing_playbook_hash,
       count(*) FILTER (WHERE source_file_hash IS NULL) AS missing_source_hash,
       count(*) FILTER (WHERE git_commit_hash  IS NULL) AS missing_git_hash
FROM macro_data.load_audit WHERE status='SUCCESS';


\echo ''
\echo '############################################################'
\echo '### 2. INSTRUMENT_MASTER  — the instrument identity layer'
\echo '############################################################'

-- Breakdown by instrument_type, with market-data coverage. An instrument with
-- 0 market-data rows is an orphan (or just-created, pre-extraction).
SELECT im.instrument_type,
       count(*) AS instruments,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM macro_data.market_data_daily md
           WHERE md.instrument_id = im.instrument_id)) AS with_market_data,
       count(*) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM macro_data.market_data_daily md
           WHERE md.instrument_id = im.instrument_id)) AS orphans
FROM macro_data.instrument_master im
GROUP BY im.instrument_type ORDER BY im.instrument_type;

-- Breakdown by asset_class / country / currency.
SELECT asset_class, country, currency, count(*)
FROM macro_data.instrument_master
GROUP BY asset_class, country, currency ORDER BY asset_class, country, currency;

-- Breakdown by curve_family.
SELECT coalesce(curve_family,'(null)') AS curve_family, count(*)
FROM macro_data.instrument_master GROUP BY curve_family ORDER BY 1;

-- (vendor, vendor_ticker) uniqueness — uq_instrument_vendor. EXPECT 0.
SELECT count(*) AS duplicate_vendor_ticker FROM (
  SELECT vendor, vendor_ticker FROM macro_data.instrument_master
  GROUP BY vendor, vendor_ticker HAVING count(*) > 1) d;

-- Duplicate non-NULL CUSIP / ISIN — cash-bond identity must be unique. EXPECT 0.
SELECT 'dup cusip' AS k, count(*) FROM (
  SELECT cusip FROM macro_data.instrument_master WHERE cusip IS NOT NULL
  GROUP BY cusip HAVING count(*)>1) d
UNION ALL
SELECT 'dup isin', count(*) FROM (
  SELECT isin FROM macro_data.instrument_master WHERE isin IS NOT NULL
  GROUP BY isin HAVING count(*)>1) d;

-- NOT-NULL columns actually populated. EXPECT 0.
SELECT count(*) AS instruments_missing_required_cols
FROM macro_data.instrument_master
WHERE vendor IS NULL OR vendor_ticker IS NULL
   OR asset_class IS NULL OR instrument_type IS NULL;


\echo ''
\echo '############################################################'
\echo '### 3. A4 — OTR RESOLVER  (otr_history + sovereign_cash_bond)'
\echo '############################################################'

-- Every otr_history window, joined to the bond it tracks. effective_to NULL =
-- the current open window. Bootstrap-only state = one open window per slot.
SELECT o.country, o.tenor, o.otr_instrument_id,
       im.vendor_ticker, im.isin,
       o.effective_from, o.effective_to,
       CASE WHEN o.effective_to IS NULL THEN 'OPEN' ELSE 'closed' END AS window,
       o.load_id
FROM macro_data.otr_history o
LEFT JOIN macro_data.instrument_master im ON im.instrument_id = o.otr_instrument_id
ORDER BY o.country, o.tenor, o.effective_from;

-- One OPEN window per (country, tenor) slot — never zero, never two. EXPECT 0.
SELECT count(*) AS slots_with_wrong_open_window_count FROM (
  SELECT country, tenor, count(*) FILTER (WHERE effective_to IS NULL) AS open_windows
  FROM macro_data.otr_history GROUP BY country, tenor
  HAVING count(*) FILTER (WHERE effective_to IS NULL) <> 1) d;

-- ck_otr_history_window: effective_from must not exceed effective_to. EXPECT 0.
SELECT count(*) AS bad_otr_windows
FROM macro_data.otr_history
WHERE effective_to IS NOT NULL AND effective_from > effective_to;

-- No overlapping windows within a slot (ex_otr_history_no_overlap). EXPECT 0.
SELECT count(*) AS overlapping_otr_windows FROM (
  SELECT a.otr_history_id
  FROM macro_data.otr_history a
  JOIN macro_data.otr_history b
    ON a.country=b.country AND a.tenor=b.tenor AND a.otr_history_id<>b.otr_history_id
   AND daterange(a.effective_from, a.effective_to, '[]')
     && daterange(b.effective_from, b.effective_to, '[]')) d;

-- Every otr_instrument_id resolves to a sovereign_cash_bond instrument. EXPECT 0.
SELECT count(*) AS otr_pointing_to_bad_instrument
FROM macro_data.otr_history o
LEFT JOIN macro_data.instrument_master im ON im.instrument_id = o.otr_instrument_id
WHERE im.instrument_id IS NULL OR im.instrument_type <> 'sovereign_cash_bond';

-- Every otr_history.load_id resolves to a load_audit row. EXPECT 0.
SELECT count(*) AS otr_orphan_load_id
FROM macro_data.otr_history o
LEFT JOIN macro_data.load_audit la ON la.load_id = o.load_id
WHERE la.load_id IS NULL;

-- OTR bonds: every one must carry an ISIN and the /isin/ vendor_ticker
-- convention (ADR 0007 §6). EXPECT 0.
SELECT count(*) AS otr_bonds_bad_identity
FROM macro_data.instrument_master im
WHERE im.instrument_type='sovereign_cash_bond'
  AND (im.isin IS NULL OR im.vendor_ticker NOT LIKE '/isin/%');

-- otr_history attributes — the false-roll pending_candidate (ADR 0007 §4).
-- A clean bootstrap carries none.
SELECT o.country, o.tenor,
       (o.attributes ? 'pending_candidate') AS has_pending_candidate,
       o.attributes
FROM macro_data.otr_history o ORDER BY o.country, o.tenor;

-- sovereign_cash_bond instruments vs the OTR windows that track them.
SELECT (SELECT count(*) FROM macro_data.instrument_master
        WHERE instrument_type='sovereign_cash_bond')          AS cash_bond_instruments,
       (SELECT count(DISTINCT otr_instrument_id)
        FROM macro_data.otr_history)                          AS distinct_bonds_in_otr_history,
       (SELECT count(*) FROM macro_data.instrument_master im
        WHERE im.instrument_type='sovereign_cash_bond'
          AND EXISTS (SELECT 1 FROM macro_data.market_data_daily md
                      WHERE md.instrument_id=im.instrument_id)) AS cash_bonds_with_market_data;


\echo ''
\echo '############################################################'
\echo '### 4. B2 — EVENT_CALENDAR  (D-econ + D-cb)'
\echo '############################################################'

-- Category breakdown. Expect economic_release 433, central_bank_meeting 88.
SELECT event_category, count(*) FROM macro_data.event_calendar
GROUP BY event_category ORDER BY event_category;

-- Per event_type / country, realized vs scheduled.
SELECT event_category, event_type, country,
       count(*) AS n,
       count(*) FILTER (WHERE actual IS NOT NULL) AS realized,
       count(*) FILTER (WHERE actual IS NULL)     AS scheduled
FROM macro_data.event_calendar
GROUP BY event_category, event_type, country
ORDER BY event_category, country, event_type;

-- Natural-key (event_type, country, release_date) uniqueness. EXPECT 0.
SELECT count(*) AS duplicate_event_natural_keys FROM (
  SELECT event_type, country, release_date FROM macro_data.event_calendar
  GROUP BY event_type, country, release_date HAVING count(*)>1) d;

-- surprise must be NULL — event ingestion never computes it (P12). EXPECT 0.
SELECT count(*) AS event_rows_with_surprise_set
FROM macro_data.event_calendar WHERE surprise IS NOT NULL;

-- event_category stays inside the closed family (P8). EXPECT 0.
SELECT count(*) AS event_rows_bad_category
FROM macro_data.event_calendar
WHERE event_category NOT IN ('economic_release','central_bank_meeting','auction');

-- Central-bank decisions (realized meetings). hike/hold/cut rides in attributes.
SELECT central_bank, attributes->>'decision' AS decision, count(*)
FROM macro_data.event_calendar
WHERE event_category='central_bank_meeting' AND actual IS NOT NULL
GROUP BY central_bank, attributes->>'decision'
ORDER BY central_bank, decision;

-- Central-bank realized rows must carry actual + prior; scheduled must not.
-- EXPECT 0 in both anomaly columns.
SELECT
  count(*) FILTER (WHERE actual IS NOT NULL AND prior IS NULL)   AS realized_missing_prior,
  count(*) FILTER (WHERE actual IS NULL AND prior IS NOT NULL)   AS scheduled_with_prior
FROM macro_data.event_calendar WHERE event_category='central_bank_meeting';

-- Policy-rate sanity per bank (realized).
SELECT central_bank, min(actual) AS min_rate, max(actual) AS max_rate
FROM macro_data.event_calendar
WHERE event_category='central_bank_meeting' AND actual IS NOT NULL
GROUP BY central_bank ORDER BY central_bank;

-- Economic-release consensus coverage + actual sanity. JP composite_pmi is
-- survey-less by design (consensus 0 — ADR 0008 §2).
SELECT event_type, country,
       count(*) FILTER (WHERE actual IS NOT NULL)           AS realized,
       count(*) FILTER (WHERE consensus_median IS NOT NULL) AS has_consensus,
       min(actual) AS lo, max(actual) AS hi
FROM macro_data.event_calendar WHERE event_category='economic_release'
GROUP BY event_type, country ORDER BY country, event_type;

-- release_date span + every load_id resolves. EXPECT orphan_load_id 0.
SELECT min(release_date) AS first_release, max(release_date) AS last_release,
       count(*) FILTER (WHERE la.load_id IS NULL) AS orphan_load_id
FROM macro_data.event_calendar ec
LEFT JOIN macro_data.load_audit la ON la.load_id = ec.load_id;


\echo ''
\echo '############################################################'
\echo '### 5. B2 — D-WIRP  (WIRP per-meeting implied rates)'
\echo '###   PRE-extraction: all 0 / empty (correct). POST: 73 / 4 / 73.'
\echo '############################################################'

-- wirp_meeting instruments. POST-extraction EXPECT 73.
SELECT count(*) AS wirp_meeting_instruments
FROM macro_data.instrument_master WHERE instrument_type='wirp_meeting';

-- WIRP market_data by field. POST-extraction EXPECT the 4 WIRP fields.
SELECT field_name, count(*) AS rows, count(DISTINCT instrument_id) AS instruments,
       min(field_value) AS lo, max(field_value) AS hi
FROM macro_data.market_data_daily
WHERE field_name LIKE 'WIRP\_%'
GROUP BY field_name ORDER BY field_name;

-- Per-meeting metric coverage — every wirp_meeting instrument must carry all 4
-- WIRP fields (strict 4/4, ADR 0009 §4). POST-extraction EXPECT 0.
SELECT count(*) AS wirp_instruments_not_full_4of4 FROM (
  SELECT im.instrument_id
  FROM macro_data.instrument_master im
  LEFT JOIN macro_data.market_data_daily md
    ON md.instrument_id=im.instrument_id AND md.field_name LIKE 'WIRP\_%'
  WHERE im.instrument_type='wirp_meeting'
  GROUP BY im.instrument_id
  HAVING count(DISTINCT md.field_name) <> 4) d;

-- WIRP orphans — a wirp_meeting instrument with NO market data. EXPECT 0.
SELECT count(*) AS wirp_orphan_instruments
FROM macro_data.instrument_master im
WHERE im.instrument_type='wirp_meeting'
  AND NOT EXISTS (SELECT 1 FROM macro_data.market_data_daily md
                  WHERE md.instrument_id=im.instrument_id);

-- The related_instrument_id link: central-bank-meeting rows linked to a WIRP
-- instrument. POST-extraction+backfill EXPECT 73 linked, 15 unlinked
-- (the far-future meetings outside the horizon).
SELECT count(*) FILTER (WHERE related_instrument_id IS NOT NULL)  AS linked,
       count(*) FILTER (WHERE related_instrument_id IS NULL)      AS unlinked
FROM macro_data.event_calendar WHERE event_category='central_bank_meeting';

-- related_instrument_id must point ONLY at a wirp_meeting instrument. EXPECT 0.
SELECT count(*) AS link_to_non_wirp_instrument
FROM macro_data.event_calendar ec
JOIN macro_data.instrument_master im ON im.instrument_id = ec.related_instrument_id
WHERE im.instrument_type <> 'wirp_meeting';

-- Link correctness — the linked WIRP instrument must share the meeting's
-- (central_bank, date): ec.central_bank = attributes->>'central_bank' and
-- ec.release_date = im.maturity_date (ADR 0009 §1/§5). EXPECT 0.
SELECT count(*) AS mismatched_wirp_links
FROM macro_data.event_calendar ec
JOIN macro_data.instrument_master im ON im.instrument_id = ec.related_instrument_id
WHERE ec.event_category='central_bank_meeting'
  AND (ec.central_bank IS DISTINCT FROM im.attributes->>'central_bank'
       OR ec.release_date IS DISTINCT FROM im.maturity_date);

-- Every wirp_meeting instrument that has data should be reachable from an
-- event_calendar row. POST EXPECT 0 (no dangling WIRP instrument).
SELECT count(*) AS wirp_instruments_not_linked_back
FROM macro_data.instrument_master im
WHERE im.instrument_type='wirp_meeting'
  AND EXISTS (SELECT 1 FROM macro_data.market_data_daily md
              WHERE md.instrument_id=im.instrument_id)
  AND NOT EXISTS (SELECT 1 FROM macro_data.event_calendar ec
                  WHERE ec.related_instrument_id = im.instrument_id);

-- WIRP instrument identity — every wirp_meeting must carry the source-ticker
-- provenance + identity keys in attributes (ADR 0009 §1). POST EXPECT 0.
SELECT count(*) AS wirp_instruments_missing_attributes
FROM macro_data.instrument_master im
WHERE im.instrument_type='wirp_meeting'
  AND NOT (im.attributes ? 'central_bank' AND im.attributes ? 'meeting_date'
           AND im.attributes ? 'wirp_region_prefix' AND im.attributes ? 'wirp_meeting_token'
           AND im.attributes ? 'wirp_ticker_fr' AND im.attributes ? 'wirp_ticker_pr'
           AND im.attributes ? 'wirp_ticker_nm' AND im.attributes ? 'wirp_ticker_ch');

-- maturity_date must mirror the attributes meeting_date. POST EXPECT 0.
SELECT count(*) AS wirp_maturity_meeting_date_mismatch
FROM macro_data.instrument_master im
WHERE im.instrument_type='wirp_meeting'
  AND im.maturity_date::text IS DISTINCT FROM (im.attributes->>'meeting_date');


\echo ''
\echo '############################################################'
\echo '### 6. B3 — INFLATION  (inflation_reference / linker / swap)'
\echo '############################################################'

-- The three inflation instrument types + market-data coverage.
SELECT im.instrument_type, im.curve_family,
       count(*) AS instruments,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM macro_data.market_data_daily md
           WHERE md.instrument_id=im.instrument_id)) AS with_market_data
FROM macro_data.instrument_master im
WHERE im.instrument_type IN ('inflation_reference','inflation_linker','inflation_swap')
GROUP BY im.instrument_type, im.curve_family
ORDER BY im.instrument_type, im.curve_family;

-- Inflation market-data footprint (per dataset, via load_audit).
SELECT la.dataset_name,
       count(*) AS rows, count(DISTINCT md.instrument_id) AS instruments,
       min(md.trade_date) AS first_obs, max(md.trade_date) AS last_obs
FROM macro_data.market_data_daily md
JOIN macro_data.load_audit la ON la.load_id = md.load_id
WHERE la.dataset_name IN ('inflation_references','inflation_indexed_bonds','inflation_swaps')
GROUP BY la.dataset_name ORDER BY la.dataset_name;

-- Every attribute key actually present on each inflation instrument type — a
-- discovery view, no assumed key name. NOTE: only inflation_swap carries
-- index_lag / interpolation (curated per-row in the playbook YAML);
-- inflation_linker and inflation_reference carry neither — cross-check against
-- the intended B3 design / docs/technical_debt.md #25 if a per-row lag was
-- expected on the linker bonds.
SELECT im.instrument_type, k AS attribute_key, count(*) AS instruments
FROM macro_data.instrument_master im, LATERAL jsonb_object_keys(im.attributes) k
WHERE im.instrument_type IN ('inflation_reference','inflation_linker','inflation_swap')
GROUP BY im.instrument_type, k ORDER BY im.instrument_type, k;

-- The distinct index_lag / interpolation values actually stored.
SELECT 'index_lag'     AS attr, im.attributes->>'index_lag'     AS value, count(*)
FROM macro_data.instrument_master im
WHERE im.instrument_type IN ('inflation_reference','inflation_linker','inflation_swap')
  AND im.attributes ? 'index_lag'
GROUP BY 2
UNION ALL
SELECT 'interpolation', im.attributes->>'interpolation', count(*)
FROM macro_data.instrument_master im
WHERE im.instrument_type IN ('inflation_reference','inflation_linker','inflation_swap')
  AND im.attributes ? 'interpolation'
GROUP BY 2
ORDER BY attr, value;


\echo ''
\echo '############################################################'
\echo '### 7. MARKET_DATA_DAILY  (the time-series substrate)'
\echo '############################################################'

-- Global shape.
SELECT count(*) AS rows,
       count(DISTINCT instrument_id) AS instruments,
       count(DISTINCT field_name)    AS fields,
       min(trade_date) AS first_date, max(trade_date) AS last_date
FROM macro_data.market_data_daily;

-- Per-dataset footprint (covers A4 sovereign_cash_bonds, B3 inflation_*, and
-- every other time-series playbook).
SELECT la.dataset_name,
       count(*) AS rows, count(DISTINCT md.instrument_id) AS instruments,
       min(md.trade_date) AS first_obs, max(md.trade_date) AS last_obs
FROM macro_data.market_data_daily md
JOIN macro_data.load_audit la ON la.load_id = md.load_id
GROUP BY la.dataset_name ORDER BY rows DESC;

-- field_name inventory.
SELECT field_name, count(*) FROM macro_data.market_data_daily
GROUP BY field_name ORDER BY count(*) DESC;

-- FK integrity — instrument_id / load_id must resolve. EXPECT 0 / 0.
SELECT
  (SELECT count(*) FROM macro_data.market_data_daily md
   LEFT JOIN macro_data.instrument_master im ON im.instrument_id=md.instrument_id
   WHERE im.instrument_id IS NULL) AS orphan_instrument_id,
  (SELECT count(*) FROM macro_data.market_data_daily md
   LEFT JOIN macro_data.load_audit la ON la.load_id=md.load_id
   WHERE la.load_id IS NULL)       AS orphan_load_id;

-- NULL field_value (the extractor drops these). EXPECT 0.
SELECT count(*) AS null_field_values
FROM macro_data.market_data_daily WHERE field_value IS NULL;

-- Duplicate (instrument_id, trade_date, field_name) — the upsert key. EXPECT 0.
SELECT count(*) AS duplicate_observations FROM (
  SELECT instrument_id, trade_date, field_name
  FROM macro_data.market_data_daily
  GROUP BY instrument_id, trade_date, field_name HAVING count(*)>1) d;

-- Implausible trade dates — far future or pre-1990. EXPECT 0.
SELECT count(*) FILTER (WHERE trade_date > current_date + 7) AS future_dated,
       count(*) FILTER (WHERE trade_date < DATE '1990-01-01') AS ancient
FROM macro_data.market_data_daily;


\echo ''
\echo '############################################################'
\echo '### 8. INSTRUMENT_METADATA_HISTORY  (rolling-contract SCD2)'
\echo '############################################################'

-- Shape + FK integrity. EXPECT orphan columns 0.
SELECT count(*) AS rows,
       count(DISTINCT h.instrument_id) AS instruments,
       count(*) FILTER (WHERE im.instrument_id IS NULL) AS orphan_instrument_id,
       count(*) FILTER (WHERE la.load_id IS NULL)       AS orphan_load_id
FROM macro_data.instrument_metadata_history h
LEFT JOIN macro_data.instrument_master im ON im.instrument_id = h.instrument_id
LEFT JOIN macro_data.load_audit la        ON la.load_id       = h.load_id;

-- Window sanity — effective_from must not exceed effective_to. EXPECT 0.
SELECT count(*) AS bad_metadata_windows
FROM macro_data.instrument_metadata_history
WHERE effective_to IS NOT NULL AND effective_from > effective_to;

-- No overlapping windows per instrument (ex_..._no_overlap). EXPECT 0.
SELECT count(*) AS overlapping_metadata_windows FROM (
  SELECT a.metadata_history_id
  FROM macro_data.instrument_metadata_history a
  JOIN macro_data.instrument_metadata_history b
    ON a.instrument_id=b.instrument_id AND a.metadata_history_id<>b.metadata_history_id
   AND daterange(a.effective_from,a.effective_to,'[]')
     && daterange(b.effective_from,b.effective_to,'[]')) d;


\echo ''
\echo '############################################################'
\echo '### 9. VIEWS'
\echo '############################################################'

-- The enriched view must be queryable and consistent with market_data_daily.
SELECT 'v_market_data_daily_enriched' AS view, count(*) AS rows
FROM macro_data.v_market_data_daily_enriched
UNION ALL
SELECT 'v_rates_instruments',  count(*) FROM macro_data.v_rates_instruments
UNION ALL
SELECT 'v_sovereign_curves',   count(*) FROM macro_data.v_sovereign_curves;

-- The enriched view should have exactly one row per market_data_daily row.
-- EXPECT 0.
SELECT (SELECT count(*) FROM macro_data.v_market_data_daily_enriched)
     - (SELECT count(*) FROM macro_data.market_data_daily) AS enriched_row_delta;


\echo ''
\echo '############################################################'
\echo '### 10. RED-FLAG ROLLUP  — EVERY ROW MUST READ 0'
\echo '############################################################'

SELECT check_name, violations FROM (
  SELECT '01 load_audit: stuck RUNNING loads' AS check_name,
         (SELECT count(*) FROM macro_data.load_audit WHERE status='RUNNING') AS violations
  UNION ALL SELECT '02 instrument_master: duplicate (vendor,vendor_ticker)',
    (SELECT count(*) FROM (SELECT vendor,vendor_ticker FROM macro_data.instrument_master
       GROUP BY vendor,vendor_ticker HAVING count(*)>1) d)
  UNION ALL SELECT '03 instrument_master: duplicate non-null cusip',
    (SELECT count(*) FROM (SELECT cusip FROM macro_data.instrument_master
       WHERE cusip IS NOT NULL GROUP BY cusip HAVING count(*)>1) d)
  UNION ALL SELECT '04 instrument_master: duplicate non-null isin',
    (SELECT count(*) FROM (SELECT isin FROM macro_data.instrument_master
       WHERE isin IS NOT NULL GROUP BY isin HAVING count(*)>1) d)
  UNION ALL SELECT '05 A4 otr_history: slot with != 1 open window',
    (SELECT count(*) FROM (SELECT country,tenor FROM macro_data.otr_history
       GROUP BY country,tenor
       HAVING count(*) FILTER (WHERE effective_to IS NULL)<>1) d)
  UNION ALL SELECT '06 A4 otr_history: bad window (from>to)',
    (SELECT count(*) FROM macro_data.otr_history
       WHERE effective_to IS NOT NULL AND effective_from>effective_to)
  UNION ALL SELECT '07 A4 otr_history: overlapping windows in a slot',
    (SELECT count(*) FROM macro_data.otr_history a JOIN macro_data.otr_history b
       ON a.country=b.country AND a.tenor=b.tenor AND a.otr_history_id<>b.otr_history_id
      AND daterange(a.effective_from,a.effective_to,'[]')
        && daterange(b.effective_from,b.effective_to,'[]'))
  UNION ALL SELECT '08 A4 otr_history: instrument not a sovereign_cash_bond',
    (SELECT count(*) FROM macro_data.otr_history o
       LEFT JOIN macro_data.instrument_master im ON im.instrument_id=o.otr_instrument_id
       WHERE im.instrument_id IS NULL OR im.instrument_type<>'sovereign_cash_bond')
  UNION ALL SELECT '09 A4 otr_history: orphan load_id',
    (SELECT count(*) FROM macro_data.otr_history o
       LEFT JOIN macro_data.load_audit la ON la.load_id=o.load_id WHERE la.load_id IS NULL)
  UNION ALL SELECT '10 A4: sovereign_cash_bond missing isin or /isin/ ticker',
    (SELECT count(*) FROM macro_data.instrument_master
       WHERE instrument_type='sovereign_cash_bond'
         AND (isin IS NULL OR vendor_ticker NOT LIKE '/isin/%'))
  UNION ALL SELECT '11 B2 event_calendar: duplicate natural key',
    (SELECT count(*) FROM (SELECT event_type,country,release_date
       FROM macro_data.event_calendar
       GROUP BY event_type,country,release_date HAVING count(*)>1) d)
  UNION ALL SELECT '12 B2 event_calendar: surprise set (P12 violation)',
    (SELECT count(*) FROM macro_data.event_calendar WHERE surprise IS NOT NULL)
  UNION ALL SELECT '13 B2 event_calendar: event_category outside closed set',
    (SELECT count(*) FROM macro_data.event_calendar
       WHERE event_category NOT IN ('economic_release','central_bank_meeting','auction'))
  UNION ALL SELECT '14 B2 event_calendar: orphan load_id',
    (SELECT count(*) FROM macro_data.event_calendar ec
       LEFT JOIN macro_data.load_audit la ON la.load_id=ec.load_id WHERE la.load_id IS NULL)
  UNION ALL SELECT '15 B2 cb-meeting: realized row missing prior',
    (SELECT count(*) FROM macro_data.event_calendar
       WHERE event_category='central_bank_meeting' AND actual IS NOT NULL AND prior IS NULL)
  UNION ALL SELECT '16 D-wirp: related_instrument_id -> non-wirp instrument',
    (SELECT count(*) FROM macro_data.event_calendar ec
       JOIN macro_data.instrument_master im ON im.instrument_id=ec.related_instrument_id
       WHERE im.instrument_type<>'wirp_meeting')
  UNION ALL SELECT '17 D-wirp: mismatched (central_bank,date) on a link',
    (SELECT count(*) FROM macro_data.event_calendar ec
       JOIN macro_data.instrument_master im ON im.instrument_id=ec.related_instrument_id
       WHERE ec.event_category='central_bank_meeting'
         AND (ec.central_bank IS DISTINCT FROM im.attributes->>'central_bank'
              OR ec.release_date IS DISTINCT FROM im.maturity_date))
  UNION ALL SELECT '18 D-wirp: wirp_meeting instrument not full 4/4',
    (SELECT count(*) FROM (SELECT im.instrument_id FROM macro_data.instrument_master im
       LEFT JOIN macro_data.market_data_daily md
         ON md.instrument_id=im.instrument_id AND md.field_name LIKE 'WIRP\_%'
       WHERE im.instrument_type='wirp_meeting'
       GROUP BY im.instrument_id HAVING count(DISTINCT md.field_name)<>4) d)
  UNION ALL SELECT '19 D-wirp: wirp_meeting instrument with no market data',
    (SELECT count(*) FROM macro_data.instrument_master im
       WHERE im.instrument_type='wirp_meeting'
         AND NOT EXISTS (SELECT 1 FROM macro_data.market_data_daily md
                         WHERE md.instrument_id=im.instrument_id))
  UNION ALL SELECT '20 D-wirp: data-bearing wirp instrument not linked back',
    (SELECT count(*) FROM macro_data.instrument_master im
       WHERE im.instrument_type='wirp_meeting'
         AND EXISTS (SELECT 1 FROM macro_data.market_data_daily md
                     WHERE md.instrument_id=im.instrument_id)
         AND NOT EXISTS (SELECT 1 FROM macro_data.event_calendar ec
                         WHERE ec.related_instrument_id=im.instrument_id))
  UNION ALL SELECT '21 market_data_daily: orphan instrument_id',
    (SELECT count(*) FROM macro_data.market_data_daily md
       LEFT JOIN macro_data.instrument_master im ON im.instrument_id=md.instrument_id
       WHERE im.instrument_id IS NULL)
  UNION ALL SELECT '22 market_data_daily: orphan load_id',
    (SELECT count(*) FROM macro_data.market_data_daily md
       LEFT JOIN macro_data.load_audit la ON la.load_id=md.load_id WHERE la.load_id IS NULL)
  UNION ALL SELECT '23 market_data_daily: NULL field_value',
    (SELECT count(*) FROM macro_data.market_data_daily WHERE field_value IS NULL)
  UNION ALL SELECT '24 market_data_daily: duplicate observation',
    (SELECT count(*) FROM (SELECT instrument_id,trade_date,field_name
       FROM macro_data.market_data_daily
       GROUP BY instrument_id,trade_date,field_name HAVING count(*)>1) d)
  UNION ALL SELECT '25 instrument_metadata_history: overlapping windows',
    (SELECT count(*) FROM macro_data.instrument_metadata_history a
       JOIN macro_data.instrument_metadata_history b
       ON a.instrument_id=b.instrument_id AND a.metadata_history_id<>b.metadata_history_id
      AND daterange(a.effective_from,a.effective_to,'[]')
        && daterange(b.effective_from,b.effective_to,'[]'))
  UNION ALL SELECT '26 instrument_metadata_history: orphan instrument_id',
    (SELECT count(*) FROM macro_data.instrument_metadata_history h
       LEFT JOIN macro_data.instrument_master im ON im.instrument_id=h.instrument_id
       WHERE im.instrument_id IS NULL)
  UNION ALL SELECT '27 v_market_data_daily_enriched: row delta vs base',
    abs((SELECT count(*) FROM macro_data.v_market_data_daily_enriched)
      - (SELECT count(*) FROM macro_data.market_data_daily))
) r ORDER BY check_name;

\echo ''
\echo '### AUDIT COMPLETE — Section 10 above: every violations value must be 0.'
\echo '### (Checks 16-20 also read 0 PRE-WIRP-extraction; re-run POST-extraction.)'
