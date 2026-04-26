import {
  app,
  BrowserWindow,
  ipcMain,
  screen,
  Tray,
  Menu,
  nativeImage,
} from "electron";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { spawn, type ChildProcess } from "child_process";
import * as net from "net";

const __dirname = dirname(fileURLToPath(import.meta.url));
const isDev = process.env.NODE_ENV === "development";

// ────────────────────────────────────────────────────────────
// 백엔드 자동 시작
// ────────────────────────────────────────────────────────────

let backendProc: ChildProcess | null = null;

function isPortOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.createConnection({ port, host: "127.0.0.1" });
    s.on("connect", () => { s.destroy(); resolve(true); });
    s.on("error", () => resolve(false));
  });
}

async function waitForPort(port: number, maxMs = 30_000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (await isPortOpen(port)) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function startBackendIfNeeded(): Promise<void> {
  if (await isPortOpen(12393)) return; // 이미 실행 중

  // .app 번들 기준: Contents/MacOS/새싹이 → ../../../ → .app 상위
  // 개발 시: web/electron-dist/ → ../../ → ai-assistant/
  const appDir = app.isPackaged
    ? join(app.getAppPath(), "..", "..", "..", "..", "..") // .app/Contents/Resources/app → 5단계 위
    : join(__dirname, "..", "..");                        // web/electron-dist → ai-assistant/

  const startSh = join(appDir, "start.sh");

  backendProc = spawn("bash", [startSh], {
    cwd: appDir,
    detached: false,
    stdio: ["ignore", "pipe", "pipe"],
  });

  backendProc.stdout?.on("data", (d: Buffer) =>
    process.stdout.write(`[backend] ${d}`)
  );
  backendProc.stderr?.on("data", (d: Buffer) =>
    process.stderr.write(`[backend] ${d}`)
  );

  await waitForPort(12393, 60_000);
}

// 사용자 제스처 없이도 speechSynthesis / AudioContext 자동 재생 허용
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

let win: BrowserWindow | null = null;
let tray: Tray | null = null;

// ────────────────────────────────────────────────────────────
// Single instance lock
// ────────────────────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}

app.on("second-instance", () => {
  if (win) {
    if (win.isMinimized()) win.restore();
    win.focus();
  }
});

// ────────────────────────────────────────────────────────────
// Tray icon helper (B3 fix: use real icon, not createEmpty)
// ────────────────────────────────────────────────────────────
function createTrayIcon(): Electron.NativeImage {
  const iconPath = isDev
    ? join(__dirname, "../public/avatars/neutral.png")
    : join(app.getAppPath(), "dist/avatars/neutral.png");

  try {
    const img = nativeImage.createFromPath(iconPath);
    if (img.isEmpty()) throw new Error("icon file not found or empty");
    const size = process.platform === "darwin" ? 18 : 32;
    return img.resize({ width: size, height: size });
  } catch {
    // Minimal fallback: a 16×16 opaque green square so the tray item is visible
    return nativeImage.createFromDataURL(
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/" +
      "9hAAAAHElEQVQ4jWNg+M9QzwAFJGMGwYBRSygBAAD//wIABf4B/wAAAABJRU5ErkJggg=="
    );
  }
}

// ────────────────────────────────────────────────────────────
// Window creation
// ────────────────────────────────────────────────────────────
function createWindow(): void {
  const display = screen.getPrimaryDisplay();
  const { x, y, width, height } = display.bounds;

  win = new BrowserWindow({
    x,
    y,
    width,
    height,
    transparent: true,
    frame: false,
    hasShadow: false,
    backgroundColor: "#00000000",
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    focusable: true,
    acceptFirstMouse: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // B1 fix: reference .cjs preload — no ESM/CJS ambiguity
      preload: join(__dirname, "preload.cjs"),
    },
  });

  // Platform-specific always-on-top and workspace settings
  if (process.platform === "darwin") {
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    win.setAlwaysOnTop(true, "floating");
  } else {
    win.setAlwaysOnTop(true, "screen-saver");
  }

  // CRITICAL: initial state is click-through ON with forward:true
  win.setIgnoreMouseEvents(true, { forward: true });

  // Load app
  if (isDev) {
    void win.loadURL("http://localhost:5173");
    win.webContents.openDevTools({ mode: "detach" });
  } else {
    void win.loadFile(join(__dirname, "../dist/index.html"));
  }
}

// ────────────────────────────────────────────────────────────
// Tray
// ────────────────────────────────────────────────────────────
function buildMenu(): Electron.Menu {
  return Menu.buildFromTemplate([
    {
      label: "채팅 열기",
      click: () => {
        win?.show();
        win?.webContents.send("tray:open-chat");
        tray?.setContextMenu(buildMenu());
      },
    },
    { type: "separator" },
    {
      label: win?.isVisible() ? "새싹이 숨기기" : "새싹이 보이기",
      click: () => {
        if (win) {
          if (win.isVisible()) {
            win.hide();
          } else {
            win.show();
          }
        }
        tray?.setContextMenu(buildMenu());
      },
    },
    { type: "separator" },
    {
      label: "종료",
      click: () => {
        app.quit();
      },
    },
  ]);
}

function createTray(): void {
  tray = new Tray(createTrayIcon());
  tray.setToolTip("새싹이");
  tray.setContextMenu(buildMenu());
  tray.on("click", () => {
    if (win) {
      if (win.isVisible()) {
        win.focus();
      } else {
        win.show();
      }
    }
  });
}

// ────────────────────────────────────────────────────────────
// IPC handlers
// ────────────────────────────────────────────────────────────

// CRITICAL: always use { forward: true } so mousemove still arrives
// at renderer even when ignore is true
ipcMain.on(
  "mascot:set-ignore-mouse",
  (_e, { ignore }: { ignore: boolean }) => {
    if (!win) return;
    win.setIgnoreMouseEvents(ignore, { forward: true });
  }
);

ipcMain.handle("mascot:get-display", () => {
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.bounds;
  return { width, height, scaleFactor: display.scaleFactor };
});

ipcMain.on("mascot:quit", () => {
  app.quit();
});

ipcMain.on("mascot:open-devtools", () => {
  if (!win) return;
  win.webContents.openDevTools({ mode: "detach" });
});

// ────────────────────────────────────────────────────────────
// Display change handler
// ────────────────────────────────────────────────────────────
function onDisplayChanged(): void {
  if (!win) return;
  const display = screen.getPrimaryDisplay();
  const { x, y, width, height } = display.bounds;
  win.setBounds({ x, y, width, height });
  win.webContents.send("display-changed", { width, height });
}

// ────────────────────────────────────────────────────────────
// App lifecycle
// ────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // Hide dock icon on macOS (tray-only app)
  if (process.platform === "darwin" && app.dock) {
    app.dock.hide();
  }

  if (!isDev) {
    await startBackendIfNeeded();
  }

  createWindow();
  createTray();

  screen.on("display-metrics-changed", onDisplayChanged);
  screen.on("display-added", onDisplayChanged);
  screen.on("display-removed", onDisplayChanged);
});

// Tray keeps app alive — do not quit on window-all-closed
app.on("window-all-closed", () => {
  // intentionally empty: tray keeps the process alive
});

app.on("before-quit", () => {
  win = null;
  if (backendProc) {
    backendProc.kill();
    backendProc = null;
  }
});
