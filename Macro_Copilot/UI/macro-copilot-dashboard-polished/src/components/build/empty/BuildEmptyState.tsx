// ============================================================================
// BuildEmptyState — the Build canvas when no workspace is open.
// ----------------------------------------------------------------------------
// Matches Mockup A: editorial display headline with a soft ambient orb
// behind it, 6-tile category grid in the polished ``research-card``
// register, an OR-separator, and the composer pinned beneath.
//
// User interactions:
//   - Click a category tile → seed the composer with the tile's
//     prompt + focus the composer.  No automatic send; the user
//     can edit before hitting send.
//   - Type + send → ``onSend(content)`` fires.  Parent decides
//     what to do (BuildShell hands it to ``CopilotContext.sendMessage``
//     and transitions to BuildBuilding).
// ============================================================================

import { useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { BUILD_CATEGORIES } from './categoryDefinitions';
import { CategoryTile } from './CategoryTile';
import {
  EmptyStateComposer,
  type EmptyStateComposerHandle,
} from './EmptyStateComposer';
import type { BuildEmptyCategory } from '../lib/buildTypes';

type Props = {
  onSend: (content: string) => void;
  composerDisabled?: boolean;
};

export function BuildEmptyState({ onSend, composerDisabled }: Props) {
  const composerRef = useRef<EmptyStateComposerHandle | null>(null);
  const navigate = useNavigate();

  const handleTile = (category: BuildEmptyCategory) => {
    // Phase R4 — tiles tagged with ``builderTool`` deep-link into the
    // standalone model builder canvas instead of seeding the composer.
    // The other tiles keep the original behaviour of dropping a prompt
    // into the centre composer.
    if (category.builderTool) {
      navigate(`/workspace?builder=${encodeURIComponent(category.builderTool)}`);
      return;
    }
    composerRef.current?.seed(category.promptSeed);
  };

  return (
    <div className="ambient-grid relative flex h-full min-h-0 flex-col overflow-y-auto">
      {/* Soft ambient orb behind the headline — gives the empty state
          a single editorial focal point without becoming decorative.
          Positioned absolutely so it doesn't push the layout. */}
      <span
        aria-hidden
        className="ambient-orb pointer-events-none"
        style={{
          ['--orb-size' as string]: '720px',
          top: '-180px',
          left: '50%',
          transform: 'translateX(-50%)',
          opacity: 0.6,
        }}
      />

      <div className="relative mx-auto flex w-full max-w-[920px] flex-col gap-10 px-6 py-12">
        <Header />

        <section>
          <div className="mb-3 flex items-baseline justify-between px-1">
            <p className="kicker">Start somewhere</p>
            <span className="text-[10px] text-fg-faint">
              {BUILD_CATEGORIES.length} categories
            </span>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {BUILD_CATEGORIES.map((c) => (
              <CategoryTile key={c.id} category={c} onSelect={handleTile} />
            ))}
          </div>
        </section>

        <Separator />

        <section className="flex flex-col gap-2">
          <EmptyStateComposer
            ref={composerRef}
            onSend={onSend}
            disabled={composerDisabled}
          />
        </section>
      </div>
    </div>
  );
}

function Header() {
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <span className="kicker text-fg-muted">Workspace</span>
      <h1 className="font-serif-display text-[44px] font-light leading-[1.05] tracking-[-0.02em] text-fg-primary">
        What would you like to{' '}
        <span className="italic">build</span>?
      </h1>
      <p className="max-w-[520px] text-[13px] leading-[1.55] text-fg-secondary">
        Build any macro analysis. Start from a prompt,{' '}
        <br className="hidden sm:inline" />
        extend existing results, or explore examples.
      </p>
    </div>
  );
}

function Separator() {
  return (
    <div
      role="separator"
      aria-hidden
      className="flex items-center gap-3 px-1 text-[10px] uppercase tracking-[0.18em] text-fg-faint"
    >
      <span className="h-px flex-1 bg-line-subtle" />
      <span>Or start with a prompt</span>
      <span className="h-px flex-1 bg-line-subtle" />
    </div>
  );
}
