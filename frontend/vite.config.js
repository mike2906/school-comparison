import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ command, mode }) => {
  if (command === 'build' && !loadEnv(mode, process.cwd()).VITE_CARTO_API_KEY) {
    console.warn(
      '\n\x1b[33m[warn] VITE_CARTO_API_KEY is not set: map tiles will show an ' +
        '"API KEY REQUIRED" watermark. See frontend/.env.example.\x1b[0m\n'
    )
  }

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
