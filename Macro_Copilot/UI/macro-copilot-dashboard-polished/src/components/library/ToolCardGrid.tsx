// ============================================================================
// ToolCardGrid — bento-style 3-column grid of ToolCards
// ----------------------------------------------------------------------------
// Pure layout — handles empty / loading / error states; each card
// click opens the parent's detail drawer.
// ============================================================================

import { Sparkles } from 'lucide-react';
import type { ManifestTool } from '@/types/library';
import { ToolCard } from './ToolCard';

type Props = {
  tools: ManifestTool[];
  onOpen: (tool: ManifestTool) => void;
};

export function ToolCardGrid({ tools, onOpen }: Props) {
  if (tools.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 px-8 py-20 text-center">
        <Sparkles size={16} className="text-fg-faint" />
        <p className="text-[13px] text-fg-secondary">
          No tools match the current filter.
        </p>
        <p className="font-mono text-[10.5px] tracking-[0.04em] text-fg-faint">
          Try clearing the search or picking a different category.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 px-8 pb-10 lg:grid-cols-2 xl:grid-cols-3">
      {tools.map((t) => (
        <ToolCard key={t.name} tool={t} onOpen={() => onOpen(t)} />
      ))}
    </div>
  );
}
