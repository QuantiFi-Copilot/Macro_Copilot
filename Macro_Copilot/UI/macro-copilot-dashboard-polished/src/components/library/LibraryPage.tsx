// ============================================================================
// LibraryPage — full-width tool catalogue
// ----------------------------------------------------------------------------
// Renders the 3-level hierarchy from the agreed plan:
//   Top tabs   : Primitives | Workflows | Operators (soon)
//   Agent strip: Rates Agent (live) | FX / Credit / ... (soon, dimmed)
//   Sub-strip  : All | Sovereign Bonds | OIS    ← from manifest.sub_agent_counts
//   Categories : All + 7 functional categories  ← manifest.category_counts
//   Search     : free-text across name/one_liner/related_tools/workflows
//   Grid       : ToolCardGrid
//   Drawer     : ToolDetailDrawer (right-slide on click)
//
// Reads the entire catalogue from one fetch (`/api/v1/library/manifest`),
// then filters client-side.  No hardcoded tool data anywhere — the
// manifest YAMLs are the source of truth.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import { useLibraryManifest } from '@/hooks/useLibraryManifest';
import type { ManifestTool } from '@/types/library';
import { LibraryHeader } from './LibraryHeader';
import { LibraryTabs } from './LibraryTabs';
import { AgentStrip } from './AgentStrip';
import { InstrumentStrip } from './InstrumentStrip';
import { CategoryChips } from './CategoryChips';
import { LibrarySearch } from './LibrarySearch';
import { ToolCardGrid } from './ToolCardGrid';
import { ToolDetailDrawer } from './ToolDetailDrawer';

export function LibraryPage() {
  const { data, isLoading, error } = useLibraryManifest();

  // Filter state — default to Rates Agent / All instruments / All
  // categories.  Activated agent must exist in the manifest (the
  // hook's loading state guards initial render before this matters).
  const [activeAgent, setActiveAgent] = useState<string>('rates_agent');
  const [activeSubAgent, setActiveSubAgent] = useState<string>('all');
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [drawerTool, setDrawerTool] = useState<ManifestTool | null>(null);

  // Reset sub-agent + category whenever the agent changes — otherwise
  // a stale "ois" filter could persist across an agent swap.
  useEffect(() => {
    setActiveSubAgent('all');
    setActiveCategory('all');
  }, [activeAgent]);

  if (error) return <FatalError error={error} />;
  if (isLoading || !data) return <Loading />;

  const agentManifest = data.agents[activeAgent];
  if (!agentManifest) return <FatalError error={new Error(`Unknown agent: ${activeAgent}`)} />;

  // After applying the sub-agent filter we recompute category counts
  // (so the chip row's "(N)" reflects what's actually available
  // within the current sub-agent).
  const subFilteredTools =
    activeSubAgent === 'all'
      ? agentManifest.tools
      : agentManifest.tools.filter((t) => t.sub_agent === activeSubAgent);

  const subFilteredCategoryCounts: Record<string, number> = {};
  for (const t of subFilteredTools) {
    if (!t.category) continue;
    subFilteredCategoryCounts[t.category] = (subFilteredCategoryCounts[t.category] ?? 0) + 1;
  }

  // After category, apply text search — pure client-side over the
  // small manifest payload.
  const categoryFiltered =
    activeCategory === 'all'
      ? subFilteredTools
      : subFilteredTools.filter((t) => t.category === activeCategory);

  const q = searchQuery.trim().toLowerCase();
  const visibleTools =
    q.length === 0
      ? categoryFiltered
      : categoryFiltered.filter((t) =>
          [
            t.name,
            t.implementation.tool_function,
            t.one_liner,
            ...t.related_tools,
            ...t.workflows,
          ]
            .join(' ')
            .toLowerCase()
            .includes(q),
        );

  // Resolver for the drawer's related-tool chips — looks up a
  // `tool_function` (e.g. "calculate_curve_spread_tool") across all
  // agents in the manifest.  Returns null when not found (cross-
  // agent or stale references render as disabled chips).
  const resolveRelated = (toolFnName: string): ManifestTool | null => {
    for (const agent of Object.values(data.agents)) {
      const found = agent.tools.find(
        (t) => t.implementation.tool_function === toolFnName,
      );
      if (found) return found;
    }
    return null;
  };

  return (
    <div className="h-full overflow-y-auto">
      <LibraryHeader
        primitiveCount={data.total_tools}
        workflowCount={2 /* hardcoded V1 — workflows not in manifest yet */}
        operatorCount={null}
      />

      <LibraryTabs
        active="primitives"
        primitiveCount={data.total_tools}
        workflowCount={2}
      />

      <AgentStrip
        activeAgentId={activeAgent}
        toolCounts={Object.fromEntries(
          Object.entries(data.agents).map(([id, m]) => [id, m.tool_count]),
        )}
        onSelect={setActiveAgent}
      />

      <InstrumentStrip
        active={activeSubAgent}
        counts={agentManifest.sub_agent_counts}
        totalCount={agentManifest.tool_count}
        onSelect={setActiveSubAgent}
      />

      <CategoryChips
        active={activeCategory}
        counts={subFilteredCategoryCounts}
        totalCount={subFilteredTools.length}
        onSelect={setActiveCategory}
      />

      <LibrarySearch
        value={searchQuery}
        onChange={setSearchQuery}
        placeholder={`Search ${categoryFiltered.length} tool${categoryFiltered.length === 1 ? '' : 's'} by name, methodology, related…`}
      />

      <ToolCardGrid tools={visibleTools} onOpen={setDrawerTool} />

      <ToolDetailDrawer
        tool={drawerTool}
        resolveRelated={resolveRelated}
        onOpenRelated={setDrawerTool}
        onClose={() => setDrawerTool(null)}
      />
    </div>
  );
}

// ----------------------------------------------------------------------------

function Loading() {
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="flex items-center gap-3 text-fg-muted">
        <Loader2 size={14} className="animate-spin text-lineage-300" />
        <span className="font-mono text-[12px] tracking-[0.02em]">
          Loading manifest…
        </span>
      </div>
    </div>
  );
}

function FatalError({ error }: { error: Error }) {
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-[480px] rounded-md border border-coral-400/30 bg-coral-400/[0.05] p-5">
        <div className="flex items-start gap-2.5">
          <AlertCircle size={14} className="mt-0.5 shrink-0 text-coral-300" />
          <div>
            <p className="font-mono text-[10px] font-medium uppercase tracking-[0.16em] text-coral-300">
              MANIFEST UNAVAILABLE
            </p>
            <p className="mt-1.5 text-[13px] leading-[1.55] text-coral-300/85">
              {error.message}
            </p>
            <p className="mt-3 text-[11px] leading-[1.5] text-fg-muted">
              The Library reads from{' '}
              <span className="font-mono">/api/v1/library/manifest</span>. Make
              sure the API server is running.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
