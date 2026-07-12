/// <reference types="vitest/config" />
import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // En dev local, l'API tourne via `make api` ; en staging c'est Caddy qui route /api.
      // `changeOrigin: false` : le header Host d'origine (sous-domaine tenant) doit
      // atteindre l'API tel quel — Caddy fait de même en staging/prod (reverse_proxy
      // sans réécriture) ; sinon le contrôle CSRF Origin-vs-Host (Phase 2) rejette tout.
      "/api": { target: "http://localhost:8000", changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
  },
});
