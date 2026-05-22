// ============================================================================
// ProseAnswer — the assistant's text answer, with inline data tokens
// styled as first-class data references rather than plain text.
// ----------------------------------------------------------------------------
// Mirrors the parser logic in `lib/parseInlineTokens.ts` — every
// recognized token (σ, bps, %, ISO date, "47 events", "T+3") gets
// wrapped in a `.data-token` span with optional pos/neg tone.
//
// Also handles the existing markdown-ish features (paragraphs, **bold**,
// `code`, headers, bullet/numbered lists) that the supervisor's prose
// uses, so we don't regress text rendering quality vs the legacy
// ChatDrawer.
// ============================================================================

import { type ReactNode } from 'react';
import { parseInlineTokens } from '@/components/ask/lib/parseInlineTokens';
import { cn } from '@/utils/cn';

type Props = {
  text: string;
  isStreaming?: boolean;
};

export function ProseAnswer({ text, isStreaming }: Props) {
  if (!text) return null;

  const paragraphs = text.split(/\n\n+/);
  return (
    <div className="space-y-3 px-5 pb-4 text-[14.5px] leading-[1.6] text-fg-primary">
      {paragraphs.map((para, pi) => renderParagraph(para, pi, paragraphs.length, pi === paragraphs.length - 1 && !!isStreaming))}
    </div>
  );
}

function renderParagraph(
  para: string,
  pi: number,
  total: number,
  appendCursor: boolean,
): ReactNode {
  if (!para.trim()) return null;

  // Header (#, ##, ###)
  const headerMatch = para.match(/^(#{1,3})\s+(.+)/);
  if (headerMatch) {
    const level = headerMatch[1].length;
    return (
      <p
        key={pi}
        className={cn(
          'font-semibold text-fg-primary',
          level === 1 && 'text-[16px]',
          level === 2 && 'text-[14.5px]',
          level >= 3 && 'text-[13px]',
        )}
      >
        {renderInline(headerMatch[2], false)}
      </p>
    );
  }

  // Bulleted list
  if (para.match(/^[-•*]\s/m)) {
    const items = para.split('\n').filter((l) => l.trim());
    return (
      <ul key={pi} className="space-y-1.5 pl-1">
        {items.map((item, ii) => (
          <li
            key={ii}
            className="flex gap-2.5 text-[13.5px] leading-[1.6] text-fg-secondary"
          >
            <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-fg-faint" />
            <span>{renderInline(item.replace(/^[-•*]\s+/, ''), false)}</span>
          </li>
        ))}
      </ul>
    );
  }

  // Numbered list
  if (para.match(/^\d+\.\s/m)) {
    const items = para.split('\n').filter((l) => l.trim());
    return (
      <ol key={pi} className="space-y-1.5 pl-1">
        {items.map((item, ii) => {
          const numMatch = item.match(/^(\d+)\.\s+(.*)/);
          return (
            <li
              key={ii}
              className="flex gap-2.5 text-[13.5px] leading-[1.6] text-fg-secondary"
            >
              <span className="mono mt-px w-5 shrink-0 text-right text-[10.5px] text-fg-faint">
                {numMatch?.[1] ?? ii + 1}.
              </span>
              <span>
                {renderInline(numMatch?.[2] ?? item, false)}
              </span>
            </li>
          );
        })}
      </ol>
    );
  }

  // Regular paragraph — collapse internal newlines into spaces so the
  // streamer's chunk boundaries don't break flow mid-sentence.
  return (
    <p key={pi} className="text-fg-primary">
      {renderInline(para.replace(/\n/g, ' '), pi === total - 1 && appendCursor)}
    </p>
  );
}

// ----------------------------------------------------------------------------
// Inline pass — interleaves the markdown (**bold**, `code`) splitter
// with the data-token detector.  Order:
//   1. Split on **bold** / `code`
//   2. Within each non-bold-non-code segment, run parseInlineTokens
//   3. Render each piece

function renderInline(text: string, appendCursor: boolean): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  const nodes: ReactNode[] = [];

  parts.forEach((part, i) => {
    if (!part) return;
    if (part.startsWith('**') && part.endsWith('**')) {
      nodes.push(
        <strong key={`b-${i}`} className="font-semibold text-fg-primary">
          {renderTokens(part.slice(2, -2))}
        </strong>,
      );
      return;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      nodes.push(
        <code
          key={`c-${i}`}
          className="mono rounded bg-white/[0.06] px-1 py-0.5 text-[12.5px] text-ice-300"
        >
          {part.slice(1, -1)}
        </code>,
      );
      return;
    }
    nodes.push(<span key={`t-${i}`}>{renderTokens(part)}</span>);
  });

  if (appendCursor) {
    nodes.push(<span key="cursor" className="ask-cursor align-baseline" />);
  }
  return nodes;
}

function renderTokens(text: string): ReactNode {
  const segments = parseInlineTokens(text);
  return segments.map((seg, i) => {
    if (seg.kind === 'text') return <span key={i}>{seg.text}</span>;
    return (
      <span
        key={i}
        className={cn(
          'data-token',
          seg.tone === 'data-pos' && 'data-token-pos',
          seg.tone === 'data-neg' && 'data-token-neg',
        )}
      >
        {seg.text}
      </span>
    );
  });
}
