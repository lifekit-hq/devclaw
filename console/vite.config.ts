import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Bundle is served by FastMCP under /console. Base path must match so all
// asset URLs resolve when the app is not on /.
export default defineConfig({
  plugins: [react()],
  base: "/console/",
  build: {
    // The SOURCE lives at the repo root (it is a web app, not a Python
    // module), but the BUILT bundle must land inside the Python package:
    // devclaw ships as a wheel (`pip install .` in deploy/Dockerfile) and
    // anything outside `packages = ["devclaw"]` is simply not installed.
    // Same lesson as the vendored .specify scaffold in pyproject (#588).
    outDir: "../devclaw/server/console_dist",
    emptyOutDir: true,
    // devclaw ships as a Python package; a couple of small chunks are easier
    // to reason about than tree-shaken bundle splitting we don't yet need.
    rollupOptions: {
      output: { manualChunks: undefined },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // dev-server proxy so `vite dev` can hit a running devclaw MCP for data.
      // For local-→-VPS testing, run: ssh -N -L 8000:127.0.0.1:8000 lifekit-vps
      "/projects.json": "http://127.0.0.1:8000",
      "/goals.json": "http://127.0.0.1:8000",
      "/projects": "http://127.0.0.1:8000",
      "/goals": { target: "http://127.0.0.1:8000", ws: true },
      "/prs": "http://127.0.0.1:8000",
    },
  },
});
