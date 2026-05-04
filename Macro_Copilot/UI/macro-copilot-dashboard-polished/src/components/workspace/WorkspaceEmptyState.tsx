import { useNavigate } from 'react-router-dom';
import { LineChart, BarChart3, Activity, Zap, Gauge, Database, Network } from 'lucide-react';

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
  {
    label: 'FX regime',
    subtitle: 'USD, risk, vol and carry regime',
    icon: <Gauge size={14} />,
    url: '/workspace?tool=fx_regime_classifier&anchor_pair=EURUSD&tenor=1M',
  },
  {
    label: 'EURUSD beta',
    subtitle: 'Macro correlation and beta stack',
    icon: <Network size={14} />,
    url: '/workspace?tool=fx_correlation_beta&pair=EURUSD&window_observations=63',
  },
  {
    label: 'FX data health',
    subtitle: 'Coverage and stale-series diagnostics',
    icon: <Database size={14} />,
    url: '/workspace?tool=fx_data_health&stale_after_days=5',
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

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3">
          {QUICK_LAUNCHES.map((q) => (
            <button
              key={q.label}
              onClick={() => navigate(q.url)}
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
          ))}
        </div>
      </div>
    </div>
  );
}
