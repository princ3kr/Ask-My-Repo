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
            accent: 'var(--accent-color)',
            'accent-hover': 'var(--accent-hover)',
            surface: 'var(--surface-color)',
            'surface-muted': 'var(--surface-muted-color)',
            'text-color': 'var(--text-color)',
            'text-dim': 'var(--text-dim)',
        },
        fontFamily: {
            mono: ['"Geist Mono"', 'monospace'],
            sans: ['Syne', 'sans-serif'],
        },
        // Node colours are NOT configured here: they come from the CSS custom
        // properties --node-{class,file,model}-{color,bg,border} in index.css,
        // which the custom node components read. The previous `node-*` colour
        // entries, plus `panel` and `text-muted`, had zero class usages — as
        // did all five animations (pulse-slow, breathe, shimmer,
        // bounce-elastic, drift) and their keyframes.
    },
  },
  plugins: [],
}
