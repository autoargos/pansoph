import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// lens is a static SPA. In dev it proxies the agora API and SSE stream so the app
// runs against a local backend without CORS. In production nginx does the proxy.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // agora. Override by editing this, or front lens with nginx in production.
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist" },
});
