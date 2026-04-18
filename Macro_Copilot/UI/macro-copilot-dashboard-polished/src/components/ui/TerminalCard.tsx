import type { PropsWithChildren, ReactNode } from 'react';
import { cn } from '@/utils/cn';

type TerminalCardProps = PropsWithChildren<{
  title?: string;
  kicker?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
  bodyClassName?: string;
  headerClassName?: string;
}>;

export function TerminalCard({
  title,
  kicker,
  icon,
  action,
  className,
  bodyClassName,
  headerClassName,
  children,
}: TerminalCardProps) {
  return (
    <section className={cn('card flex min-w-0 flex-col overflow-hidden', className)}>
      {(title || action || kicker) && (
        <header
          className={cn(
            'flex items-center justify-between gap-3 border-b border-line-subtle px-5 pt-4 pb-3',
            headerClassName,
          )}
        >
          <div className="flex min-w-0 items-center gap-2.5">
            {icon ? (
              <span className="flex h-6 w-6 items-center justify-center rounded-md border border-line-subtle bg-white/[0.015] text-ice-300">
                {icon}
              </span>
            ) : null}
            <div className="flex min-w-0 flex-col">
              {kicker ? <span className="kicker">{kicker}</span> : null}
              {title ? (
                <h3 className="truncate text-[13.5px] font-semibold tracking-[-0.01em] text-fg-primary">
                  {title}
                </h3>
              ) : null}
            </div>
          </div>
          {action ? <div className="flex shrink-0 items-center gap-1.5">{action}</div> : null}
        </header>
      )}
      <div className={cn('flex min-w-0 flex-1 flex-col px-5 pt-4 pb-5', bodyClassName)}>
        {children}
      </div>
    </section>
  );
}
