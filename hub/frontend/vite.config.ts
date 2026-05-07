import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        // VITE_API_TARGET lets dev hosts point at a non-default backend port
        // (e.g. when the installed Hub is already bound to 8003 and we
        // run a side-by-side dev backend on 8014). Defaults to the
        // installed Hub's port for the typical "git pull, npm run dev"
        // workflow where Tory is iterating against the running install.
        target: process.env.VITE_API_TARGET || 'http://localhost:8003',
        changeOrigin: true,
      },
    },
  },
})
