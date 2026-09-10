/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// scripts/server.sh passes API_PORT through so the proxy follows a non-default backend port.
const apiPort = process.env.API_PORT ?? '8000';

export default defineConfig({
  plugins: [react()],
  // Unit tests cover pure helpers and reducers only, so the node environment is enough.
  test: { environment: 'node', include: ['tests/**/*.test.ts'] },
  server: {
    proxy: {
      '/api': { target: `http://localhost:${apiPort}`, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
});
