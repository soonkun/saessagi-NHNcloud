import { defineConfig, externalizeDepsPlugin } from 'electron-vite';

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
  },
  preload: {
    // sandbox: true 환경에서는 require()로 외부 모듈을 로드할 수 없으므로
    // @electron-toolkit/preload를 번들에 포함시킴 (externalize 제외)
    plugins: [externalizeDepsPlugin({ exclude: ['@electron-toolkit/preload'] })],
  },
});
