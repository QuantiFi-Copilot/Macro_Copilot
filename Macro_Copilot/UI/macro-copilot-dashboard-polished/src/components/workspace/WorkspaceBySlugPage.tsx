// ============================================================================
// WorkspaceBySlugPage
// ----------------------------------------------------------------------------
// Phase 0 PR 10 — minimal slug-driven renderer for persistent workspaces.
//
// Route: /workspace/:slug
//
// Reads the slug from the URL, fetches the workspace + DAG + per-node
// artifact summaries via workspaceApi.getWorkspace, and renders a
// terse list of nodes plus a methodology card.  Runs a parallel
// /replay?mode=current call so the page can flag YAML drift inline.
//
// Deliberately stub — Phase 3 is the redesign.  No sparklines, no
// graph viz, no editing.  The job here is to PROVE the persistence
// substrate works visually and that the URL is stable.
//
// The existing query-param WorkspacePage at /workspace stays
// unchanged.  Two pages, two URL shapes:
//   /workspace?tool=spread&...       -> WorkspacePage (existing)
//   /workspace/:slug                 -> WorkspaceBySlugPage (new)
// ============================================================================

import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { AlertCircle, CheckCircle2, GitCommit, RefreshCw } from 'lucide-react';
import {
  getWorkspace,
  replayWorkspace,
  type WorkspaceDetail,
  type WorkspaceReplay,
} from '@/services/workspaceApi';

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export function WorkspaceBySlugPage() {
  const { slug } = useParams<{ slug: string }>();
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [replay, setReplay] = useState<WorkspaceReplay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!slug) {
      setError('No workspace slug in URL.');
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Parallel — detail for the layout, replay(current) for the
    // drift indicator.  An error on the drift call should NOT block
    // the detail render; it just disables the drift surface.
    Promise.all([
      getWorkspace(slug),
      replayWorkspace(slug, 'current').catch(() => null),
    ])
      .then(([d, r]) => {
        if (cancelled) return;
        setDetail(d);
        setReplay(r);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e?.message ?? e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (!slug) {
    return (
      <div className="p-8">
        <ErrorBlock message="No workspace slug provided in URL." />
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-zinc-400">
        <RefreshCw className="h-4 w-4 animate-spin" />
        Loading workspace…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <ErrorBlock
          message={`Could not load workspace ${slug}: ${error}`}
        />
      </div>
    );
  }

  if (!detail) {
    return null;
  }

  return (
    <div className="flex flex-col gap-6 overflow-y-auto p-8">
      <Header detail={detail} replay={replay} />
      <NodeList detail={detail} />
      {replay && <MethodologyCard replay={replay} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Header({
  detail,
  replay,
}: {
  detail: WorkspaceDetail;
  replay: WorkspaceReplay | null;
}) {
  const driftCount = replay?.methodology_diffs.length ?? 0;
  return (
    <div className="border-b border-zinc-800 pb-4">
      <h1 className="text-2xl font-semibold text-zinc-100">
        {detail.name ?? 'Untitled workspace'}
      </h1>
      <div className="mt-2 flex items-center gap-3 text-xs text-zinc-400">
        <code className="rounded bg-zinc-900 px-2 py-0.5 font-mono">
          /{detail.slug}
        </code>
        <span>•</span>
        <code className="font-mono">
          DAG {detail.dag_hash.slice(0, 12)}…
        </code>
        <span>•</span>
        <span>{detail.nodes.length} nodes</span>
        {replay && (
          <>
            <span>•</span>
            {driftCount === 0 ? (
              <span className="flex items-center gap-1 text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                methodology in sync
              </span>
            ) : (
              <span className="flex items-center gap-1 text-amber-400">
                <AlertCircle className="h-3.5 w-3.5" />
                {driftCount} methodology drift
                {driftCount === 1 ? '' : 's'}
              </span>
            )}
            {replay.commit_differs && (
              <span className="flex items-center gap-1 text-amber-400">
                <GitCommit className="h-3.5 w-3.5" />
                code revision changed
              </span>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function NodeList({ detail }: { detail: WorkspaceDetail }) {
  return (
    <div className="rounded-md border border-zinc-800">
      <div className="border-b border-zinc-800 px-4 py-2 text-xs uppercase tracking-wide text-zinc-500">
        DAG nodes
      </div>
      <ul className="divide-y divide-zinc-800">
        {detail.nodes.map((node) => (
          <li key={node.node_id} className="flex items-start gap-4 px-4 py-3">
            <div className="w-16 flex-none font-mono text-xs text-zinc-500">
              {node.node_id}
            </div>
            <div className="flex-1">
              <div className="text-sm text-zinc-100">
                <span className="font-medium">{node.name}</span>
                <span className="ml-2 rounded bg-zinc-900 px-2 py-0.5 text-xs text-zinc-400">
                  {node.kind}
                </span>
              </div>
              {node.artifact ? (
                <div className="mt-1 text-xs text-zinc-400">
                  {node.artifact.artifact_type}
                  {node.artifact.units && ` · ${node.artifact.units}`}
                  {typeof node.artifact.row_count === 'number' &&
                    ` · ${node.artifact.row_count} rows`}
                  <code className="ml-2 font-mono text-zinc-500">
                    {node.artifact.hash.slice(0, 12)}…
                  </code>
                </div>
              ) : (
                <div className="mt-1 text-xs text-zinc-500">
                  no artifact recorded for this node
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MethodologyCard({ replay }: { replay: WorkspaceReplay }) {
  if (replay.methodology_version_ids.length === 0) {
    return null;
  }
  return (
    <div className="rounded-md border border-zinc-800">
      <div className="border-b border-zinc-800 px-4 py-2 text-xs uppercase tracking-wide text-zinc-500">
        Methodology
      </div>
      <ul className="divide-y divide-zinc-800">
        {replay.methodology_version_ids.map((vid) => {
          const drift = replay.methodology_diffs.find(
            (d) => d.original_version_id === vid,
          );
          return (
            <li
              key={vid}
              className="flex items-center justify-between px-4 py-3 text-sm"
            >
              <span className="font-mono text-zinc-400">version #{vid}</span>
              {drift ? (
                <span className="flex items-center gap-2 text-amber-400">
                  <AlertCircle className="h-3.5 w-3.5" />
                  on-disk drift on {drift.fields_changed.join(', ') || 'unknown fields'}
                </span>
              ) : (
                <span className="flex items-center gap-2 text-emerald-400">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  in sync
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {replay.notes.length > 0 && (
        <div className="border-t border-zinc-800 px-4 py-2 text-xs text-zinc-500">
          {replay.notes.map((n, i) => (
            <div key={i}>· {n}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-red-800/50 bg-red-950/30 p-4 text-sm text-red-200">
      <AlertCircle className="mt-0.5 h-4 w-4 flex-none" />
      <div>{message}</div>
    </div>
  );
}
