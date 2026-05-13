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
//   2. Calls the matching typed-detail endpoint on the rates API (the
//      same endpoint Monitor + the deleted WorkspacePage used).
//   3. Renders the result through the matching primitive view
//      (SpreadPrimitiveView, CrossMarketPrimitiveView, …).
//
// This is a *virtual* canvas — no workspace is persisted, no slug exists.
// Until the backend ships supervisor-side workspace persistence (Phase R5),
// this is the only Ask→Build handoff path for single-primitive calls and
// the loop closes entirely on the frontend.
//
// R6.3 — when the context carries MORE than one typed primitive, BuildShell
// mounts ``MultiPrimitiveCanvas`` instead.  This canvas stays focused on
// the single-card path; the fetch dispatcher lives in ``fetchDispatcher.ts``
// so both canvases share it.
// ============================================================================

import { useEffect, useState } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  decodePrimitiveContext,
  type PrimitiveViewKind,
} from './contextDecoder';
import {
  dispatchFetch,
  isTypedPrimitive,
  type DecodedTypedPrimitive,
  type Payload,
} from './fetchDispatcher';
import { paramSpecsFor, resolveParamValue } from './paramSpecs';
import { PrimitiveParamControls } from './PrimitiveParamControls';
import { SpreadPrimitiveView } from './SpreadPrimitiveView';
import { CrossMarketPrimitiveView } from './CrossMarketPrimitiveView';
import { ButterflyPrimitiveView } from './ButterflyPrimitiveView';
import { YieldPrimitiveView } from './YieldPrimitiveView';
import { RegimePrimitiveView } from './RegimePrimitiveView';
import { ScannerPrimitiveView } from './ScannerPrimitiveView';
import { ForwardPrimitiveView } from './ForwardPrimitiveView';
import { UnsupportedKnownToolCanvas } from './UnsupportedKnownToolCanvas';

// ----------------------------------------------------------------------------
// Component
// ----------------------------------------------------------------------------

type Props = {
  /** Raw value of the ``?context=`` URL param.  Already URI-encoded JSON. */
  contextParam: string;
};

export function VirtualPrimitiveCanvas({ contextParam }: Props) {
  const decoded = decodePrimitiveContext(contextParam);
  const navigate = useNavigate();
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // R6.2 — fold the param-spec defaults into ``decoded.params`` before
  // anything else reads it.  When the user clicks "Open in Build" on
  // the Library with no params (the common case), the URL only carries
  // ``{tool: "calculate_curve_spread_tool", params: {}}`` — without
  // this fold, the first fetch would land with all-empty params and
  // the typed-detail endpoint would reject it.  After the fold the
  // dispatchFetch call below + the PrimitiveParamControls component
  // both see a fully-populated params dict matching the spec defaults.
  const effectiveParams: Record<string, string> = {};
  if (decoded && isTypedPrimitive(decoded)) {
    for (const spec of paramSpecsFor(decoded.kind)) {
      effectiveParams[spec.key] = resolveParamValue(spec, decoded.params);
    }
    // Carry through any URL-supplied params the spec list doesn't
    // declare (the typed-detail endpoints accept e.g. ``field_name``
    // even though the spec list doesn't expose a control for it).
    for (const [k, v] of Object.entries(decoded.params)) {
      if (!(k in effectiveParams)) effectiveParams[k] = v;
    }
  }

  // Mutate the URL when the user picks a new value in the controls
  // strip.  We re-encode ``?context=`` with the same toolName + the
  // updated params dict — the canvas's decode-then-fetch effect picks
  // it up on the next render and re-runs the fetch with the new
  // arguments.  Bookmark / share / refresh stay correct because the
  // URL is still the source of truth.
  const handleParamChange = (key: string, value: string) => {
    if (!decoded || !isTypedPrimitive(decoded)) return;
    const nextParams = { ...effectiveParams, [key]: value };
    const nextCtx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: decoded.toolName, params: nextParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${nextCtx}`, { replace: true });
  };

  // R6.1 — when the decoded tool is a rich-model primitive (PCA /
  // rolling regression / attribution / half-life / beta-adjusted-
  // spread), short-circuit to ``?builder=<toolName>`` so the standalone
  // playground mounts with the user's params pre-filled.  This is the
  // half of the Library → Build bridge that used to drop into the
  // orange "Could not decode" card.
  useEffect(() => {
    if (decoded?.kind !== 'builder') return;
    const qs = new URLSearchParams();
    qs.set('builder', decoded.toolName);
    for (const [k, v] of Object.entries(decoded.params)) {
      qs.set(k, v);
    }
    navigate(`/workspace?${qs.toString()}`, { replace: true });
  }, [decoded, navigate]);

  useEffect(() => {
    if (!decoded || !isTypedPrimitive(decoded)) {
      setPayload(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    // The forward placeholder doesn't fetch; mount the view directly.
    if (decoded.kind === 'forward') {
      setPayload({ kind: 'forward', data: null });
      setIsLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setPayload(null);
    // Pass effectiveParams (defaults folded in) so the first fetch
    // lands with sensible values rather than the empty params Library
    // sends.
    dispatchFetch({ ...decoded, params: effectiveParams })
      .then((p) => {
        if (cancelled) return;
        setPayload(p);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [decoded?.kind, decoded?.toolName, JSON.stringify(effectiveParams)]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!decoded) {
    return <DecodeError contextParam={contextParam} />;
  }
  // Builder redirects through the effect above; show a tight loader
  // for the single frame the navigate() lands.
  if (decoded.kind === 'builder') {
    return <BuilderRedirectingCanvas toolName={decoded.toolName} />;
  }
  // PR1 — known-backend tool with no Build renderer yet.  Render an
  // explicit "unsupported_known" card instead of dropping the user
  // into the orange decode-error path or the empty shell.  This is
  // the explicit-state replacement the user audit asked for.
  if (decoded.kind === 'unsupported_known') {
    return (
      <UnsupportedKnownToolCanvas
        toolName={decoded.toolName}
        params={decoded.params}
      />
    );
  }

  // Typed primitive view: render the params strip + the body together.
  // R6.2 — every typed view now has user-editable dropdowns at the top.
  const specs = paramSpecsFor(decoded.kind);
  let body: React.ReactNode;
  if (error) {
    body = <FetchError decoded={decoded} message={error} />;
  } else if (isLoading || !payload) {
    body = <LoadingCanvas kind={decoded.kind} />;
  } else {
    body = <PrimitiveDispatcher payload={payload} />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PrimitiveParamControls
        specs={specs}
        current={effectiveParams}
        isLoading={isLoading}
        onChange={handleParamChange}
      />
      <div className="min-h-0 flex-1 overflow-hidden">{body}</div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// View dispatcher
// ----------------------------------------------------------------------------

function PrimitiveDispatcher({ payload }: { payload: Payload }) {
  switch (payload.kind) {
    case 'spread':
      return <SpreadPrimitiveView payload={payload.data} />;
    case 'cross_market':
      return <CrossMarketPrimitiveView payload={payload.data} />;
    case 'butterfly':
      return <ButterflyPrimitiveView payload={payload.data} />;
    case 'yield':
      return <YieldPrimitiveView payload={payload.data} />;
    case 'regime':
      return <RegimePrimitiveView payload={payload.data} />;
    case 'scanner':
      return <ScannerPrimitiveView payload={payload.data} />;
    case 'forward':
      return <ForwardPrimitiveView />;
  }
}

// ----------------------------------------------------------------------------
// Loading + error states
// ----------------------------------------------------------------------------

function LoadingCanvas({ kind }: { kind: PrimitiveViewKind }) {
  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center gap-3 px-6">
      <Loader2 size={16} className="animate-spin text-ice-300" />
      <span className="text-[12px] text-fg-muted">
        Loading {prettyKind(kind)} from rates API…
      </span>
    </div>
  );
}

function BuilderRedirectingCanvas({ toolName }: { toolName: string }) {
  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center gap-3 px-6">
      <Loader2 size={16} className="animate-spin text-ice-300" />
      <span className="text-[12px] text-fg-muted">
        Opening builder for{' '}
        <span className="font-mono text-fg-secondary">{toolName}</span>…
      </span>
    </div>
  );
}

function FetchError({
  decoded,
  message,
}: {
  decoded: DecodedTypedPrimitive;
  message: string;
}) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6">
      <div className="card flex max-w-[520px] items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-coral-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            Could not load {prettyKind(decoded.kind)}
          </div>
          <div className="mt-1 font-mono text-[10.5px] text-fg-muted">
            {decoded.toolName}
          </div>
          <div className="mt-2 text-[11.5px] leading-[1.5] text-fg-secondary">
            {message}
          </div>
        </div>
      </div>
    </div>
  );
}

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

function prettyKind(kind: PrimitiveViewKind): string {
  switch (kind) {
    case 'spread':
      return 'curve spread';
    case 'cross_market':
      return 'cross-market spread';
    case 'butterfly':
      return 'butterfly';
    case 'yield':
      return 'yield level';
    case 'regime':
      return 'curve regime';
    case 'scanner':
      return 'scanner';
    case 'forward':
      return 'OIS forward';
  }
}
