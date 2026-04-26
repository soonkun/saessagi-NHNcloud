import { resolve } from 'path';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import react from '@vitejs/plugin-react';
import { viteStaticCopy } from 'vite-plugin-static-copy'
import { normalizePath } from 'vite';

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
  },
  preload: {
    // sandbox: true 환경에서는 require()로 외부 모듈을 로드할 수 없으므로
    // @electron-toolkit/preload를 번들에 포함시킴 (externalize 제외)
    plugins: [externalizeDepsPlugin({ exclude: ['@electron-toolkit/preload'] })],
  },
  renderer: {
    resolve: {
      alias: {
        '@': resolve('src/renderer/src'),
        // M_12 §3.3 DROP: WebSDK/CubismSDK/MotionSync aliases 제거됨
        "/src": resolve("src/renderer/src"),
      },
    },
    plugins: [
      viteStaticCopy({
        targets: [
          {
            src: normalizePath(resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js')),
            dest: './libs/',
          },
          {
            src: normalizePath(resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx')),
            dest: './libs/',
          },
          {
            src: normalizePath(resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx')),
            dest: './libs/',
          },
          {
            src: normalizePath(resolve(__dirname, 'node_modules/onnxruntime-web/dist/*.wasm')),
            dest: './libs/',
          },
          // M_12 §3.3 DROP: live2dcubismcore.js 복사 제거됨
          // M_12 P4 §8.3.3 — pdfjs-dist worker (offline, CDN 금지)
          {
            src: normalizePath(resolve(__dirname, 'node_modules/pdfjs-dist/build/pdf.worker.min.mjs')),
            dest: './assets/pdfjs/',
          },
        ],
      }),
      react(),
    ],
    build: {
      rollupOptions: {
        onwarn(warning, warn) {
          if (warning.message.includes('onnxruntime')) {
            return;
          }
          warn(warning);
        },
      },
    },
  },
});
