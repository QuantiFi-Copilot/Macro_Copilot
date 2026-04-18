import type { DashboardData } from '@/types/dashboard';

const miniSeries = (values: number[]) =>
  values.map((value, index) => ({
    label: `T-${values.length - index}`,
    value,
  }));

const dashboardData: DashboardData = {
  ratesCard: {
    title: 'Rates Agent',
    subtitle: 'UST 2s10s Spread',
    spreadValue: '-0.43%',
    pcaLabel: 'PCA 1y Z Score',
    pcaValue: '1.92',
    theme: 'rates',
    spreadSeries: miniSeries([42, 45, 48, 52, 49, 46, 43, 41, 39, 37, 36, 35, 34, 35, 36, 40, 46]),
    pcaSeries: miniSeries([18, 18.5, 18.7, 19, 19.2, 19.4, 19.3, 19.6, 20.1, 20.8, 21.4, 22.1, 22.8]),
  },
  fxCard: {
    title: 'FX Agent',
    pairs: [
      {
        symbol: 'EUR/USD',
        value: '1.0867',
        delta: '+0.53%',
        direction: 'up',
      },
      {
        symbol: 'USD/JPY',
        value: '154.96',
        delta: '-0.61%',
        direction: 'down',
      },
    ],
    series: miniSeries([96, 98, 97, 99, 103, 105, 107, 110, 112, 115, 117, 121]),
  },
  fxMatrix: [
    {
      market: 'US 2Y',
      value: '1.2x',
      changeBps: '+8.5bps',
      zScore: '+0.3%',
    },
    {
      market: 'DE 5Y',
      value: '2.48',
      changeBps: '+6.5bps',
      zScore: '+1.6%',
    },
    {
      market: 'CD-2s10s',
      value: '4.85',
      changeBps: '+3.6bps',
      zScore: '+1.9%',
    },
  ],
  events: [
    {
      event: 'US PCE Inflation',
      dateLabel: 'N 0.10% Core YoY',
      probability: '4.6%',
      count: '3',
      tone: 'green',
      series: miniSeries([23, 23, 24, 24, 25, 25.5, 26]),
    },
    {
      event: 'ECB Meeting',
      dateLabel: 'May 2, 3.7% Depo Rate',
      probability: '5.75%',
      count: '3',
      tone: 'blue',
      series: miniSeries([18, 18.2, 18.3, 18.35, 18.6, 18.9, 19.2]),
    },
    {
      event: 'US NFP Report',
      dateLabel: 'May 3, 245K Consensus',
      probability: '245%',
      count: '4',
      tone: 'amber',
      series: miniSeries([9, 9.2, 9.1, 9.4, 9.8, 10.2, 10.8]),
    },
  ],
  marketMovers: [
    {
      region: 'US 2Y',
      instrument: '5.12',
      change: '+8.1bps',
      zScore: '+1.97',
      badge: 'JF',
      direction: 'up',
    },
    {
      region: 'DE 5Y',
      instrument: '2.48',
      change: '+6.3bps',
      zScore: '+1.65',
      badge: 'JF',
      direction: 'up',
    },
    {
      region: 'CD 2s10s',
      instrument: '-0.85',
      change: '+5.3bps',
      zScore: '+1.53',
      badge: 'JF',
      direction: 'up',
    },
  ],
  highlights: [
    {
      title: 'US Core PCE Inflation Surprises High',
      body: 'April 2024 Core PCE inflation came in a 0.4% MoM vs 0.3% expectation.',
      tone: 'white',
      series: miniSeries([40, 41, 41.4, 41.2, 42, 42.4, 42.2, 43]),
    },
    {
      title: 'EUR/USD Extends Gains',
      body: 'EURUSD hits new 1-month high as ECB turns more dovish.',
      tone: 'blue',
      series: miniSeries([28, 28.2, 28.6, 29, 29.2, 29.1, 29.4, 29.7]),
    },
    {
      title: 'USD/JPY Breaks 155',
      body: 'USDJPY breaches 155 for a second time, reviving intervention risk.',
      tone: 'green',
      series: miniSeries([22, 22.1, 22.4, 22.9, 23.2, 23.6, 24.1, 24.4]),
    },
  ],
  suggestions: [
    { id: '1', label: "Summarize today's rate moves" },
    { id: '2', label: 'Compare US and EUR 2s10s spreads' },
    { id: '3', label: "What's implied by next FOMC meeting?" },
  ],
  chatMessages: [
    {
      id: 'assistant-1',
      role: 'assistant',
      content: 'Welcome. How can I help you today?',
    },
    {
      id: 'user-1',
      role: 'user',
      content: 'Type on your question...',
    },
  ],
  topNavTabs: ['Home', 'Rates', 'FX', 'Policy', 'Events'],
  sidebarGroups: [
    {
      title: 'Rates Agent',
      items: [
        { label: 'Daily Monitor', active: true },
        { label: 'Curve Analysis' },
        { label: 'Cross-Market RV' },
        { label: 'Event Analysis' },
        { label: 'Chat with Rates Agent' },
      ],
    },
    {
      title: 'Rates Agent',
      items: [
        { label: 'Daily Monitor' },
        { label: 'Curve Analysis' },
        { label: 'Cross-Market RV' },
        { label: 'Event Analysis' },
        { label: 'Chat with Rates Agent' },
      ],
    },
  ],
};

const SIMULATED_LATENCY_MS = 180;

export async function getDashboardData(): Promise<DashboardData> {
  await new Promise((resolve) => {
    window.setTimeout(resolve, SIMULATED_LATENCY_MS);
  });

  return structuredClone(dashboardData);
}
