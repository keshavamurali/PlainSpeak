import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
          proxy.on("error", (err, req, res) => {
            if (err.code === "ECONNREFUSED") {
              console.warn("[Vite] Backend not running at http://127.0.0.1:8000 — start with: uv run uvicorn api:app --port 8000");
            } else {
              console.error("[Vite] Proxy error:", err.message);
            }
          });
        },
      },
    },
  },
});
