import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Electron 프로덕션 빌드 시 ELECTRON_BUILD=1 로 실행 → base를 './'로 설정
const isElectronBuild = process.env.ELECTRON_BUILD === "1";

export default defineConfig({
  base: isElectronBuild ? "./" : "/",
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
