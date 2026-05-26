#!/usr/bin/env python3
"""tools/scaffold_module.py — Stage 5+ ergonomic module scaffolder.

Creates a complete frontend module folder for a backend primitive in
one command.  Auto-detects the right runtime tier by reading backend
sources (``rates_agent.workflows._PRIMITIVE_SPECS`` /
``WORKFLOW_INCOMPATIBLE_TOOLS`` / ``orchestrator.events._MANIFEST_ONLY_BUILD_TOOLS``).

Usage:

    python3 tools/scaffold_module.py \\
        --tool calculate_cpi_surprise_tool \\
        --sub-agent sovereign_bonds \\
        --category economic_release_surprises \\
        --display-name 'CPI Surprise' \\
        --one-line-summary 'Latest CPI release vs consensus...' \\
        [--bespoke-build] \\
        [--bespoke-result-renderer KIND] \\
        [--bespoke-preview] \\
        [--bespoke-monitor WIDGET_ID] \\
        [--bespoke-ask]

After running:

    1. ``src/modules/primitives/<tool>/`` exists with module.ts,
       THESIS.md, surfaces/ (per the flags you set), and the
       round-trip test.
    2. The module is imported + added to ``ALL_PRIMITIVE_MODULES``
       in ``src/modules/index.ts`` alphabetically.
    3. Edit the placeholder surfaces to add your actual JSX.
    4. ``npm run test:modules`` should pass without further edits.

The whole point: a developer never edits BuildShell, VirtualPrimitiveCanvas,
ConversationCanvas, monitor/registry, or toolNames to add a new tool.
Module-first dispatch (Stage 5) walks to the folder this script
created and mounts whatever surfaces are inside.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from datetime import date
from typing import Optional


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND_ROOT = REPO_ROOT / "UI" / "macro-copilot-dashboard-polished"
PRIMITIVES_ROOT = FRONTEND_ROOT / "src" / "modules" / "primitives"
LOADER_INDEX = FRONTEND_ROOT / "src" / "modules" / "index.ts"
WORKFLOWS_PY = REPO_ROOT / "rates_agent" / "workflows" / "__init__.py"
EVENTS_PY = REPO_ROOT / "orchestrator" / "events.py"


# -----------------------------------------------------------------------------
# Backend tier detection
# -----------------------------------------------------------------------------


def _read_text(p: pathlib.Path) -> str:
    try:
        return p.read_text()
    except FileNotFoundError:
        return ""


def detect_runtime_tier(tool: str) -> str:
    """Pick the right runtime-status tier for the tool.

    Reads three backend sources to decide:
    - In ``_PRIMITIVE_SPECS`` → ``generic_runnable``.
    - In ``WORKFLOW_INCOMPATIBLE_TOOLS`` → ``workflow_incompatible``.
    - In ``_MANIFEST_ONLY_BUILD_TOOLS`` → ``manifest_typed_view``.
    - Else → ``paused`` (manifest-declared without backend impl).
    """
    workflows_src = _read_text(WORKFLOWS_PY)
    events_src = _read_text(EVENTS_PY)

    # Heuristic regexes — match the canonical-form ``tool_name=`` /
    # quoted-string membership in the sets.
    if re.search(rf"tool_name\s*=\s*['\"]{re.escape(tool)}['\"]", workflows_src):
        return "generic_runnable"
    if re.search(rf"['\"]{re.escape(tool)}['\"]", workflows_src) and (
        "WORKFLOW_INCOMPATIBLE_TOOLS" in workflows_src
    ):
        # Cheap heuristic: tool name appears in workflows.py but not as
        # a ``tool_name=`` entry.  Refine by checking proximity to the
        # WORKFLOW_INCOMPATIBLE_TOOLS dict.
        idx = workflows_src.find(f'"{tool}"')
        if idx < 0:
            idx = workflows_src.find(f"'{tool}'")
        if idx >= 0:
            window = workflows_src[max(0, idx - 2000) : idx]
            if "WORKFLOW_INCOMPATIBLE_TOOLS" in window:
                return "workflow_incompatible"
    if re.search(rf"['\"]{re.escape(tool)}['\"]", events_src) and (
        "_MANIFEST_ONLY_BUILD_TOOLS" in events_src
    ):
        idx = events_src.find(f'"{tool}"')
        if idx < 0:
            idx = events_src.find(f"'{tool}'")
        if idx >= 0:
            window = events_src[max(0, idx - 2000) : idx]
            if "_MANIFEST_ONLY_BUILD_TOOLS" in window:
                return "manifest_typed_view"
    return "paused"


def needs_unsupported_reason(runtime: str) -> bool:
    return runtime in {"workflow_incompatible", "manifest_typed_view", "paused", "deferred"}


# -----------------------------------------------------------------------------
# File templates
# -----------------------------------------------------------------------------


def _tier_list(tiers: list[str]) -> str:
    return "[" + ", ".join(f"'{t}'" for t in tiers) + "]"


def _module_ts(
    tool: str,
    display_name: str,
    category: str,
    one_line_summary: str,
    runtime: str,
    bespoke_build: bool,
    bespoke_result_renderer: Optional[str],
    bespoke_preview: bool,
    bespoke_monitor: Optional[str],
    bespoke_ask: bool,
) -> str:
    cap_tiers: list[str] = []
    if bespoke_build:
        cap_tiers.append("custom_build_surface")
    if bespoke_result_renderer:
        cap_tiers.append("custom_build_surface")
    if bespoke_preview:
        cap_tiers.append("custom_preview_widget")
    if bespoke_monitor:
        cap_tiers.append("monitor_surface")
    if bespoke_ask:
        cap_tiers.append("ask_surface")
    # de-dup (build + resultRenderer both claim custom_build_surface)
    seen: set[str] = set()
    cap_tiers = [t for t in cap_tiers if not (t in seen or seen.add(t))]

    imports: list[str] = ["import type { PrimitiveModuleSpec } from '../../types';"]
    if bespoke_build:
        imports.append("import BuildSurface from './surfaces/BuildSurface';")
    if bespoke_result_renderer:
        imports.append("import ResultRenderer from './surfaces/ResultRenderer';")
    if bespoke_preview:
        imports.append("import PreviewWidget from './surfaces/PreviewWidget';")
    if bespoke_ask:
        imports.append("import AskCard from './surfaces/AskCard';")
    if bespoke_monitor:
        widget_class = "".join(part.capitalize() for part in bespoke_monitor.split("_")) + "Widget"
        imports.append(f"import {{ {widget_class} }} from './surfaces/monitor/{widget_class}';")

    surfaces_lines: list[str] = []
    if bespoke_build:
        surfaces_lines.append("    build: BuildSurface,")
    if bespoke_result_renderer:
        surfaces_lines.append("    resultRenderer: ResultRenderer,")
    if bespoke_preview:
        surfaces_lines.append("    preview: PreviewWidget,")
    if bespoke_ask:
        surfaces_lines.append("    ask: AskCard,")
    surfaces_block = (
        "  surfaces: {\n" + "\n".join(surfaces_lines) + "\n  },\n"
        if surfaces_lines
        else ""
    )

    monitor_block = ""
    if bespoke_monitor:
        widget_class = "".join(part.capitalize() for part in bespoke_monitor.split("_")) + "Widget"
        monitor_block = f"""  monitorWidgets: [
    {{
      id: '{bespoke_monitor}',
      label: '{display_name}',
      description: 'TODO — describe the catalog tile.',
      category: 'data',
      defaultSize: 'medium',
      allowedSizes: ['small', 'medium'],
      parameterized: false,
      component: {widget_class},
    }},
  ],
"""

    typed_view_line = (
        f"  typedView: '{bespoke_result_renderer}',\n" if bespoke_result_renderer else ""
    )

    reason_block = ""
    if needs_unsupported_reason(runtime):
        reason_block = f"""  unsupportedReason: {{
    label: '{display_name}',
    reason: 'TODO — explain why the tool is {runtime} on the backend (mirror the backend rationale).',
    whatWorksNow: 'TODO — what alternative the user can reach today.',
  }},
"""

    all_tiers = [runtime, *cap_tiers]

    return f"""// ============================================================================
// src/modules/primitives/{tool}/module.ts
// ----------------------------------------------------------------------------
// Generated by ``tools/scaffold_module.py`` (Stage 5+ scaffolder).
// Edit the placeholder surfaces under ``surfaces/`` to add the
// tool-specific JSX; this spec file generally doesn't need further
// edits unless you change tier claims.
// ============================================================================

{chr(10).join(imports)}

export const MODULE: PrimitiveModuleSpec = {{
  toolName: '{tool}',
  tiers: {_tier_list(all_tiers)},
  displayName: '{display_name}',
  category: '{category}',
  oneLineSummary:
    '{one_line_summary}',
{typed_view_line}{surfaces_block}{monitor_block}{reason_block}}};
"""


def _thesis_md(
    tool: str,
    display_name: str,
    category: str,
    one_line: str,
    runtime: str,
    cap_tiers: list[str],
) -> str:
    tier_set = _tier_list([runtime, *cap_tiers])
    return f"""# THESIS — `{tool}`

> Scaffolded by `tools/scaffold_module.py`.  Fill in the Q&A below as the
> surfaces land.

**Version:** v1 (scaffolded)
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `{tool}` ({runtime})
**Tier set:** `{tier_set}`
**Category:** `{category}`

---

## 1. What surfaces does this module ship?

- **`{runtime}`** — runtime-status tier.
{chr(10).join(f"- **`{t}`** — TODO describe." for t in cap_tiers)}

## 2. What does the user read off each surface?

TODO

## 3. Why these surfaces and not others?

TODO

## 4. What would change the design?

TODO

## 5. Which backend doctrine does this module operationalise?

- **FM1** — folder name equals backend `tool_name` exactly.
- **FM3** — claims runtime tier `{runtime}`{f" + capabilities [{', '.join(cap_tiers)}]" if cap_tiers else ""}.
- **FM7** — `module.ts` exports a pure value.
- **FM8** — every claimed capability tier has a populated surface file.
- **FM10** — this file.
- **FM11** — `__tests__/module.spec.ts` calls `assertStandardModuleInvariants`.
- **FM12** — module imported in `src/modules/index.ts`.

---

## One-line summary

{one_line}

---

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | {date.today().isoformat()} | Scaffolded by ``tools/scaffold_module.py``. |
"""


def _module_spec_test(tool: str) -> str:
    safe = tool.lstrip("_")
    return f"""// ============================================================================
// {tool}/__tests__/module.spec.ts — Stage 5 scaffold-generated round-trip.
// ============================================================================

import {{ assertStandardModuleInvariants }} from '@/modules/__test-utils';
import {{ MODULE }} from '../module';

type Check = {{ label: string; fn: () => Promise<void> | void }};
const _checks: Check[] = [];
function check(label: string, fn: () => Promise<void> | void): void {{
  _checks.push({{ label, fn }});
}}

interface NodeGlobal {{ process?: {{ cwd?: () => string }} }}

check('module satisfies the standard invariants', async () => {{
  const _g = globalThis as unknown as NodeGlobal;
  const cwd = _g.process?.cwd?.() ?? '.';
  await assertStandardModuleInvariants(MODULE, {{
    folderName: '{tool}',
    moduleFolderPath: `${{cwd}}/src/modules/primitives/{tool}`,
  }});
}});

export async function runAll{safe}Tests(): Promise<void> {{
  let passed = 0;
  let failed = 0;
  for (const {{ label, fn }} of _checks) {{
    try {{
      await fn();
      passed += 1;
    }} catch (err) {{
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${{label}}\\n  ${{(err as Error).message}}`);
    }}
  }}
  // eslint-disable-next-line no-console
  console.log(`\\n{tool}: ${{passed}} passed, ${{failed}} failed`);
  if (failed > 0) {{
    throw new Error(`${{failed}} {tool} check(s) failed`);
  }}
}}

const _meta = (import.meta as unknown) as {{ main?: boolean }};
if (_meta && _meta.main) {{
  void runAll{safe}Tests();
}}
"""


def _placeholder_build_surface() -> str:
    return """// Generated by tools/scaffold_module.py.  Replace with real JSX.
import type { BuildSurfaceProps } from '@/modules/types';

function BuildSurface({ toolName }: BuildSurfaceProps) {
  return (
    <div>
      <h2>TODO — Build surface for {toolName}</h2>
    </div>
  );
}

export default BuildSurface;
"""


def _placeholder_result_renderer() -> str:
    return """// Generated by tools/scaffold_module.py.  Replace with real JSX.
// Receives ``{ payload }`` — the typed-detail endpoint response body.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ResultRenderer({ payload }: { payload: any }) {
  return (
    <div>
      <h2>TODO — Result renderer</h2>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </div>
  );
}

export default ResultRenderer;
"""


def _placeholder_preview_widget() -> str:
    return """// Generated by tools/scaffold_module.py.  Replace with real JSX.
import type { NodeRenderProps } from '@/components/build/lib/nodeRendererRegistry';

function PreviewWidget(_props: NodeRenderProps) {
  return <div>TODO — persisted-artifact preview</div>;
}

export default PreviewWidget;
"""


def _placeholder_ask_card() -> str:
    return """// Generated by tools/scaffold_module.py.  Replace with real JSX.
import type { CopilotMessage } from '@/types/copilot';

function AskCard({ message }: { message: CopilotMessage }) {
  return (
    <div>
      <h3>TODO — assistant card</h3>
      <p>{message.content}</p>
    </div>
  );
}

export default AskCard;
"""


def _placeholder_monitor_widget(widget_class: str) -> str:
    return f"""// Generated by tools/scaffold_module.py.  Replace with real JSX.
export function {widget_class}() {{
  return <div>TODO — Monitor widget</div>;
}}
"""


# -----------------------------------------------------------------------------
# Loader-index insertion
# -----------------------------------------------------------------------------


def insert_into_loader(tool: str) -> None:
    """Insert an import + ALL_PRIMITIVE_MODULES entry alphabetically."""
    text = LOADER_INDEX.read_text()
    if f"primitives/{tool}/module" in text:
        return  # already present
    import_line = f"import {{ MODULE as {tool} }} from './primitives/{tool}/module';"

    # Find the alphabetical insertion point in the import block.
    lines = text.split("\n")
    insert_at: Optional[int] = None
    last_module_import: Optional[int] = None
    for i, line in enumerate(lines):
        m = re.match(r"import \{ MODULE as (\w+) \} from '\./primitives/(\w+)/module';", line)
        if not m:
            continue
        last_module_import = i
        existing = m.group(2)
        if tool < existing and insert_at is None:
            insert_at = i
    if insert_at is None and last_module_import is not None:
        insert_at = last_module_import + 1
    if insert_at is None:
        raise SystemExit(
            "Could not find the primitive-import block in src/modules/index.ts"
        )
    lines.insert(insert_at, import_line)
    text = "\n".join(lines)

    # Insert into ALL_PRIMITIVE_MODULES array.  Find the alphabetical
    # spot in the array.
    array_lines = text.split("\n")
    in_array = False
    insert_at_arr: Optional[int] = None
    last_arr_entry: Optional[int] = None
    for i, line in enumerate(array_lines):
        stripped = line.strip()
        if "ALL_PRIMITIVE_MODULES" in line and "=" in line:
            in_array = True
            continue
        if not in_array:
            continue
        if stripped.startswith("];"):
            break
        m = re.match(r"(\w+),?", stripped)
        if not m:
            continue
        existing = m.group(1)
        last_arr_entry = i
        if tool < existing and insert_at_arr is None:
            insert_at_arr = i
    if insert_at_arr is None and last_arr_entry is not None:
        insert_at_arr = last_arr_entry + 1
    if insert_at_arr is None:
        raise SystemExit(
            "Could not find the ALL_PRIMITIVE_MODULES array in src/modules/index.ts"
        )
    indent = "  "
    array_lines.insert(insert_at_arr, f"{indent}{tool},")
    text = "\n".join(array_lines)

    LOADER_INDEX.write_text(text)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True, help="Backend-canonical tool name")
    parser.add_argument("--sub-agent", default="", help="Sub-agent name (documentation only)")
    parser.add_argument(
        "--category", default="snapshots", help="Manifest category (drives Library chip)"
    )
    parser.add_argument(
        "--display-name", default="", help="Title Case display name (defaults to tool slug)"
    )
    parser.add_argument(
        "--one-line-summary", default="TODO", help="One-line tool summary"
    )
    parser.add_argument(
        "--runtime-tier",
        default=None,
        help="Force a runtime tier (default: auto-detect from backend)",
    )
    parser.add_argument("--bespoke-build", action="store_true", help="Ship surfaces/BuildSurface.tsx")
    parser.add_argument(
        "--bespoke-result-renderer",
        default=None,
        help="Ship surfaces/ResultRenderer.tsx + set typedView to this kind",
    )
    parser.add_argument(
        "--bespoke-preview", action="store_true", help="Ship surfaces/PreviewWidget.tsx"
    )
    parser.add_argument(
        "--bespoke-monitor",
        default=None,
        help="Ship surfaces/monitor/<WidgetClass>.tsx with this widget id",
    )
    parser.add_argument(
        "--bespoke-ask", action="store_true", help="Ship surfaces/AskCard.tsx"
    )
    args = parser.parse_args()

    tool = args.tool
    if not re.match(r"^[a-z][a-z0-9_]*_tool$", tool):
        print(f"WARN: tool name '{tool}' doesn't match the conventional shape", file=sys.stderr)
    display = args.display_name or tool.removesuffix("_tool").replace("_", " ").title()

    runtime = args.runtime_tier or detect_runtime_tier(tool)
    print(f"runtime tier: {runtime}")

    cap_tiers: list[str] = []
    if args.bespoke_build:
        cap_tiers.append("custom_build_surface")
    if args.bespoke_result_renderer and "custom_build_surface" not in cap_tiers:
        cap_tiers.append("custom_build_surface")
    if args.bespoke_preview:
        cap_tiers.append("custom_preview_widget")
    if args.bespoke_monitor:
        cap_tiers.append("monitor_surface")
    if args.bespoke_ask:
        cap_tiers.append("ask_surface")

    folder = PRIMITIVES_ROOT / tool
    if folder.exists():
        print(f"FATAL: module folder already exists: {folder}", file=sys.stderr)
        return 1
    folder.mkdir(parents=True)
    (folder / "surfaces").mkdir(exist_ok=True)
    (folder / "__tests__").mkdir(exist_ok=True)

    # module.ts
    (folder / "module.ts").write_text(
        _module_ts(
            tool=tool,
            display_name=display,
            category=args.category,
            one_line_summary=args.one_line_summary.replace("'", "\\'"),
            runtime=runtime,
            bespoke_build=args.bespoke_build,
            bespoke_result_renderer=args.bespoke_result_renderer,
            bespoke_preview=args.bespoke_preview,
            bespoke_monitor=args.bespoke_monitor,
            bespoke_ask=args.bespoke_ask,
        )
    )

    # THESIS.md
    (folder / "THESIS.md").write_text(
        _thesis_md(
            tool=tool,
            display_name=display,
            category=args.category,
            one_line=args.one_line_summary,
            runtime=runtime,
            cap_tiers=cap_tiers,
        )
    )

    # Round-trip test
    (folder / "__tests__" / "module.spec.ts").write_text(_module_spec_test(tool))

    # Surface placeholders
    if args.bespoke_build:
        (folder / "surfaces" / "BuildSurface.tsx").write_text(_placeholder_build_surface())
    if args.bespoke_result_renderer:
        (folder / "surfaces" / "ResultRenderer.tsx").write_text(
            _placeholder_result_renderer()
        )
    if args.bespoke_preview:
        (folder / "surfaces" / "PreviewWidget.tsx").write_text(_placeholder_preview_widget())
    if args.bespoke_ask:
        (folder / "surfaces" / "AskCard.tsx").write_text(_placeholder_ask_card())
    if args.bespoke_monitor:
        monitor_dir = folder / "surfaces" / "monitor"
        monitor_dir.mkdir(exist_ok=True)
        widget_class = (
            "".join(p.capitalize() for p in args.bespoke_monitor.split("_")) + "Widget"
        )
        (monitor_dir / f"{widget_class}.tsx").write_text(
            _placeholder_monitor_widget(widget_class)
        )

    # Register in loader
    insert_into_loader(tool)

    print(f"\nscaffolded: {folder.relative_to(REPO_ROOT)}")
    print(f"  module.ts        → {folder.relative_to(REPO_ROOT)}/module.ts")
    print(f"  THESIS.md        → {folder.relative_to(REPO_ROOT)}/THESIS.md")
    print(f"  __tests__/       → round-trip spec")
    for f in sorted((folder / "surfaces").rglob("*.tsx")):
        print(f"  surface          → {f.relative_to(REPO_ROOT)}")
    print(f"\nnext: edit the placeholder JSX in surfaces/, then run:")
    print(f"  npm run test:modules")
    print(f"  make check-module-parity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
