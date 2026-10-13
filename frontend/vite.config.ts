import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
	plugins: [tailwindcss()],
	resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
	server: {
		proxy: {
			"/api/auth": "http://localhost:3005",
			"/api/deepteam": "http://localhost:5002",
			"/api": "http://localhost:8000",
		},
	},
	test: {
		include: ["src/**/*.test.{ts,tsx}"],
		environment: "jsdom",
		setupFiles: ["./src/test-setup.ts"],
	},
});
