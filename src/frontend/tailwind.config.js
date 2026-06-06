/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
        colors: {
            background: '#050508',
            accent: '#00D4FF',
            surface: '#0A0A10',
            'surface-muted': '#14141C',
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
