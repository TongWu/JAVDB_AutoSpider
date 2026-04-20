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
          const path = url.split("?")[0];
          const isViteInternalRequest =
            path.startsWith("/@") || path.startsWith("/src/") || path.startsWith("/node_modules/");
          const isPageRequest =
            req.method === "GET" &&
            !path.startsWith("/api") &&
            !isViteInternalRequest &&
            !path.includes(".");
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
