/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Django serves the built page at /app/ and these files under
// /static/web/. In development Vite serves both and passes the API, the
// sign-in pages and Django's own static files through to Django, so the
// session cookie and the CSRF check work exactly as they will in use.
const django = process.env.DJANGO_URL ?? "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/web/" : "/",
  plugins: [react()],
  build: {
    outDir: "../apps/web/static/web",
    emptyOutDir: true,
    sourcemap: false,
    // One chunk per module, so a clerk who only opens Sales never
    // downloads Accounts.
    chunkSizeWarningLimit: 400,
  },
  server: {
    proxy: {
      "/api": { target: django },
      "/accounts": { target: django },
      "/static/admin": { target: django },
      "/station": { target: django },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
}));
