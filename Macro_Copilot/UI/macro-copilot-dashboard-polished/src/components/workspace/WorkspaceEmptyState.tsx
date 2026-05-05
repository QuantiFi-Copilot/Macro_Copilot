import { useNavigate } from 'react-router-dom';
import {
  LineChart,
  BarChart3,
  Activity,
  Zap,
  Sigma,
  Workflow,
  Wrench,
} from 'lucide-react';

type QuickLaunch = {
  label: string;
  subtitle: string;
  icon: React.ReactNode;
  url: string;
};

const QUICK_LAUNCHES: QuickLaunch[] = [
  {
    label: 'UST 2s10s',
    subtitle: 'US Treasury curve spread',
    icon: <LineChart size={14} />,
    url: '/workspace?tool=spread&curve_family=UST&short_tenor=2Y&long_tenor=10Y',
  },
  {
    label: 'BTP-Bund 10Y',
    subtitle: 'Italy vs Germany cross-market',
    icon: <BarChart3 size={14} />,
    url: '/workspace?tool=cross_market&curve_family_1=IT_BTP&curve_family_2=DE_BUND&tenor=10Y',
  },
  {
    label: 'UST 10Y',
    subtitle: 'Treasury yield level',
    icon: <Activity size={14} />,
    url: '/workspace?tool=yield&curve_family=UST&tenor=10Y',
  },
  {
    label: 'UST 2s5s10s',
    subtitle: 'Treasury butterfly / curvature',
    icon: <BarChart3 size={14} />,
    url: '/workspace?tool=butterfly&curve_family=UST&short_tenor=2Y&belly_tenor=5Y&long_tenor=10Y',
  },
  {
    label: 'UST regime',
    subtitle: '22d curve-move classification',
    icon: <Activity size={14} />,
    url: '/workspace?tool=regime&curve_family=UST&lookback_period=22d',
  },
  {
    label: 'Run scanner',
    subtitle: 'Z-score extremes across sovereigns',
    icon: <Zap size={14} />,
    url: '/workspace?tool=scanner',
  },
];

// PR 10 — interactive primitive models.  Each row deep-links into
// PrimitiveModelView with sensible defaults pre-populated, so the
// PM can hit Run and see a result inside ~1s.
const PRIMITIVE_MODELS: QuickLaunch[] = [
  {
    label: 'OIS Curve Spread',
    subtitle: 'OIS 2s10s with rolling z-score',
    icon: <Sigma size={14} />,
    url:
      '/workspace?tool=primitive&name=calculate_ois_curve_spread_tool' +
      '&curve_family=USD_OIS&short_tenor=2Y&long_tenor=10Y&lookback_days=252',
  },
  {
    label: 'Swap Spread',
    subtitle: 'OIS – Treasury at one tenor + Δ z-score',
    icon: <Sigma size={14} />,
    url:
      '/workspace?tool=primitive&name=calculate_swap_spread_tool' +
      '&ois_curve_family=USD_OIS&treasury_curve_family=UST&tenor=10Y' +
      '&lookback_days=504',
  },
  {
    label: 'OIS Forward Rate',
    subtitle: 'e.g. USD 2Y2Y forward implied by OIS',
    icon: <Sigma size={14} />,
    url:
      '/workspace?tool=primitive&name=calculate_ois_forward_rate_tool' +
      '&curve_family=USD_OIS&forward_start=2Y&forward_length=2Y&lookback_days=252',
  },
  {
    label: 'OIS Cross-Market',
    subtitle: 'USD-EUR OIS spread at one tenor',
    icon: <Sigma size={14} />,
    url:
      '/workspace?tool=primitive&name=calculate_ois_cross_market_spread_tool' +
      '&curve_family_1=USD_OIS&curve_family_2=EUR_OIS&tenor=10Y&lookback_days=252',
  },
  {
    label: 'OIS Rate Level',
    subtitle: 'Single OIS rate as a daily series',
    icon: <Sigma size={14} />,
    url:
      '/workspace?tool=primitive&name=get_ois_rate_level_tool' +
      '&curve_family=USD_OIS&tenor=10Y&lookback_days=252',
  },
  {
    label: 'Sovereign Yield',
    subtitle: 'Single sovereign yield series',
    icon: <Sigma size={14} />,
    url:
      '/workspace?tool=primitive&name=get_yield_levels_tool' +
      '&curve_family=UST&tenor=10Y&lookback_days=252',
  },
];

export function WorkspaceEmptyState() {
  const navigate = useNavigate();

  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-12">
      <div className="w-full max-w-2xl">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg border border-line-soft bg-white/[0.02]">
            <LineChart size={18} className="text-ice-300" />
          </div>
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-fg-primary">
            Select an instrument to analyze
          </h2>
          <p className="mt-1.5 text-[12px] text-fg-secondary">
            Quick-launch a common analysis below, or use the copilot chat to
            navigate here from a tool result.
          </p>
        </div>

        <div className="mb-4 flex items-baseline justify-between">
          <p className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-fg-faint">
            Saved analyses
          </p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3">
          {QUICK_LAUNCHES.map((q) => (
            <QuickLaunchTile key={q.label} q={q} onNavigate={navigate} />
          ))}
        </div>

        {/* PR 10 — interactive primitive models */}
        <div className="mt-10">
          <div className="mb-4 flex items-baseline justify-between">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-fg-faint">
              Primitive models · interactive
            </p>
            <button
              type="button"
              onClick={() => navigate('/tools')}
              className="flex items-center gap-1 text-[10.5px] text-fg-muted transition-colors hover:text-ice-300"
            >
              <Wrench size={11} />
              Browse all in catalogue →
            </button>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3">
            {PRIMITIVE_MODELS.map((q) => (
              <QuickLaunchTile key={q.label} q={q} onNavigate={navigate} />
            ))}
          </div>
        </div>

        <div className="mt-10 rounded-lg border border-line-subtle bg-white/[0.008] px-4 py-3">
          <div className="flex items-start gap-2.5">
            <Workflow size={13} className="mt-0.5 text-ice-300" />
            <div className="flex-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-fg-muted">
                Looking for the LLM-orchestrated workflows?
              </p>
              <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-secondary">
                The full event-study and regime-conditioned-relationship
                templates live in the{' '}
                <button
                  className="text-ice-300 hover:underline"
                  onClick={() => navigate('/workflows')}
                  type="button"
                >
                  Workflows catalogue
                </button>
                {' '}— or just ask the copilot in plain English.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function QuickLaunchTile({
  q,
  onNavigate,
}: {
  q: QuickLaunch;
  onNavigate: (url: string) => void;
}) {
  return (
    <button
      onClick={() => onNavigate(q.url)}
      className="group flex flex-col items-start gap-1.5 rounded-lg border border-line-soft bg-white/[0.012] px-3.5 py-3 text-left transition-all duration-150 ease-sleek hover:border-ice-400/30 hover:bg-ice-500/[0.05]"
    >
      <div className="flex h-6 w-6 items-center justify-center rounded-md border border-line-subtle bg-white/[0.02] text-ice-300 transition-colors group-hover:border-ice-400/30 group-hover:bg-ice-500/15">
        {q.icon}
      </div>
      <div className="min-w-0">
        <div className="text-[12px] font-semibold text-fg-primary">
          {q.label}
        </div>
        <div className="mt-0.5 text-[10.5px] leading-snug text-fg-muted">
          {q.subtitle}
        </div>
      </div>
    </button>
  );
}
