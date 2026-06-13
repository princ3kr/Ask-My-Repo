/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
        colors: {
            background: 'var(--bg-color)',
            panel: 'var(--panel-color)',
            accent: 'var(--accent-color)',
            'accent-hover': 'var(--accent-hover)',
            surface: 'var(--surface-color)',
            'surface-muted': 'var(--surface-muted-color)',
            'node-class': '#3b82f6',
            'node-class-bg': '#1e3a8a',
            'node-file': '#10b981',
            'node-file-bg': '#064e3b',
            'node-model': '#f59e0b',
            'node-model-bg': '#78350f',
            'text-dim': 'var(--text-dim)',
        },
        fontFamily: {
            mono: ['"Geist Mono"', 'monospace'],
            sans: ['Syne', 'sans-serif'],
        },
        animation: {
            'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
            'breathe': 'breathe 2.5s ease-in-out infinite',
            'shimmer': 'shimmer 2s linear infinite',
            'bounce-elastic': 'bounce-elastic 0.8s infinite',
            'drift': 'drift 60s linear infinite',
        },
        keyframes: {
            breathe: {
                '0%, 100%': { opacity: '0.3', transform: 'scale(0.8)' },
                '50%': { opacity: '1', transform: 'scale(1.2)', boxShadow: '0 0 8px rgba(139,92,246,0.6)' },
            },
            shimmer: {
                '0%': { backgroundPosition: '-200% 0' },
                '100%': { backgroundPosition: '200% 0' }
            },
            'bounce-elastic': {
                '0%, 100%': { transform: 'translateY(0)' },
                '50%': { transform: 'translateY(-4px)' },
            },
            drift: {
                '0%': { transform: 'translateY(0) translateX(0)' },
                '100%': { transform: 'translateY(-50%) translateX(-20%)' }
            }
        }
    },
  },
  plugins: [],
}
