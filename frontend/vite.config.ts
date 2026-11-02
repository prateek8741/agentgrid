import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build is written into the Python package and served by FastAPI,
// so running AgentGrid never requires Node.js (Node is only needed to edit the console).
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "../app/static/console", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", ws: true },
      "/widget.js": "http://localhost:8000",
      "/demo": "http://localhost:8000",
    },
  },
});
