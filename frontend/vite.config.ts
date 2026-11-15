import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // stable vendor chunks: cached across app deploys, downloaded in parallel
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('recharts') || id.includes('d3-') || id.includes('victory')) return 'charts'
          if (id.includes('leaflet')) return 'map'
          if (id.includes('lucide-react')) return 'icons'
          if (id.includes('react')) return 'react'
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000', '^/\\.?sim(/|$)': { target: 'http://127.0.0.1:8100', ws: true, rewrite: (path: string) => path.replace(/^\/\.sim(?=\/|$)/, '/sim') } },
    // allow sharing via Cloudflare quick tunnels (https://<random>.trycloudflare.com)
    allowedHosts: ['.trycloudflare.com', '.hf.space'],
  },
  // `vite preview` (production build) uses the same proxy + host rules
  preview: {
    port: 4173,
    proxy: { '/api': 'http://127.0.0.1:8000', '^/\\.?sim(/|$)': { target: 'http://127.0.0.1:8100', ws: true, rewrite: (path: string) => path.replace(/^\/\.sim(?=\/|$)/, '/sim') } },
    allowedHosts: ['.trycloudflare.com', '.hf.space'],
  },
})
