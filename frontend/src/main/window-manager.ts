import {
  BrowserWindow, screen, shell, ipcMain,
} from 'electron';
import { join } from 'path';
import { is } from '@electron-toolkit/utils';

const isMac = process.platform === 'darwin';
const isWindows = process.platform === 'win32';
const isLinux = process.platform === 'linux';

export class WindowManager {
  private window: BrowserWindow | null = null;

  private windowedBounds: {
    x: number;
    y: number;
    width: number;
    height: number;
  } | null = null;

  private hoveringComponents: Set<string> = new Set();

  private currentMode: 'window' | 'pet' = 'window';

  // Track if mouse events are forcibly ignored
  private forceIgnoreMouse = false;

  // Linux 전용 커서 폴러 (pet 모드).
  // setIgnoreMouseEvents(true, {forward:true})의 forward는 @platform darwin,win32 —
  // Linux(WSLg 포함)에서는 click-through 중 mousemove가 렌더러에 전달되지 않아
  // clickthrough.ts의 evaluate()가 영영 실행되지 않고 창이 영구 클릭스루로 고착된다.
  // 우회: 커서 위치를 폴링해 합성 mouseMove를 주입 → 기존 evaluate() 경로가 그대로 동작.
  // 주의: WSLg에서 screen.getCursorScreenPoint()는 실제 마우스를 따라가지 않는
  // 고착값을 반환한다(E-53). X 서버 QueryPointer는 실입력을 정확히 추적하므로
  // x11 패키지로 X 서버에 직접 묻는다.
  private linuxCursorPoller: ReturnType<typeof setInterval> | null = null;

  private x11Client: {
    client: { QueryPointer: (root: number, cb: (err: unknown, p: { rootX: number; rootY: number }) => void) => void };
    root: number;
  } | null = null;

  private x11InitFailed = false;

  private async getLinuxCursorPoint(): Promise<{ x: number; y: number } | null> {
    if (this.x11InitFailed) return null;
    if (!this.x11Client) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
        const x11 = require('x11');
        const display = await new Promise<{ client: never; screen: { root: number }[] }>(
          (resolve, reject) => {
            x11.createClient((err: unknown, d: never) => (err ? reject(err) : resolve(d)));
          },
        );
        this.x11Client = { client: display.client, root: display.screen[0].root };
      } catch (err) {
        console.warn('[window-manager] X11 connect failed; cursor poller falls back to Electron API:', err);
        this.x11InitFailed = true;
        return null;
      }
    }
    return new Promise((resolve) => {
      this.x11Client!.client.QueryPointer(this.x11Client!.root, (err, p) => {
        resolve(err ? null : { x: p.rootX, y: p.rootY });
      });
    });
  }

  constructor() {
    ipcMain.on('renderer-ready-for-mode-change', (_event, newMode) => {
      if (newMode === 'pet') {
        setTimeout(() => {
          this.continueSetWindowModePet();
        }, 500);
      } else {
        setTimeout(() => {
          this.continueSetWindowModeWindow();
        }, 500);
      }
    });

    ipcMain.on('mode-change-rendered', () => {
      this.window?.setOpacity(1);
    });

    ipcMain.on('window-unfullscreen', () => {
      const window = this.getWindow();
      if (window && window.isFullScreen()) {
        window.setFullScreen(false);
      }
    });

    // Handle toggle force ignore mouse events from renderer
    ipcMain.on('toggle-force-ignore-mouse', () => {
      this.toggleForceIgnoreMouse();
    });
  }

  createWindow(options: Electron.BrowserWindowConstructorOptions): BrowserWindow {
    this.window = new BrowserWindow({
      width: 900,
      height: 670,
      show: false,
      transparent: true,
      backgroundColor: '#ffffff',
      autoHideMenuBar: true,
      frame: false,
      icon: process.platform === 'win32'
        ? join(__dirname, '../../resources/icon.ico')
        : join(__dirname, '../../resources/icon.png'),
      ...(isMac ? { titleBarStyle: 'hiddenInset' } : {}),
      webPreferences: {
        preload: join(__dirname, '../preload/index.js'),
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
      },
      hasShadow: false,
      paintWhenInitiallyHidden: true,
      ...options,
    });

    this.setupWindowEvents();
    this.loadContent();

    this.window.on('enter-full-screen', () => {
      this.window?.webContents.send('window-fullscreen-change', true);
    });

    this.window.on('leave-full-screen', () => {
      this.window?.webContents.send('window-fullscreen-change', false);
    });

    return this.window;
  }

  private setupWindowEvents(): void {
    if (!this.window) return;

    this.window.on('ready-to-show', () => {
      this.window?.show();
      this.window?.webContents.send(
        'window-maximized-change',
        this.window.isMaximized(),
      );
    });

    this.window.on('maximize', () => {
      this.window?.webContents.send('window-maximized-change', true);
    });

    this.window.on('unmaximize', () => {
      this.window?.webContents.send('window-maximized-change', false);
    });

    this.window.on('resize', () => {
      const window = this.getWindow();
      if (window) {
        const bounds = window.getBounds();
        const { width, height } = screen.getPrimaryDisplay().workArea;
        const isMaximized = bounds.width >= width && bounds.height >= height;
        window.webContents.send('window-maximized-change', isMaximized);
      }
    });

    this.window.webContents.setWindowOpenHandler((details) => {
      shell.openExternal(details.url);
      return { action: 'deny' };
    });
  }

  private loadContent(): void {
    if (!this.window) return;

    if (is.dev && process.env.ELECTRON_RENDERER_URL) {
      // electron-vite dev 전용 (ELECTRON_RENDERER_URL은 vite dev server 주소에만 허용)
      this.window.loadURL(process.env.ELECTRON_RENDERER_URL);
    } else {
      // 새싹이 web UI를 로컬 파일로 로드 — file:// 프로토콜 (HTTP URL 절대 금지)
      // __dirname = out/main/ → ../../../web/dist = <project-root>/web/dist
      this.window.loadFile(join(__dirname, '../../../web/dist/index.html'));
    }
  }

  setWindowMode(mode: 'window' | 'pet'): void {
    if (!this.window) return;

    this.currentMode = mode;
    this.window.setOpacity(0);

    if (mode === 'window') {
      this.stopLinuxCursorPoller();
      this.setWindowModeWindow();
    } else {
      this.setWindowModePet();
      this.startLinuxCursorPoller();
    }
  }

  private startLinuxCursorPoller(): void {
    if (!isLinux || this.linuxCursorPoller) return;
    let inFlight = false;
    this.linuxCursorPoller = setInterval(() => {
      const win = this.window;
      if (!win || win.isDestroyed() || inFlight) return;
      inFlight = true;
      this.getLinuxCursorPoint()
        .then((pt) => {
          inFlight = false;
          const w = this.window;
          if (!w || w.isDestroyed()) return;
          // X 연결 실패 시 Electron API로 폴백 (WSLg에선 고착값이지만 없는 것보단 낫다)
          const p = pt ?? screen.getCursorScreenPoint();
          const b = w.getBounds();
          const x = p.x - b.x;
          const y = p.y - b.y;
          if (x < 0 || y < 0 || x >= b.width || y >= b.height) return;
          w.webContents.sendInputEvent({ type: 'mouseMove', x, y });
        })
        .catch(() => {
          inFlight = false;
        });
    }, 80);
  }

  private stopLinuxCursorPoller(): void {
    if (this.linuxCursorPoller) {
      clearInterval(this.linuxCursorPoller);
      this.linuxCursorPoller = null;
    }
  }

  private setWindowModeWindow(): void {
    if (!this.window) return;

    this.window.setAlwaysOnTop(false);
    this.window.setIgnoreMouseEvents(false);
    this.window.setSkipTaskbar(false);
    this.window.setResizable(true);
    this.window.setFocusable(true);
    this.window.setAlwaysOnTop(false);

    // setBackgroundColor('#ffffff') 호출 금지 — 한 번 opaque로 설정하면 macOS에서
    // native window backing이 굳어 다시 #00000000로 되돌려도 흰 화면이 잔존.
    // 색상은 CSS(DesktopView outer의 background)로만 처리 — transparent backing 유지.
    this.window.webContents.send('pre-mode-changed', 'window');
  }

  private continueSetWindowModeWindow(): void {
    if (!this.window) return;
    // pet 모드는 가상 스크린 전체 크기로 저장되므로 windowedBounds를 신뢰할 수 없다.
    // 항상 primary display work area의 80%를 차지하는 큰 창으로 시작.
    const { width: dw, height: dh, x: dx, y: dy } = screen.getPrimaryDisplay().workArea;
    const w = Math.round(dw * 0.8);
    const h = Math.round(dh * 0.85);
    this.window.setBounds({
      x: dx + Math.round((dw - w) / 2),
      y: dy + Math.round((dh - h) / 2),
      width: w,
      height: h,
    });

    if (isMac) {
      this.window.setWindowButtonVisibility(true);
      this.window.setVisibleOnAllWorkspaces(false, {
        visibleOnFullScreen: false,
      });
    }

    this.window?.setIgnoreMouseEvents(false, { forward: true });

    this.window.webContents.send('mode-changed', 'window');
    this.window.setOpacity(1);
  }

  private setWindowModePet(): void {
    if (!this.window) return;

    this.windowedBounds = this.window.getBounds();

    if (this.window.isFullScreen()) {
      this.window.setFullScreen(false);
    }

    this.window.setBackgroundColor('#00000000');

    this.window.setAlwaysOnTop(true, 'screen-saver');
    this.window.setPosition(0, 0);

    this.window.webContents.send('pre-mode-changed', 'pet');
  }

  private continueSetWindowModePet(): void {
    if (!this.window) return;
    // Calculate the bounding rectangle that covers all connected displays.
    // This allows the transparent pet-mode window to span across monitors,
    // so the avatar can be dragged freely between them.
    const displays = screen.getAllDisplays();
    const minX = Math.min(...displays.map((d) => d.bounds.x));
    const minY = Math.min(...displays.map((d) => d.bounds.y));
    const maxX = Math.max(...displays.map((d) => d.bounds.x + d.bounds.width));
    const maxY = Math.max(...displays.map((d) => d.bounds.y + d.bounds.height));
    const combinedWidth = maxX - minX;
    const combinedHeight = maxY - minY;

    // Resize and position the window to cover the entire virtual screen
    // so the avatar is not clipped when dragged to a second monitor.
    this.window.setBounds({
      x: minX,
      y: minY,
      width: combinedWidth,
      height: combinedHeight,
    });

    if (isMac) this.window.setWindowButtonVisibility(false);
    this.window.setResizable(false);
    this.window.setSkipTaskbar(true);
    // Windows에서 setFocusable(false)는 키보드 입력을 완전히 차단하므로 생략
    if (!isWindows) this.window.setFocusable(false);

    this.window.setIgnoreMouseEvents(true, { forward: true });
    if (isMac) {
      this.window.setVisibleOnAllWorkspaces(true, {
        visibleOnFullScreen: true,
      });
    }

    this.window.webContents.send('mode-changed', 'pet');
    // web UI가 mode-change-rendered를 보내지 않을 수 있으므로 직접 opacity 복구
    this.window.setOpacity(1);
  }

  getWindow(): BrowserWindow | null {
    return this.window;
  }

  setIgnoreMouseEvents(ignore: boolean): void {
    if (!this.window) return;
    // forward:true — ignore=true여도 mousemove는 렌더러에 전달
    // (clickthrough.ts의 evaluate()가 실행되도록 macOS도 동일하게 적용)
    if (ignore) {
      this.window.setIgnoreMouseEvents(true, { forward: true });
    } else {
      this.window.setIgnoreMouseEvents(false);
    }
  }

  /**
   * 파일 피커 등 네이티브 다이얼로그 이후 호출.
   * macOS pet 모드에서 setFocusable(false)로 인해 다이얼로그 종료 시
   * Electron 창이 key window 지위를 회복하지 못하는 문제를 해결.
   * 300ms 동안만 focusable=true로 전환 후 복원.
   */
  restoreFocus(): void {
    if (!this.window || this.currentMode !== 'pet') return;
    this.window.setFocusable(true);
    this.window.focus();
    setTimeout(() => {
      if (this.window && this.currentMode === 'pet' && !isWindows) {
        this.window.setFocusable(false);
      }
    }, 300);
  }

  maximizeWindow(): void {
    if (!this.window) return;

    if (this.isWindowMaximized()) {
      if (this.windowedBounds) {
        this.window.setBounds(this.windowedBounds);
        this.windowedBounds = null;
        this.window.webContents.send('window-maximized-change', false);
      }
    } else {
      this.windowedBounds = this.window.getBounds();
      const { width, height } = screen.getPrimaryDisplay().workArea;
      this.window.setBounds({
        x: 0, y: 0, width, height,
      });
      this.window.webContents.send('window-maximized-change', true);
    }
  }

  isWindowMaximized(): boolean {
    if (!this.window) return false;
    const bounds = this.window.getBounds();
    const { width, height } = screen.getPrimaryDisplay().workArea;
    return bounds.width >= width && bounds.height >= height;
  }

  updateComponentHover(componentId: string, isHovering: boolean): void {
    if (this.currentMode === 'window') return;

    // If force ignore is enabled, don't change the mouse ignore state
    if (this.forceIgnoreMouse) return;

    if (isHovering) {
      this.hoveringComponents.add(componentId);
    } else {
      this.hoveringComponents.delete(componentId);
    }

    if (this.window) {
      const shouldIgnore = this.hoveringComponents.size === 0;
      if (shouldIgnore) {
        this.window.setIgnoreMouseEvents(true, { forward: true });
      } else {
        this.window.setIgnoreMouseEvents(false);
        this.window.setFocusable(true);
      }
    }
  }

  // Toggle force ignore mouse events
  toggleForceIgnoreMouse(): void {
    this.forceIgnoreMouse = !this.forceIgnoreMouse;

    // Apply the new setting immediately
    if (this.forceIgnoreMouse) {
      this.window?.setIgnoreMouseEvents(true, { forward: true });
    } else {
      const shouldIgnore = this.hoveringComponents.size === 0;
      if (shouldIgnore) {
        this.window?.setIgnoreMouseEvents(true, { forward: true });
      } else {
        this.window?.setIgnoreMouseEvents(false);
      }
    }

    // Notify renderer about the change
    this.window?.webContents.send('force-ignore-mouse-changed', this.forceIgnoreMouse);
  }

  // Get current force ignore state
  isForceIgnoreMouse(): boolean {
    return this.forceIgnoreMouse;
  }

  // Get current mode
  getCurrentMode(): 'window' | 'pet' {
    return this.currentMode;
  }
}
