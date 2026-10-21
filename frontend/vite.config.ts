import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend URL comes from the environment (make demo sets it); default is the local server.
const backend = process.env.SCAMSHIELD_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.SCAMSHIELD_FRONTEND_PORT ?? 5173),
    strictPort: true,
    proxy: {
      "/sessions": backend,
      "/health": backend,
      "/verify": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
});
