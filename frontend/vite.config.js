import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
    plugins: [react()],
    server: { proxy: { "/api": "http://localhost:8000" } },
    build: { outDir: "dist" },
    test: {
        environment: "jsdom",
        setupFiles: ["./src/test/setup.ts"],
        exclude: [...configDefaults.exclude, "e2e/**"],
    },
});
