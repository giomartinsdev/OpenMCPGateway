import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA is served same-origin by FastAPI in production; in dev, proxy /api so
// cookies stay first-party and no CORS dance is needed.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
