/*
 * AEDI - IONITY GLOBAL | @ionity/portal | Vite config | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Relative base: the same build is served by the gateway (/portal/), the fleet host (/flasher/) or GitHub Pages.
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    // `npm run dev`: talk to a local fleet host / gateway without CORS.
    proxy: { '/api': 'http://localhost:8099' },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
    // react-dom and xterm.js are large, rarely-changing vendor code: separate chunks
    // stay cached across portal releases. esptool-js and the simulator are already
    // split by dynamic import().
    chunkSizeWarningLimit: 400,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: 'xterm', test: /node_modules[\\/]@xterm[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    environment: 'node',
    include: ['test/**/*.test.ts'],
  },
});
