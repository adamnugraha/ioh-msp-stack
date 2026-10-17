import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Two builds from the same code:
//   vite build              the console the kit ships: served by FastAPI from msp_glue/console/ui, data from /api
//   vite build --mode demo  one self-contained page with sample data and no network calls (the preview)
export default defineConfig(({ mode }) => {
  const demo = mode === "demo";
  return {
    base: demo ? "./" : "/ui/",
    plugins: [react(), tailwindcss(), ...(demo ? [viteSingleFile({ removeViteModuleLoader: true })] : [])],
    resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
    build: {
      outDir: demo ? "dist-demo" : "../msp_glue/console/ui",
      emptyOutDir: true,
      chunkSizeWarningLimit: 4000,
      assetsInlineLimit: demo ? 50_000_000 : 4096,
    },
    server: {
      // npm run dev: the API of a console running in the lab (port 8088)
      proxy: { "/api": "http://localhost:8088", "/auth": "http://localhost:8088" },
    },
  };
});
