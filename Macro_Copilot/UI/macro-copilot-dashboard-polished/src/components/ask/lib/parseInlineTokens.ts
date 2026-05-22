// ============================================================================
// parseInlineTokens
// ----------------------------------------------------------------------------
// Detects "data tokens" inside assistant prose so the renderer can style
// them as first-class data instead of plain text — e.g. `+1.5σ`,
// `-3.4 bps`, `47 events`, `2026-05-08`, `0.42%`.
//
// The backend ships prose as plain markdown (bold + code + line breaks);
// it does not annotate numeric tokens.  This module is the client-side
// detection layer that finds them via a single conservative regex pass.
//
// Conservative principles:
//   - Only match patterns that are unambiguously data (number + unit /
//     ISO date / σ / explicit count word).  Don't over-match — if we
//     style "today" or a sentence number as data, the prose stops
//     reading like prose.
//   - Preserve the underlying text exactly (no rewriting).  The renderer
//     wraps each match in a styled span; everything else passes through
//     untouched.
//   - Tone (positive / negative) keys off a leading `+` / `-` ON THE
//     NUMBER ITSELF.  No semantic inference about whether a "loss" is
//     bad — that's the job of the prose, not the token styler.
// ============================================================================

export type InlineTokenKind = 'data' | 'data-pos' | 'data-neg';

export type InlineSegment =
  | { kind: 'text'; text: string }
  | { kind: 'token'; tone: InlineTokenKind; text: string };

// Patterns evaluated in order of specificity.  The first match wins per
// position; subsequent patterns get whatever is left.
//
// Calibrated for rates / macro prose:
//   - SIGMA:    +1.5σ, -2σ, 1.5σ
//   - BPS:      -3.4 bps, +12.0 bps, 3 bps
//   - PCT:      0.42%, +1.2%, -0.5%
//   - DATE:     2026-05-08
//   - COUNT:    "47 events", "1,260 obs", "5 days"
//   - HORIZON:  "T+3", "T-2", "T+10"
//   - MAGN:     standalone signed decimals adjacent to currency-style
//               labels are NOT matched here — too noisy.  Better to let
//               them flow as text unless the unit is explicit.
const PATTERNS: Array<{ kind: 'sigma' | 'bps' | 'pct' | 'date' | 'count' | 'horizon'; re: RegExp }> = [
  { kind: 'sigma',   re: /([+\-]?\d+(?:\.\d+)?)\s?σ/g },
  { kind: 'bps',     re: /([+\-]?\d+(?:\.\d+)?)\s?bps\b/g },
  { kind: 'pct',     re: /([+\-]?\d+(?:\.\d+)?)\s?%/g },
  { kind: 'date',    re: /\b(\d{4}-\d{2}-\d{2})\b/g },
  { kind: 'count',   re: /\b(\d{1,3}(?:,\d{3})*|\d+)\s+(events|obs|observations|days|weeks|months|trading\s+days)\b/g },
  { kind: 'horizon', re: /\b(T[+\-]\d+)\b/g },
];

export function parseInlineTokens(text: string): InlineSegment[] {
  if (!text) return [];

  // Collect every match across all patterns into a single list, then
  // resolve overlaps by preferring the earliest start (and longest at
  // tie).  Finally, slice the text into alternating text / token
  // segments.
  type Match = { start: number; end: number; tone: InlineTokenKind };
  const matches: Match[] = [];

  for (const { kind, re } of PATTERNS) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const numberLike = (m[1] ?? '').toString();
      const tone = toneFor(kind, numberLike);
      matches.push({ start: m.index, end: m.index + m[0].length, tone });
    }
  }

  // Sort by start, drop overlapping.
  matches.sort((a, b) =>
    a.start === b.start ? b.end - b.start - (a.end - a.start) : a.start - b.start,
  );
  const filtered: Match[] = [];
  let cursor = -1;
  for (const m of matches) {
    if (m.start < cursor) continue;
    filtered.push(m);
    cursor = m.end;
  }

  if (filtered.length === 0) return [{ kind: 'text', text }];

  const segments: InlineSegment[] = [];
  let pos = 0;
  for (const m of filtered) {
    if (m.start > pos) {
      segments.push({ kind: 'text', text: text.slice(pos, m.start) });
    }
    segments.push({
      kind: 'token',
      tone: m.tone,
      text: text.slice(m.start, m.end),
    });
    pos = m.end;
  }
  if (pos < text.length) {
    segments.push({ kind: 'text', text: text.slice(pos) });
  }
  return segments;
}

function toneFor(
  kind: 'sigma' | 'bps' | 'pct' | 'date' | 'count' | 'horizon',
  numberLike: string,
): InlineTokenKind {
  // Date / count / horizon tokens are not signed — render in the
  // neutral `data` tone.
  if (kind === 'date' || kind === 'count' || kind === 'horizon') {
    return 'data';
  }
  if (numberLike.startsWith('-')) return 'data-neg';
  if (numberLike.startsWith('+')) return 'data-pos';
  return 'data';
}
