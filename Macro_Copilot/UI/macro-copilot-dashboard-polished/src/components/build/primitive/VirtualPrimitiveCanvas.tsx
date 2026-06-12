// ============================================================================
// VirtualPrimitiveCanvas — Build canvas for a single-primitive Ask handoff.
// ----------------------------------------------------------------------------
// Phase R1.2.  When the user clicks "Open in Build" on an Ask answer, the
// chat encodes the supervisor turn's ``workspaceContext`` into
// ``/workspace?context=<encoded JSON>``.  BuildShell.SlugFreeShell detects
// that param and mounts this canvas — which:
//
//   1. Hands the raw context string to ``decodePrimitiveContext`` to pick
//      the most informative tool call.
//   2. Mounts the owning module's ``surfaces.buildExtended`` when the
//      module ships the dual-view contract (module-first dispatch — the
//      module owns the entire canvas: controls strip, fetch dispatch,
//      output rendering, error states).
//   3. Otherwise falls back per decode kind: the schema-driven
//      ``GenericPrimitiveBuilder`` for runnable tools, or the honest
//      ``UnsupportedKnownToolCanvas`` card for workflow-incompatible /
//      paused tools.
//
// This is a *virtual* canvas — no workspace is persisted, no slug exists.
//
// R6.3 — when the context carries MORE than one tool, BuildShell mounts
// ``MultiToolDagCanvas`` instead.  This canvas stays focused on the
// single-card path.
// ============================================================================

import { AlertCircle } from 'lucide-react';
import { decodePrimitiveContext } from './contextDecoder';
import { UnsupportedKnownToolCanvas } from './UnsupportedKnownToolCanvas';
import { GenericPrimitiveBuilder } from './GenericPrimitiveBuilder';
// Module-first dispatch: per-tool Build surfaces are owned by their
// module folders.  The page-shell minimality rule (FP12) forbids
// importing ``@/modules/primitives/<name>/`` directly from anything
// under ``src/components/{build,library,monitor,ask,layout}``, so the
// dispatcher below resolves the surface component via the module-spec
// lookup instead of direct per-tool imports.
import { getPrimitiveModule } from '@/modules';

// ----------------------------------------------------------------------------
// Component
// ----------------------------------------------------------------------------

type Props = {
  /** Raw value of the ``?context=`` URL param.  Already URI-encoded JSON. */
  contextParam: string;
  /** PR-B-β — true when the URL carried ``handoff=ask``.  Passed
   *  through to the module Build surface so it can choose to render a
   *  missing-param affordance vs silent defaults for Ask-handoff vs
   *  Library-blank invocations. */
  askHandoff?: boolean;
};

export function VirtualPrimitiveCanvas({
  contextParam,
  askHandoff = false,
}: Props) {
  const decoded = decodePrimitiveContext(contextParam);

  if (!decoded) {
    return <DecodeError contextParam={contextParam} />;
  }

  // ---- Module-first dispatch (rendering-density standard) -----------
  //
  // If the owning primitive module ships the dual-view extended Build
  // surface, mount IT and skip the per-kind fallbacks below.  The
  // module owns the entire canvas: controls strip, fetch dispatch,
  // output rendering, error states.  This canvas's single-tool
  // dispatch mounts the EXTENDED view; the compact view fires only
  // inside multi-tool DAG infrastructure (DagNodeBody).
  //
  // Any new tool that wants total ownership of its Build surface just
  // ships ``surfaces/BuildExtended.tsx`` + ``surfaces/BuildCompact.tsx``
  // + claims ``custom_build_surface`` — no edits to this file needed.
  const moduleForBuild = getPrimitiveModule(decoded.toolName);
  const ModuleBuildSurface = moduleForBuild?.surfaces?.buildExtended;
  if (ModuleBuildSurface) {
    return (
      <ModuleBuildSurface
        toolName={decoded.toolName}
        params={decoded.params}
        decoded={decoded}
        askHandoff={askHandoff}
      />
    );
  }

  // PR2 — runnable primitive without a module Build surface.  Mount the
  // schema-driven generic builder: fetches the ``ToolCard``, renders a
  // form from ``input_fields``, runs via ``POST /tools/{name}/run``.
  if (decoded.kind === 'generic_builder') {
    // PR-B-β — pass the STRUCTURED params so the builder can seed
    // ``series_spec`` / ``series_spec_list`` / ``multi_tenor``
    // controls from an Ask hand-off that included nested config.
    // ``decoded.paramsStructured`` is a superset of ``decoded.params``
    // (same scalar entries + any nested ones the decoder preserved).
    return (
      <GenericPrimitiveBuilder
        toolName={decoded.toolName}
        initialParams={decoded.paramsStructured}
      />
    );
  }

  // Stage 1 — workflow-incompatible: tool ships on the backend
  // (callable via MCP) but its output shape can't be lifted into
  // a ``Series`` / ``Panel`` artifact, so the generic builder's
  // ``POST /tools/{name}/run`` route would return the FastAPI
  // ``{ok: false, error: "..."}`` envelope.  PR1 — unsupported_known:
  // known but NOT runnable (manifest-only / paused).  Both render the
  // honest card with per-tool reason text (sourced via
  // ``unsupportedKnownReasonFor`` inside the canvas).
  return (
    <UnsupportedKnownToolCanvas
      toolName={decoded.toolName}
      params={decoded.params}
    />
  );
}

// ----------------------------------------------------------------------------
// Decode-error state
// ----------------------------------------------------------------------------

function DecodeError({ contextParam }: { contextParam: string }) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6">
      <div className="card flex max-w-[520px] items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            Could not decode workspace context
          </div>
          <div className="mt-1 truncate font-mono text-[10.5px] text-fg-muted">
            ?context={contextParam.slice(0, 64)}…
          </div>
          <div className="mt-2 text-[11.5px] leading-[1.5] text-fg-secondary">
            The link from Ask carries a tool context Build doesn't recognise
            yet.  Open the Ask answer again, or start a new analysis from the
            empty state below.
          </div>
        </div>
      </div>
    </div>
  );
}
