import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Flask serves everything under static/. Built index.html is collected back into
// ../static (emptying it first), and asset URLs are prefixed with /static/ so the
// Flask built-in static route can serve them. During dev, /api is proxied to Flask.
export default defineConfig({
  base: '/static/',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
    },
  },
})
