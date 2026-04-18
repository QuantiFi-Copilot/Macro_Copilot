import type { Config } from 'tailwindcss';

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      screens: {
        '3xl': '1920px',
        '4xl': '2240px',
      },
      colors: {
        ink: {
          900: '#06080C',
          800: '#0A0C12',
          700: '#0E1118',
          600: '#131621',
          500: '#181C28',
          400: '#1E2230',
        },
        line: {
          subtle: 'rgba(148,163,184,0.07)',
          soft: 'rgba(148,163,184,0.10)',
          strong: 'rgba(148,163,184,0.16)',
        },
        ice: {
          50: '#EAF0FF',
          100: '#C9D6FF',
          200: '#A6BCFF',
          300: '#86A3FF',
          400: '#7AA2FF',
          500: '#5E8BF7',
          600: '#4A72DB',
          700: '#3957B1',
        },
        mint: {
          300: '#6BE4B0',
          400: '#3FD69A',
          500: '#1EB67C',
        },
        coral: {
          300: '#FF8FA0',
          400: '#FF6B7E',
          500: '#E84B63',
        },
        amber: {
          300: '#F8CE88',
          400: '#F3B755',
          500: '#D99528',
        },
        fg: {
          primary: '#E8EAF0',
          secondary: '#A4A8B6',
          muted: '#6A6E7C',
          faint: '#45485A',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        display: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        'micro': ['10px', { lineHeight: '14px', letterSpacing: '0.12em' }],
        'kicker': ['10px', { lineHeight: '12px', letterSpacing: '0.16em' }],
      },
      boxShadow: {
        panel: '0 1px 0 0 rgba(255,255,255,0.035) inset, 0 24px 60px -24px rgba(0,0,0,0.55)',
        card: '0 1px 0 0 rgba(255,255,255,0.035) inset, 0 20px 50px -28px rgba(0,0,0,0.6)',
        glow: '0 0 0 1px rgba(122,162,255,0.22), 0 12px 40px -12px rgba(122,162,255,0.18)',
        ring: '0 0 0 1px rgba(148,163,184,0.12)',
      },
      backgroundImage: {
        'ambient-radial':
          'radial-gradient(1200px 600px at 22% -10%, rgba(94,139,247,0.08), transparent 55%), radial-gradient(900px 500px at 88% -20%, rgba(66,92,172,0.06), transparent 60%), radial-gradient(800px 500px at 50% 120%, rgba(122,162,255,0.04), transparent 60%)',
        'panel-sheen':
          'linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.012) 40%, rgba(255,255,255,0) 100%)',
        'card-sheen':
          'linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.008) 60%, rgba(255,255,255,0) 100%)',
        'border-gradient':
          'linear-gradient(140deg, rgba(255,255,255,0.12), rgba(255,255,255,0.02) 35%, rgba(255,255,255,0) 60%, rgba(122,162,255,0.06) 100%)',
      },
      transitionTimingFunction: {
        sleek: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
      },
    },
  },
  plugins: [],
} satisfies Config;
