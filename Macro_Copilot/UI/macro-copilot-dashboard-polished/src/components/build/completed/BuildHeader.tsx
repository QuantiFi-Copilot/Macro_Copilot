// ============================================================================
// BuildHeader — title + actions row at the top of a completed workspace.
// ----------------------------------------------------------------------------
// Matches the top of Mockup B/C.  Three regions:
//
//   - Breadcrumb-style label: ``Workspace · <slug-derived name>``
//   - Title + status pill (``Completed`` / ``Editing`` / etc.)
//   - Action cluster: Save / Share / Run all
//
// Save / Share / Run all are visual affordances that fire toast-style
// placeholders.  Real wiring depends on backend endpoints that are
// not yet shipped (a preset-save store, share-link minter, workflow
// re-run route).  The header is rendered so the visual register
// stays consistent across the surface; the toast copy is honest
// about the deferral.
// ============================================================================

import { useState } from 'react';
import { Play, Save, Share2 } from 'lucide-react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { cn } from '@/utils/cn';

type Props = {
  detail: WorkspaceDetail;
  /** Visual status pill — derived by the parent. */
  status: 'completed' | 'editing' | 'paused';
};

export function BuildHeader({ detail, status }: Props) {
  const title = detail.name?.trim() || prettyFromSlug(detail.slug);

  return (
    <header className="flex shrink-0 items-start justify-between gap-4 border-b border-line-subtle px-6 py-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-[0.16em] text-fg-faint">
          <span>Workspace</span>
          <span aria-hidden>·</span>
          <span className="font-mono normal-case tracking-normal text-fg-muted">
            /{detail.slug}
          </span>
        </div>
        <div className="mt-1.5 flex items-center gap-3">
          <h1 className="truncate text-[18px] font-semibold tracking-[-0.012em] text-fg-primary">
            {title}
          </h1>
          <StatusPill status={status} />
        </div>
        <p className="mt-1 text-[11px] text-fg-muted">
          Updated {formatRelative(detail.updated_at)} · {detail.nodes.length}{' '}
          {detail.nodes.length === 1 ? 'node' : 'nodes'} ·{' '}
          <span className="font-mono">DAG {detail.dag_hash.slice(0, 10)}…</span>
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <ActionButton icon={Save} label="Save" />
        <ActionButton icon={Share2} label="Share" />
        <ActionButton icon={Play} label="Run all" primary />
      </div>
    </header>
  );
}

function StatusPill({ status }: { status: Props['status'] }) {
  const map = {
    completed: {
      label: 'Completed',
      cls: 'border-emerald-400/30 bg-emerald-500/10 text-emerald-200',
    },
    editing: {
      label: 'Editing',
      cls: 'border-ice-400/30 bg-ice-500/10 text-ice-200',
    },
    paused: {
      label: 'Paused',
      cls: 'border-amber-400/30 bg-amber-500/10 text-amber-200',
    },
  } as const;
  const { label, cls } = map[status];
  return (
    <span
      className={cn(
        'rounded-sm border px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-[0.16em]',
        cls,
      )}
    >
      {label}
    </span>
  );
}

function ActionButton({
  icon: Icon,
  label,
  primary,
}: {
  icon: typeof Save;
  label: string;
  primary?: boolean;
}) {
  const [toast, setToast] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        setToast(true);
        setTimeout(() => setToast(false), 1400);
      }}
      title={`${label} — coming soon`}
      className={cn(
        'relative flex h-8 items-center gap-1.5 rounded-md border px-3 text-[11.5px] font-medium transition-colors',
        primary
          ? 'border-transparent composer-send-active text-ink-900'
          : 'border-line-soft bg-white/[0.012] text-fg-secondary hover:border-ice-400/30 hover:text-fg-primary',
      )}
    >
      <Icon size={11} strokeWidth={2} />
      <span>{label}</span>
      {toast && (
        <span className="pointer-events-none absolute -bottom-7 right-0 whitespace-nowrap rounded-sm border border-line-soft bg-ink-900/95 px-2 py-1 text-[10px] text-fg-secondary shadow-lg">
          {label} — coming soon
        </span>
      )}
    </button>
  );
}

function prettyFromSlug(slug: string): string {
  // Strip the trailing 8-char uuid suffix the substrate appends.
  const cleaned = slug.replace(/-[0-9a-f]{8}$/, '');
  return cleaned
    .split('-')
    .map((tok) => tok.charAt(0).toUpperCase() + tok.slice(1))
    .join(' ');
}

function formatRelative(isoString: string): string {
  try {
    const d = new Date(isoString);
    const diffMin = Math.round((Date.now() - d.getTime()) / 60_000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin} min ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toISOString().slice(0, 10);
  } catch {
    return 'recently';
  }
}
