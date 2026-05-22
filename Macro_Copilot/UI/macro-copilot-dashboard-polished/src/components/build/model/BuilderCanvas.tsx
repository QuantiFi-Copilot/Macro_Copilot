// ============================================================================
// BuilderCanvas — Build-shell entry point for the standalone model builder.
// ----------------------------------------------------------------------------
// Phase R4.  When the user lands on ``/workspace?builder=<tool_name>`` —
// either by clicking a primitive tile on the Build empty state or by
// hitting "Open in builder" from a Library card — BuildShell mounts this
// canvas in the centre column.
//
// The canvas is a thin wrapper that:
//   - validates the tool name against the model registry (every primitive
//     with a ``getModelMetadata`` entry has rich controls + a bespoke
//     renderer; everything else falls back to the auto-renderer)
//   - extracts any additional URL params (e.g. ``curve_family=UST``) and
//     hands them to the page as ``initialParams`` so deep-links pre-fill
//     the form
//   - renders ``ModelWorkspacePage`` — the 3-column playground restored
//     from git history and reshelled under ``components/build/model/``
//
// Unrecognised / missing tool name → "no such builder" caption with a
// link back to the Build empty state.  Doesn't 404 the page.
// ============================================================================

import { AlertCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ModelWorkspacePage } from './ModelWorkspacePage';

type Props = {
  /** Raw ``?builder=`` URL param.  Empty string and undefined both
   *  surface the "missing tool" caption. */
  toolName: string | null;
  /** Remaining URL search params (every key/value except ``builder``)
   *  forwarded to the page as initial form values. */
  initialParams: Record<string, string>;
};

export function BuilderCanvas({ toolName, initialParams }: Props) {
  if (!toolName) {
    return <MissingToolCanvas />;
  }
  return (
    <ModelWorkspacePage
      toolName={toolName}
      initialParams={initialParams}
    />
  );
}

function MissingToolCanvas() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6">
      <div className="card flex max-w-[480px] items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            No builder tool specified
          </div>
          <div className="mt-2 text-[11.5px] leading-[1.5] text-fg-secondary">
            Open a tool from the Library, or pick a starter category from the{' '}
            <Link
              to="/workspace"
              className="text-ice-200 underline-offset-2 hover:underline"
            >
              Build empty state
            </Link>
            .
          </div>
        </div>
      </div>
    </div>
  );
}
