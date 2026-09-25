import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API_TARGET can override the backend URL for local testing (default: FastAPI on :8000).
const target = process.env.API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target, changeOrigin: true } },
  },
  preview: {
    proxy: { "/api": { target, changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1000 },
});
