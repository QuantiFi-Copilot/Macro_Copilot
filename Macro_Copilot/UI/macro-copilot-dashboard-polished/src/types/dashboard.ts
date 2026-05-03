export type SparklinePoint = {
  label?: string;
  value: number;
};

export type RatesCardData = {
  title: string;
  subtitle: string;
  spreadValue: string;
  pcaLabel: string;
  pcaValue: string;
  theme: 'rates';
  spreadSeries: SparklinePoint[];
  pcaSeries: SparklinePoint[];
};

export type FXCardData = {
  title: string;
  pairs: Array<{
    symbol: string;
    value: string;
    delta: string;
    direction: 'up' | 'down';
  }>;
  series: SparklinePoint[];
};

export type FXMatrixRow = {
  market: string;
  value: string;
  changeBps: string;
  zScore: string;
};

export type EventMonitorRow = {
  event: string;
  dateLabel: string;
  probability: string;
  count: string;
  series: SparklinePoint[];
  tone: 'green' | 'blue' | 'amber';
};

export type MarketMover = {
  region: string;
  instrument: string;
  change: string;
  zScore: string;
  badge: string;
  direction: 'up' | 'down';
};

export type MacroHighlight = {
  title: string;
  body: string;
  series: SparklinePoint[];
  tone: 'white' | 'green' | 'blue';
};

export type ChatSuggestion = {
  id: string;
  label: string;
};

export type ChatMessage = {
  id: string;
  role: 'assistant' | 'user';
  content: string;
};

export type SidebarGroup = {
  title: string;
  items: Array<{
    label: string;
    active?: boolean;
  }>;
};

export type DashboardData = {
  ratesCard: RatesCardData;
  fxCard: FXCardData;
  fxMatrix: FXMatrixRow[];
  events: EventMonitorRow[];
  marketMovers: MarketMover[];
  highlights: MacroHighlight[];
  suggestions: ChatSuggestion[];
  chatMessages: ChatMessage[];
  topNavTabs: string[];
  sidebarGroups: SidebarGroup[];
};
