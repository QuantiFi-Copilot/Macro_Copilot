// ============================================================================
// UserMessage — right-aligned typographic block, no bubble, no avatar
// ----------------------------------------------------------------------------
// Per the brief: a 4% wash, 8px radius, max 640px width, 14px Inter
// regular, fg-primary text, with a 10px mono timestamp 4px below the
// message right-aligned.
// ============================================================================

import { cn } from '@/utils/cn';

type Props = {
  content: string;
  timestamp: Date;
};

export function UserMessage({ content, timestamp }: Props) {
  return (
    <div className="flex flex-col items-end">
      <div
        className={cn(
          'max-w-[640px] rounded-lg bg-white/[0.04] px-4 py-2.5',
          'text-[14px] leading-[1.55] text-fg-primary',
        )}
      >
        {content}
      </div>
      <span className="mt-1 mr-1 text-[10px] mono text-fg-faint">
        {formatTime(timestamp)}
      </span>
    </div>
  );
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}
