# Macro Copilot Dashboard

A production-minded React + Vite + TypeScript + Tailwind frontend shell for a macro research terminal.

## Stack

- React 18
- Vite
- TypeScript
- Tailwind CSS
- lucide-react
- Recharts

## Run locally

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
npm run preview
```

## Current structure

```text
macro-copilot-dashboard/
├── index.html
├── package.json
├── postcss.config.js
├── tailwind.config.ts
├── tsconfig.app.json
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
└── src/
    ├── App.tsx
    ├── index.css
    ├── main.tsx
    ├── components/
    │   ├── dashboard/
    │   │   ├── Dashboard.tsx
    │   │   ├── DashboardSkeleton.tsx
    │   │   ├── EventMonitorCard.tsx
    │   │   ├── FXCard.tsx
    │   │   ├── FXMatrixCard.tsx
    │   │   ├── MacroHighlights.tsx
    │   │   ├── MarketMoversTable.tsx
    │   │   └── RatesCard.tsx
    │   ├── layout/
    │   │   ├── AppShell.tsx
    │   │   ├── ChatDrawer.tsx
    │   │   └── Sidebar.tsx
    │   └── ui/
    │       ├── Sparkline.tsx
    │       ├── TerminalCard.tsx
    │       └── TopNav.tsx
    ├── hooks/
    │   └── useDashboardData.ts
    ├── lib/
    │   └── chart.ts
    ├── services/
    │   └── mockData.ts
    ├── types/
    │   └── dashboard.ts
    └── utils/
        └── cn.ts
```

## Backend wiring notes

The current UI consumes data only through `useDashboardData()` and `services/mockData.ts`.

To wire this into FastAPI next:
1. Replace `getDashboardData()` with a real fetch client.
2. Keep the `DashboardData` response shape stable.
3. Swap the hook internals to TanStack Query without touching the widgets.
4. Replace the mock `ratesCard` payload and `marketMovers` payload first.
