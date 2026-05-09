// ============================================================================
// LibrarySearch — single-line search input for the catalogue
// ----------------------------------------------------------------------------
// Filters tools by name, one_liner, related_tools, and workflows.
// Pure client-side — the manifest is small enough that we don't need
// server-side search.
// ============================================================================

import { Search, X } from 'lucide-react';

type Props = {
  value: string;
  onChange: (next: string) => void;
  placeholder: string;
};

export function LibrarySearch({ value, onChange, placeholder }: Props) {
  return (
    <div className="px-8 pb-3 pt-1">
      <div className="composer-shell mx-auto flex h-10 max-w-[720px] items-center gap-2 px-3.5">
        <Search size={13} className="shrink-0 text-fg-muted" />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="flex-1 bg-transparent text-[13px] tracking-[-0.005em] text-fg-primary placeholder:text-fg-faint focus:outline-none"
        />
        {value && (
          <button
            type="button"
            onClick={() => onChange('')}
            aria-label="Clear search"
            className="flex h-5 w-5 items-center justify-center rounded text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary"
          >
            <X size={11} />
          </button>
        )}
      </div>
    </div>
  );
}
