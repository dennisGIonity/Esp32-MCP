// AEDI - IONITY GLOBAL | Ionity ESP32 Flasher build | Policy 986 AED
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" -> the same build works at http://<host>:8099/flasher/ and on
// GitHub Pages under /<repo>/.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: true, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    // `npm run dev` against a running fleet server
    proxy: { "/api": "http://localhost:8099" },
  },
  test: { environment: "node" },
} as any);
