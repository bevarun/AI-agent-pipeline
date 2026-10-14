import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api/* is proxied to the mock CMS (uvicorn cms.app:app --port 8000)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
