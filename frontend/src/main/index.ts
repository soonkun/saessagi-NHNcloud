/* eslint-disable no-shadow */
import { app, ipcMain, globalShortcut, desktopCapturer, screen, shell, dialog } from "electron";
import { electronApp, optimizer } from "@electron-toolkit/utils";
import { WindowManager } from "./window-manager";
import { MenuManager } from "./menu-manager";
import { loadPetWindowState, savePetWindowState } from "./pet-window-persistence";
import {
  isBool,
  isFiniteNumber,
  clampToVirtualScreen,
} from "./pet-ipc-validators";

// Electron main 프로세스 로거
const logger = {
  info: (...args: unknown[]): void => { console.info('[M_12]', ...args); },
  warn: (...args: unknown[]): void => { console.warn('[M_12]', ...args); },
};

let windowManager: WindowManager;
let menuManager: MenuManager;
let isQuitting = false;

// M_12 P3 §3.2 Q-9 B안 — drag 시점의 'cursor의 창 내 위치' 저장.
// dragStart에서 기록, dragEnd에서 초기화. dragMove에서 win.setPosition(screenX - x, screenY - y).
let dragOffset: { x: number; y: number } | null = null;

/** 현재 연결된 모든 displays의 bounds를 clampToVirtualScreen에 넘길 형태로 변환. */
function currentDisplayBounds(): { x: number; y: number; width: number; height: number }[] {
  try {
    return screen.getAllDisplays().map((d) => ({
      x: d.bounds.x,
      y: d.bounds.y,
      width: d.bounds.width,
      height: d.bounds.height,
    }));
  } catch {
    return [];
  }
}

function setupIPC(): void {
  ipcMain.handle("get-platform", () => process.platform);

  ipcMain.on("set-ignore-mouse-events", (_event, ignore: boolean) => {
    const window = windowManager.getWindow();
    if (window) {
      windowManager.setIgnoreMouseEvents(ignore);
    }
  });

  ipcMain.on("get-current-mode", (event) => {
    event.returnValue = windowManager.getCurrentMode();
  });

  ipcMain.on("pre-mode-changed", (_event, newMode) => {
    if (newMode === 'window' || newMode === 'pet') {
      menuManager.setMode(newMode);
    }
  });

  ipcMain.on("window-minimize", () => {
    windowManager.getWindow()?.minimize();
  });

  ipcMain.on("window-maximize", () => {
    const window = windowManager.getWindow();
    if (window) {
      windowManager.maximizeWindow();
    }
  });

  ipcMain.on("window-close", () => {
    const window = windowManager.getWindow();
    if (window) {
      if (process.platform === "darwin") {
        window.hide();
      } else {
        window.close();
      }
    }
  });

  // electronAPI.quit() — 캐릭터 X 버튼에서 호출
  ipcMain.on("app-quit", () => {
    isQuitting = true;
    app.quit();
  });

  // electronAPI.getDisplay() — CharacterWidget 위치 클램핑용
  ipcMain.handle("get-display", () => {
    const d = screen.getPrimaryDisplay();
    return { width: d.workAreaSize.width, height: d.workAreaSize.height, scaleFactor: d.scaleFactor };
  });

  // electronAPI.openDevTools()
  ipcMain.on("open-dev-tools", () => {
    windowManager.getWindow()?.webContents.openDevTools();
  });

  ipcMain.on(
    "update-component-hover",
    (_event, componentId: string, isHovering: boolean) => {
      windowManager.updateComponentHover(componentId, isHovering);
    },
  );

  ipcMain.handle("get-config-files", () => {
    const configFiles = JSON.parse(localStorage.getItem("configFiles") || "[]");
    menuManager.updateConfigFiles(configFiles);
    return configFiles;
  });

  ipcMain.on("update-config-files", (_event, files) => {
    menuManager.updateConfigFiles(files);
  });

  ipcMain.handle('get-screen-capture', async () => {
    const sources = await desktopCapturer.getSources({ types: ['screen'] });
    return sources[0].id;
  });

  // M_12 P3 — Pet Mode IPC 실배선 (§3.2, §5.2, §9.4, Q-9 B안, Q-12)
  // 채널 접두사: 스펙 §9.4 L397 "pet:" 준수.
  // 모든 핸들러는 §9.4·§10.3에 따라 입력값 범위 검증을 수행 (boolean·finite number).

  // pet:enable → windowManager.setWindowMode('pet') + 저장 위치 복원 (virtual screen clamp)
  ipcMain.handle('pet:enable', () => {
    logger.info('[PetMode] enable');
    windowManager.setWindowMode('pet');
    // 저장된 위치를 다음 틱에서 복원 (upstream setWindowModePet 가상 스크린 span 완료 후).
    // MAJOR-4: 현재 virtual screen 범위 밖이면 복원 생략 (모니터 토폴로지 변경 대비).
    setTimeout(() => {
      const state = loadPetWindowState();
      if (!state) return;
      const clamped = clampToVirtualScreen(state.x, state.y, currentDisplayBounds());
      if (!clamped) {
        logger.warn(
          `[PetMode] saved position x=${state.x} y=${state.y} out of virtual screen; skip restore`,
        );
        return;
      }
      const win = windowManager.getWindow();
      win?.setPosition(clamped.x, clamped.y);
      logger.info(`[PetMode] restored position x=${clamped.x} y=${clamped.y}`);
    }, 600);
  });

  // pet:disable → windowManager.setWindowMode('window')
  ipcMain.handle('pet:disable', () => {
    logger.info('[PetMode] disable');
    windowManager.setWindowMode('window');
  });

  // pet:setClickThrough(on, forward) → win.setIgnoreMouseEvents(on, {forward})
  ipcMain.handle('pet:setClickThrough', (_event, on: unknown, forward: unknown) => {
    if (!isBool(on) || !isBool(forward)) {
      logger.warn(`[PetMode] setClickThrough invalid args: on=${on} forward=${forward}`);
      throw new Error('pet:setClickThrough: args must be boolean');
    }
    logger.info(`[PetMode] setClickThrough on=${on} forward=${forward}`);
    const win = windowManager.getWindow();
    if (!win) return;
    if (on) {
      win.setIgnoreMouseEvents(true, { forward });
    } else {
      win.setIgnoreMouseEvents(false);
    }
  });

  // pet:setAlwaysOnTop(on) → win.setAlwaysOnTop(on)
  ipcMain.handle('pet:setAlwaysOnTop', (_event, on: unknown) => {
    if (!isBool(on)) {
      logger.warn(`[PetMode] setAlwaysOnTop invalid arg: on=${on}`);
      throw new Error('pet:setAlwaysOnTop: on must be boolean');
    }
    logger.info(`[PetMode] setAlwaysOnTop on=${on}`);
    const win = windowManager.getWindow();
    win?.setAlwaysOnTop(on, on ? 'screen-saver' : undefined);
  });

  // pet:dragStart({x, y}) — cursor의 창 내 위치 저장, 즉시 수행 없음 (Q-9 B안)
  ipcMain.handle('pet:dragStart', (_event, payload: unknown) => {
    if (
      !payload ||
      typeof payload !== 'object' ||
      !isFiniteNumber((payload as { x: unknown }).x) ||
      !isFiniteNumber((payload as { y: unknown }).y)
    ) {
      logger.warn(`[PetMode] dragStart invalid payload: ${JSON.stringify(payload)}`);
      throw new Error('pet:dragStart: payload.x and payload.y must be finite numbers');
    }
    const { x, y } = payload as { x: number; y: number };
    logger.info(`[PetMode] dragStart x=${x} y=${y}`);
    dragOffset = { x, y };
  });

  // pet:dragMove({screenX, screenY}) — 저장된 offset으로 창 이동 (Q-9 B안)
  ipcMain.handle('pet:dragMove', (_event, payload: unknown) => {
    if (
      !payload ||
      typeof payload !== 'object' ||
      !isFiniteNumber((payload as { screenX: unknown }).screenX) ||
      !isFiniteNumber((payload as { screenY: unknown }).screenY)
    ) {
      logger.warn(`[PetMode] dragMove invalid payload: ${JSON.stringify(payload)}`);
      throw new Error('pet:dragMove: payload.screenX and payload.screenY must be finite numbers');
    }
    if (!dragOffset) {
      logger.warn('[PetMode] dragMove called without dragStart; no-op');
      return;
    }
    const { screenX, screenY } = payload as { screenX: number; screenY: number };
    const win = windowManager.getWindow();
    if (!win) return;
    const newX = Math.round(screenX - dragOffset.x);
    const newY = Math.round(screenY - dragOffset.y);
    win.setPosition(newX, newY);
  });

  // M_12 P4 §8.3.2 — shell:openPath (로컬 파일만 허용, 보안 검증)
  ipcMain.handle('shell:openPath', async (_event, absolutePath: unknown): Promise<string> => {
    if (typeof absolutePath !== 'string' || absolutePath.length === 0) {
      throw new Error('shell:openPath: absolutePath must be non-empty string');
    }
    // 보안: http/https/file URL 및 Windows UNC 경로 거부 (로컬 절대 경로만)
    if (/^(https?:|file:|\\\\)/i.test(absolutePath)) {
      throw new Error('shell:openPath: remote/UNC paths not allowed');
    }
    logger.info(`[Shell] openPath: ${absolutePath}`);
    return shell.openPath(absolutePath);
  });

  // pet:dragEnd() — offset 초기화 + 최종 위치 영속화 (Q-12)
  ipcMain.handle('pet:dragEnd', () => {
    logger.info('[PetMode] dragEnd');
    dragOffset = null;
    const win = windowManager.getWindow();
    if (win) {
      const bounds = win.getBounds();
      savePetWindowState(bounds);
    }
  });
}

app.whenReady().then(() => {
  electronApp.setAppUserModelId("com.electron");

  windowManager = new WindowManager();
  menuManager = new MenuManager((mode) => windowManager.setWindowMode(mode));

  // electronAPI.onDisplayChanged — 디스플레이 변경 시 렌더러에 통보
  const sendDisplayChanged = (): void => {
    const d = screen.getPrimaryDisplay();
    windowManager.getWindow()?.webContents.send("display-changed", {
      width: d.workAreaSize.width,
      height: d.workAreaSize.height,
    });
  };
  screen.on("display-metrics-changed", sendDisplayChanged);
  screen.on("display-added", sendDisplayChanged);
  screen.on("display-removed", sendDisplayChanged);

  const window = windowManager.createWindow({
    titleBarOverlay: {
      color: "#111111",
      symbolColor: "#FFFFFF",
      height: 30,
    },
  });

  // 회의록 다운로드: 파일 형식에 따라 save-as 다이얼로그 표시
  window.webContents.session.on('will-download', (_event, item) => {
    const filename = item.getFilename() || '';
    const isTxt = filename.toLowerCase().endsWith('.txt');
    const defaultName = filename || (isTxt ? '파일.txt' : '회의결과보고서.hwpx');
    const savePath = dialog.showSaveDialogSync(window, {
      defaultPath: defaultName,
      filters: isTxt
        ? [{ name: '텍스트 파일', extensions: ['txt'] }]
        : [{ name: '한글 문서', extensions: ['hwpx'] }],
    });
    if (savePath) {
      item.setSavePath(savePath);
    } else {
      item.cancel();
    }
  });
  menuManager.createTray();

  window.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      window.hide();
    }
    return false;
  });

  // if (process.env.NODE_ENV === "development") {
  //   globalShortcut.register("F12", () => {
  //     const window = windowManager.getWindow();
  //     if (!window) return;

  //     if (window.webContents.isDevToolsOpened()) {
  //       window.webContents.closeDevTools();
  //     } else {
  //       window.webContents.openDevTools();
  //     }
  //   });
  // }

  setupIPC();

  app.on("activate", () => {
    const window = windowManager.getWindow();
    if (window) {
      window.show();
    }
  });

  app.on("browser-window-created", (_, window) => {
    optimizer.watchWindowShortcuts(window);
  });

  app.on('web-contents-created', (_, contents) => {
    contents.session.setPermissionRequestHandler((webContents, permission, callback) => {
      if (permission === 'media') {
        callback(true);
      } else {
        callback(false);
      }
    });
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  isQuitting = true;
  menuManager.destroy();
  globalShortcut.unregisterAll();
});
