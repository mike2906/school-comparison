/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Sofia-inspired palette: warm terracotta meets educational teal
        primary: {
          50: '#f0fdfa',
          100: '#ccfbf1',
          200: '#99f6e4',
          300: '#5eead4',
          400: '#2dd4bf',
          500: '#14b8a6',  // Main teal
          600: '#0d9488',
          700: '#0f766e',
          800: '#115e59',
          900: '#134e4a',
        },
        accent: {
          50: '#fef7f0',
          100: '#feecdc',
          200: '#fcd9bd',
          300: '#fac08f',
          400: '#f79d5c',
          500: '#f97316',  // Warm orange accent
          600: '#ea580c',
          700: '#c2410c',
          800: '#9a3412',
          900: '#7c2d12',
        },
        neutral: {
          50: '#fafafa',
          100: '#f5f5f5',
          200: '#e5e5e5',
          300: '#d4d4d4',
          400: '#a3a3a3',
          500: '#737373',
          600: '#525252',
          700: '#404040',
          800: '#262626',
          900: '#171717',
        }
      },
      boxShadow: {
        'card': '0 1px 2px rgba(0,0,0,0.04), 0 2px 8px rgba(0,0,0,0.06)',
        'card-hover': '0 2px 4px rgba(0,0,0,0.06), 0 8px 24px rgba(0,0,0,0.1)',
        'panel': '0 0 0 1px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.08)',
      },
      transitionTimingFunction: {
        'smooth': 'cubic-bezier(0.4, 0, 0.2, 1)',
      }
    },
  },
  plugins: [],
}
