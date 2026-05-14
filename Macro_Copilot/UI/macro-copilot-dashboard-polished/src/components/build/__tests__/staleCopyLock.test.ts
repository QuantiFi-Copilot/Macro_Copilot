/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// staleCopyLock.test.ts — PR-A regression lock for user-visible PR copy.
// ----------------------------------------------------------------------------
// The original build PR1–PR8 series left visible product copy that
// referenced internal milestones ("ships in PR B", "land in PR4",
// "Read-only in PR A").  PR-A swept it out (see PR-A description).
// This lock catches it coming back.
//
// Strategy
// --------
// Walk every TSX file under ``src/components/build/`` (the surface
// the audit covered) plus a handful of cross-cutting surfaces
// (``src/components/library/ToolDetailDrawer.tsx``).  For each file,
// strip JS/TS comments and JSX comments, then assert the remaining
// substantive content does NOT match any of the documented stale
// patterns.  Source-level comments are explicitly preserved — they
// document HISTORY and would otherwise rot if hidden behind a lint
// rule.
//
// Patterns flagged
// ----------------
// - ``PR <letter>`` followed by a space-or-end ("PR A", "PR B").
// - ``PR <digit>`` followed by a space-or-end ("PR2", "PR4").
// - "ships in PR" / "coming in PR" / "lands in PR" / "land in PR"
//   anywhere.
// - "schema builder · PR" — the specific chip we cleaned up; if a
//   future contributor restores that suffix we want to catch it.
//
// What this lock does NOT do
// --------------------------
// - It doesn't enforce that any specific tile / button is wired.
//   Wiring is a behavioural concern; this is a copy-discipline lock.
// - It doesn't sweep .ts files (no JSX = nothing rendered).
// ============================================================================

interface NodeFs {
  readFileSync: (path: string, encoding: string) => string;
  readdirSync: (path: string) => string[];
  statSync: (path: string) => { isDirectory: () => boolean; isFile: () => boolean };
}
interface NodeGlobal {
  process?: { cwd?: () => string };
}

type Check = { label: string; fn: () => Promise<void> | void };
const _checks: Check[] = [];

function check(label: string, fn: () => Promise<void> | void): void {
  _checks.push({ label, fn });
}

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
}

async function loadFs(): Promise<NodeFs> {
  // @ts-expect-error - node-only; esbuild --platform=node resolves it.
  return (await import('fs')) as NodeFs;
}

function cwd(): string {
  const _g = globalThis as unknown as NodeGlobal;
  return _g.process?.cwd?.() ?? '.';
}

async function walkTsx(root: string): Promise<string[]> {
  const fs = await loadFs();
  const out: string[] = [];
  function rec(dir: string): void {
    let entries: string[];
    try {
      entries = fs.readdirSync(dir);
    } catch {
      return;
    }
    for (const name of entries) {
      const full = `${dir}/${name}`;
      let stat;
      try {
        stat = fs.statSync(full);
      } catch {
        continue;
      }
      if (stat.isDirectory()) {
        // Skip __tests__ subtrees — they reference these patterns
        // exhaustively and would otherwise self-trigger.
        if (name === '__tests__') continue;
        rec(full);
        continue;
      }
      if (stat.isFile() && name.endsWith('.tsx')) out.push(full);
    }
  }
  rec(root);
  return out;
}

// ----------------------------------------------------------------------------
// Comment-stripping — keep JSX-rendered text only.
// ----------------------------------------------------------------------------
//
// Source comments document HISTORY and are valuable.  The lock is
// about USER-VISIBLE copy — JSX text content + attribute values.
// We strip:
//   - ``/* ... */`` block comments (greedy, multi-line).
//   - ``// ...`` line comments to end of line.
//   - ``{/* ... */}`` JSX comments.
// What's left includes JSX text, ``title=...``, ``placeholder=...``,
// and any other string-literal attribute the audit cares about.

function stripComments(src: string): string {
  let s = src;
  // JSX comments: {/* ... */}  — strip first so the inner '/*...*/'
  // isn't double-handled.
  s = s.replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '');
  // C-style block comments.
  s = s.replace(/\/\*[\s\S]*?\*\//g, '');
  // Line comments.
  s = s.replace(/\/\/.*$/gm, '');
  return s;
}

// ----------------------------------------------------------------------------
// Pattern list — every stale phrase the PR-A audit called out, plus
// the obvious regression vectors.  Each entry has a label so the
// failure message points at the exact thing to fix.
// ----------------------------------------------------------------------------

interface Pattern {
  label: string;
  re: RegExp;
}

const STALE_PATTERNS: Pattern[] = [
  // "PR A", "PR B", "PR C" — letter-suffix internal milestones.  We
  // intentionally match a word-boundary on both sides so phrases like
  // "PRA" (a real word, e.g. as part of a name) don't trigger, and
  // "PR Album" / "PR Annual" type false positives don't either.  The
  // lock specifically targets "PR <single uppercase letter>".
  { label: 'letter milestone', re: /\bPR\s+[A-C]\b/ },
  // "PR1", "PR4", etc. — digit-suffix internal milestones.
  { label: 'digit milestone', re: /\bPR[0-9]\b/ },
  // Specific phrases the audit found.
  { label: 'ships in PR', re: /ships in PR/i },
  { label: 'coming in PR', re: /coming in PR/i },
  { label: 'land(s) in PR', re: /lands? in PR/i },
  { label: 'read-only in PR', re: /read-only in PR/i },
  { label: 'workspace-scoped chat ships in PR', re: /chat ships in PR/i },
  // The chip suffix we cleaned up in GenericPrimitiveBuilder.
  { label: 'schema builder · PR', re: /schema builder\s*·\s*PR/i },
];

// ----------------------------------------------------------------------------
// Checks
// ----------------------------------------------------------------------------

check('stale-copy lock: no user-visible PR-era copy under src/components/build/', async () => {
  const fs = await loadFs();
  const root = `${cwd()}/src/components/build`;
  const files = await walkTsx(root);
  assertTruthy(files.length > 10, 'walked enough files');
  const findings: string[] = [];
  for (const f of files) {
    const raw = fs.readFileSync(f, 'utf8');
    const stripped = stripComments(raw);
    for (const p of STALE_PATTERNS) {
      const m = p.re.exec(stripped);
      if (m) {
        findings.push(`${f.replace(cwd() + '/', '')}: ${p.label} → "${m[0]}"`);
      }
    }
  }
  if (findings.length > 0) {
    throw new Error(
      `Stale PR-era copy still visible to users:\n  ` + findings.join('\n  '),
    );
  }
});

check('stale-copy lock: ToolDetailDrawer (library surface) is clean', async () => {
  const fs = await loadFs();
  const path = `${cwd()}/src/components/library/ToolDetailDrawer.tsx`;
  let raw: string;
  try {
    raw = fs.readFileSync(path, 'utf8');
  } catch {
    throw new Error(`Could not read ${path}`);
  }
  const stripped = stripComments(raw);
  for (const p of STALE_PATTERNS) {
    const m = p.re.exec(stripped);
    if (m) {
      throw new Error(
        `${path.replace(cwd() + '/', '')}: ${p.label} → "${m[0]}"`,
      );
    }
  }
});

check('stale-copy lock: pattern set still matches a known pre-PR-A example', () => {
  // Sanity: the regex catalogue should still flag the ORIGINAL
  // pre-PR-A strings.  Without this self-test a future edit that
  // accidentally weakens a regex would let real regressions slip.
  const reference = [
    'Save (coming in PR B)',
    'workspace-scoped chat ships in PR B',
    'land in PR4',
    'Read-only in PR A',
    'schema builder · PR2',
  ];
  const missed: string[] = [];
  for (const r of reference) {
    if (!STALE_PATTERNS.some((p) => p.re.test(r))) missed.push(r);
  }
  if (missed.length > 0) {
    throw new Error(
      `Pattern catalogue regressed — these legacy phrases no longer match:\n  ` +
        missed.join('\n  '),
    );
  }
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export async function runAllStaleCopyLockTests(): Promise<void> {
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      await fn();
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `\nstale-copy lock coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} stale-copy lock check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  void runAllStaleCopyLockTests();
}
