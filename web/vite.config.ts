import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const backend = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1", // localhost only: partner data never on the network
    port: 5173,
    proxy: { "/api": backend, "/media": backend, "/out": backend },
  },
});
