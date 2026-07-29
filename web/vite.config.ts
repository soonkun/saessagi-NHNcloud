import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// CR-38: Electron(file://) 지원 제거 — 백엔드가 '/'에 서빙하므로 base는 항상 '/'.
// 과거 ELECTRON_BUILD=1을 빠뜨려 흰 화면이 뜨던 함정(E-22)도 이걸로 사라진다.
export default defineConfig({
  base: "/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:12393",
        changeOrigin: true,
      },
      "/client-ws": {
        target: "ws://127.0.0.1:12393",
        ws: true,
      },
      "/avatars": {
        target: "http://127.0.0.1:12393",
        changeOrigin: true,
      },
    },
  },
});
