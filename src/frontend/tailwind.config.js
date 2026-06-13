/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
        colors: {
            background: '#0d1117',
            panel: '#161b22',
            accent: '#8b5cf6',
            'accent-hover': '#7c3aed',
            surface: '#1c2128',
            'surface-muted': '#22272e',
            'node-class': '#2563eb',
            'node-class-bg': '#1e3a8a',
            'node-file': '#059669',
            'node-file-bg': '#064e3b',
            'node-model': '#d97706',
            'node-model-bg': '#78350f',
            'text-dim': '#8b949e',
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
                '50%': { opacity: '1', transform: 'scale(1.2)', boxShadow: '0 0 8px rgba(0,212,255,0.6)' },
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
