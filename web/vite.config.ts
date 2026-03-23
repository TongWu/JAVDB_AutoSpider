import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [
    vue(),
    {
      name: "spa-history-fallback",
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          const url = req.url || "";
          const isPageRequest = req.method === "GET" && !url.startsWith("/api") && !url.includes(".");
          if (isPageRequest) req.url = "/index.html";
          next();
        });
      },
    },
  ],
  server: {
    port: 5173,
    host: "0.0.0.0",
  },
});
