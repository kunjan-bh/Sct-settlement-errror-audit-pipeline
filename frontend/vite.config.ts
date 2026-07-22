import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Lets the frontend call fetch('/api/...') without hardcoding
      // http://localhost:5000 everywhere -- Vite forwards it to Flask.
      '/api': 'http://localhost:5000',
    },
  },
})
