import { Loader2 } from 'lucide-react';
import type { CopilotMessage } from '@/types/copilot';
import { ToolExecutionTrace } from '@/components/copilot/ToolExecutionTrace';
import { WorkflowResultCard } from '@/components/copilot/WorkflowResultCard';
import { WorkspaceButton } from '@/components/copilot/WorkspaceButton';
import { cn } from '@/utils/cn';

type ChatMessageProps = {
  message: CopilotMessage;
};

export function ChatMessageBubble({ message }: ChatMessageProps) {
  if (message.role === 'user') {
    return <UserMessage content={message.content} />;
  }

  return <AssistantMessage message={message} />;
}

// ------------------------------------------------------------------
// User message
// ------------------------------------------------------------------

function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[88%] rounded-xl rounded-br-sm border border-ice-400/20 bg-ice-500/[0.08] px-3.5 py-2.5">
        <p className="text-[12.5px] leading-[1.55] text-fg-primary">
          {content}
        </p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Assistant message
// ------------------------------------------------------------------

function AssistantMessage({ message }: { message: CopilotMessage }) {
  const hasContent = message.content.length > 0;
  const hasTrace = message.traceSteps.length > 0;
  const hasWorkflow = !!message.workflow;
  const showThinking =
    message.phase === 'thinking' && !hasContent && !hasTrace && !hasWorkflow;
  const showCursor = message.isStreaming && hasContent;

  return (
    <div className="relative">
      {/* Accent rail */}
      <div className="absolute -left-1 top-2 h-6 w-[2px] rounded-full bg-gradient-to-b from-ice-400 to-transparent" />

      {/* Label */}
      <div className="kicker mb-1.5 pl-2 text-fg-muted">
        {hasWorkflow ? 'Copilot · Workflow' : 'Copilot'}
      </div>

      <div className="pl-2">
        {/* Thinking indicator */}
        {showThinking && (
          <div className="flex items-center gap-2 py-1">
            <Loader2 size={12} className="animate-spin text-ice-300" />
            <span className="text-[11px] text-fg-muted">Understanding query...</span>
          </div>
        )}

        {/* Tool execution trace (existing supervisor path) */}
        {hasTrace && (
          <ToolExecutionTrace
            steps={message.traceSteps}
            phase={message.phase}
            totalDurationMs={message.totalDurationMs}
          />
        )}

        {/* Text content */}
        {hasContent && (
          <div className="text-[12.5px] leading-[1.6] text-fg-primary">
            <FormattedContent text={message.content} />
            {showCursor && (
              <span className="ml-0.5 inline-block h-[14px] w-[2px] animate-pulse bg-ice-300" />
            )}
          </div>
        )}

        {/* PR 10 — workflow result card (workflow-router path) */}
        {hasWorkflow && message.workflow && (
          <WorkflowResultCard payload={message.workflow} />
        )}

        {/* Workspace button (existing supervisor path) */}
        {!message.isStreaming && message.workspaceContext && (
          <WorkspaceButton context={message.workspaceContext} />
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Simple markdown-ish formatting (bold, line breaks)
// ------------------------------------------------------------------

function FormattedContent({ text }: { text: string }) {
  // Split into paragraphs
  const paragraphs = text.split('\n\n');

  return (
    <>
      {paragraphs.map((para, pi) => {
        if (!para.trim()) return null;

        // Check for headers (## or ###)
        const headerMatch = para.match(/^(#{1,3})\s+(.+)/);
        if (headerMatch) {
          const level = headerMatch[1].length;
          const headerText = headerMatch[2];
          return (
            <p
              key={pi}
              className={cn(
                'mt-3 mb-1.5 font-semibold text-fg-primary',
                level === 1 && 'text-[13px]',
                level === 2 && 'text-[12.5px]',
                level >= 3 && 'text-[12px]',
              )}
            >
              {formatInline(headerText)}
            </p>
          );
        }

        // Check for list items
        if (para.match(/^[-•*]\s/m)) {
          const items = para.split('\n').filter((l) => l.trim());
          return (
            <ul key={pi} className="mt-1.5 mb-1.5 space-y-1 pl-1">
              {items.map((item, ii) => (
                <li key={ii} className="flex gap-2 text-[12px] text-fg-secondary">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-fg-faint" />
                  <span>{formatInline(item.replace(/^[-•*]\s+/, ''))}</span>
                </li>
              ))}
            </ul>
          );
        }

        // Check for numbered list
        if (para.match(/^\d+\.\s/m)) {
          const items = para.split('\n').filter((l) => l.trim());
          return (
            <ol key={pi} className="mt-1.5 mb-1.5 space-y-1 pl-1">
              {items.map((item, ii) => {
                const numMatch = item.match(/^(\d+)\.\s+(.*)/);
                return (
                  <li key={ii} className="flex gap-2 text-[12px] text-fg-secondary">
                    <span className="mono mt-px w-4 shrink-0 text-right text-[10px] text-fg-faint">
                      {numMatch?.[1] ?? ii + 1}.
                    </span>
                    <span>{formatInline(numMatch?.[2] ?? item)}</span>
                  </li>
                );
              })}
            </ol>
          );
        }

        // Regular paragraph
        return (
          <p key={pi} className={cn('text-fg-secondary', pi > 0 && 'mt-2')}>
            {formatInline(para.replace(/\n/g, ' '))}
          </p>
        );
      })}
    </>
  );
}

/**
 * Format inline markdown: **bold** and `code`
 */
function formatInline(text: string): React.ReactNode {
  // Split on **bold** and `code` patterns
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);

  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={i} className="font-semibold text-fg-primary">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code
          key={i}
          className="mono rounded bg-white/[0.06] px-1 py-0.5 text-[11px] text-ice-300"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}
