// ============================================================================
// ToolsCataloguePage  (route: /tools)
// ----------------------------------------------------------------------------
// Discoverable grid of every primitive registered in
// rates_agent.workflows.rates_primitive_resolver.  Click a tile to open the
// 4-tab InspectionPanel.  Filter by domain.
// ============================================================================

import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Wrench, AlertCircle, ArrowUpRight } from 'lucide-react';
import { useTools, useTool } from '@/hooks/useWorkflows';
import { InspectionPanel } from './InspectionPanel';
import { cn } from '@/utils/cn';

type DomainFilter = 'all' | 'sovereign_bonds' | 'ois';

export function ToolsCataloguePage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useTools();
  const [domain, setDomain] = useState<DomainFilter>('all');
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedTool = searchParams.get('tool');
  const { data: toolDetail } = useTool(selectedTool);

  const openInWorkspace = (toolName: string) => {
    navigate(`/workspace?tool=primitive&name=${encodeURIComponent(toolName)}`);
  };

  const filtered = useMemo(() => {
    if (!data) return [];
    if (domain === 'all') return data;
    return data.filter((t) => t.domain === domain);
  }, [data, domain]);

  const closePanel = () => {
    const next = new URLSearchParams(searchParams);
    next.delete('tool');
    setSearchParams(next, { replace: true });
  };

  const openTool = (toolName: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('tool', toolName);
    setSearchParams(next, { replace: false });
  };

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-[1080px]">
        <PageHeader
          domain={domain}
          onDomainChange={setDomain}
          totalCount={data?.length ?? 0}
          shownCount={filtered.length}
        />

        {error && (
          <div className="card mt-4 flex items-start gap-3 px-4 py-3 text-coral-300">
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <div className="text-[12px]">
              <div className="font-semibold">Failed to load tools catalogue</div>
              <div className="mt-1 text-fg-secondary">{error.message}</div>
            </div>
          </div>
        )}

        {isLoading && (
          <div className="card mt-6 flex h-[320px] items-center justify-center text-[12px] text-fg-muted">
            Loading tools catalogue…
          </div>
        )}

        {!isLoading && !error && (
          <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((t) => (
              <ToolTile
                key={t.tool_name}
                toolName={t.tool_name}
                domain={t.domain}
                category={t.category}
                description={t.description}
                onClick={() => openTool(t.tool_name)}
                onOpenInWorkspace={() => openInWorkspace(t.tool_name)}
              />
            ))}
          </div>
        )}

        {!isLoading && !error && filtered.length === 0 && (
          <div className="card mt-6 flex h-[200px] items-center justify-center text-[12px] text-fg-muted">
            No tools in this filter.
          </div>
        )}
      </div>

      <InspectionPanel
        open={!!selectedTool}
        onClose={closePanel}
        tool={toolDetail}
        onOpenInWorkspace={
          selectedTool ? () => openInWorkspace(selectedTool) : undefined
        }
      />
    </div>
  );
}

// ----------------------------------------------------------------------------

function PageHeader({
  domain,
  onDomainChange,
  totalCount,
  shownCount,
}: {
  domain: DomainFilter;
  onDomainChange: (d: DomainFilter) => void;
  totalCount: number;
  shownCount: number;
}) {
  const filters: { id: DomainFilter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'sovereign_bonds', label: 'Sovereign' },
    { id: 'ois', label: 'OIS' },
  ];
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        <p className="kicker text-fg-muted">Library</p>
        <h1 className="mt-1 text-[20px] font-semibold tracking-[-0.01em] text-fg-primary">
          Tools
        </h1>
        <p className="mt-1.5 text-[12.5px] text-fg-secondary">
          Every deterministic primitive the rates agent calls.  Click a tile
          to inspect inputs, outputs, methodology, and read-only conventions.
        </p>
      </div>
      <div className="flex items-center gap-3">
        <div className="text-right text-[10.5px] uppercase tracking-[0.12em] text-fg-faint">
          <div>{shownCount} of {totalCount}</div>
          <div className="text-fg-muted">shown</div>
        </div>
        <div className="flex rounded-lg border border-line-soft bg-white/[0.012] p-1">
          {filters.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => onDomainChange(f.id)}
              className={cn(
                'rounded-md px-3 py-1 text-[11.5px] font-medium transition-colors',
                domain === f.id
                  ? 'bg-white/[0.06] text-fg-primary'
                  : 'text-fg-muted hover:text-fg-secondary',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ToolTile({
  toolName,
  domain,
  category,
  description,
  onClick,
  onOpenInWorkspace,
}: {
  toolName: string;
  domain: string;
  category?: string | null;
  description: string;
  onClick: () => void;
  onOpenInWorkspace: () => void;
}) {
  const shortDescription = description.split('\n').slice(0, 2).join(' ').slice(0, 200);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick();
        }
      }}
      className="group flex cursor-pointer flex-col gap-3 rounded-xl border border-line-soft bg-white/[0.014] px-4 py-4 text-left transition-all duration-150 ease-sleek hover:border-ice-400/30 hover:bg-ice-500/[0.04]"
    >
      <div className="flex items-start gap-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line-soft bg-gradient-to-br from-ice-500/15 to-ice-700/15 text-ice-300 group-hover:border-ice-400/30">
          <Wrench size={14} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[9.5px] font-medium uppercase tracking-[0.1em] text-fg-muted">
              {domain === 'sovereign_bonds' ? 'sov' : domain}
            </span>
            {category && (
              <span className="text-[9.5px] uppercase tracking-[0.1em] text-fg-faint">
                {category.replace(/_/g, ' ')}
              </span>
            )}
          </div>
          <p className="mt-1 truncate text-[12.5px] font-semibold text-fg-primary">
            {toolName}
          </p>
        </div>
      </div>
      <p className="line-clamp-3 text-[11.5px] leading-[1.5] text-fg-secondary">
        {shortDescription}
      </p>
      <div className="mt-1 flex items-center justify-between gap-2 border-t border-line-subtle pt-2.5">
        <span className="text-[10.5px] text-fg-faint">Click for inspection</span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpenInWorkspace();
          }}
          className="flex items-center gap-1 rounded-md border border-ice-400/20 bg-ice-500/10 px-2 py-1 text-[10.5px] font-semibold text-ice-200 transition-colors hover:border-ice-400/40 hover:bg-ice-500/20"
        >
          Open in workspace
          <ArrowUpRight size={10} />
        </button>
      </div>
    </div>
  );
}
