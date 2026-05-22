// ============================================================================
// handoffSignal.ts — PR-B-β: distinguish Ask-handoff vs Library-blank.
// ----------------------------------------------------------------------------
// Both surfaces deep-link into Build via the same ``?context=`` URL
// pattern.  Pre-PR-B-β there was no way to tell them apart; the typed
// canvas folded spec defaults silently in both cases.
//
// PR-B-β adds an explicit URL marker.  Ask appends ``&handoff=ask``;
// Library doesn't.  Surfaces that need different policies (the
// missing-param tile vs silent defaults) read this signal via
// ``isAskHandoff(...)``.
//
// Backward compatibility
// ----------------------
// Pre-PR-B-β URLs without the marker (bookmarks, shared links,
// Library opens) default to ``handoff !== 'ask'`` → existing
// "fold-the-defaults" behaviour preserved.  Only NEW Ask-side URLs
// set the marker, and only THOSE trigger the missing-param flow.
//
// Pure, no React.  Easy to unit-test.
// ============================================================================

/** The closed family of recognised handoff origins.  Anything else
 *  (including absent / malformed values) is treated as "library" so
 *  the silent-defaults path keeps working for non-Ask deep-links. */
export type HandoffOrigin = 'ask' | 'library';

/** Canonical name of the URL query param the Ask side sets when
 *  handing a tool trace to Build.  Exported so the Ask-side encoder
 *  and the Build-side decoder agree on the literal. */
export const HANDOFF_QUERY_PARAM = 'handoff' as const;

/** The literal value Ask writes to the param when its message is the
 *  origin of the handoff.  Pre-PR-B-β links lack this entirely; we
 *  default to the library-blank policy in that case so existing
 *  behaviour is preserved. */
export const HANDOFF_ASK_VALUE = 'ask' as const;

/** Decode the handoff origin from a query-string source.  Accepts a
 *  ``URLSearchParams`` (test path) OR a raw query-string fragment
 *  (the runtime path inside ``BuildShell``).
 *
 *  Returns ``'ask'`` ONLY when the param explicitly equals ``"ask"``;
 *  every other value (including absent, empty, garbage) returns
 *  ``'library'``.  Defaulting to library is the BACKWARD-COMPATIBLE
 *  choice — pre-PR-B-β URLs continue to render with spec defaults. */
export function parseHandoffOrigin(
  source: URLSearchParams | string | null | undefined,
): HandoffOrigin {
  if (source == null) return 'library';
  const params =
    typeof source === 'string' ? new URLSearchParams(source) : source;
  const raw = params.get(HANDOFF_QUERY_PARAM);
  if (raw === HANDOFF_ASK_VALUE) return 'ask';
  return 'library';
}

/** Convenience predicate matching the parser.  Returns ``true`` ONLY
 *  for explicit ``handoff=ask`` URLs.  Use this at policy-decision
 *  sites (missing-param tile rendering, etc.) so the literal value
 *  is checked in exactly one place. */
export function isAskHandoff(
  source: URLSearchParams | string | null | undefined,
): boolean {
  return parseHandoffOrigin(source) === 'ask';
}

/** Build the query-param fragment Ask should append when constructing
 *  an "Open in Build" URL.  Returned WITHOUT a leading ``&`` /
 *  ``?`` so the caller can compose it freely:
 *
 *      `/workspace?context=${ctx}&${handoffQueryFragment('ask')}`
 *
 *  Returns the empty string for non-Ask origins so the caller can
 *  unconditionally append without producing trailing ``&`` noise. */
export function handoffQueryFragment(origin: HandoffOrigin): string {
  if (origin === 'ask') return `${HANDOFF_QUERY_PARAM}=${HANDOFF_ASK_VALUE}`;
  return '';
}
