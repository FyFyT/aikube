/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: '#0A0C10',
          secondary: '#0F1117',
          tertiary: '#161A23',
        },
        brand: {
          purple: '#7C6AF7',
          teal: '#22D3A5',
          red: '#FF5E6D',
          amber: '#F4A64A',
          green: '#4ADE80',
        }
      },
      fontFamily: {
        sans: ['IBM Plex Sans', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      }
    },
  },
  plugins: [],
}
