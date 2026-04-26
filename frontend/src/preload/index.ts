/* eslint-disable @typescript-eslint/ban-ts-comment */
import electron from 'electron';
const { contextBridge, ipcRenderer, desktopCapturer } = electron;
import { electronAPI } from '@electron-toolkit/preload';
import { ConfigFile } from '../main/menu-manager';

declare global {
  interface Window {
    electron: typeof electronAPI;
    // @ts-ignore
    api: typeof api;
  }
}

// M_12 P4 §8.3.2 — shell IPC API (로컬 파일 열기)
const shellApi = {
  openPath: (absolutePath: string): Promise<string> =>
    ipcRenderer.invoke('shell:openPath', absolutePath),
};

// 새싹이 web UI(web/src)가 사용하는 window.electronAPI
const saessagiElectronApi = {
  isElectron: true as const,
  quit: (): void => ipcRenderer.send("app-quit"),
  setIgnoreMouseEvents: (ignore: boolean): void =>
    ipcRenderer.send("set-ignore-mouse-events", ignore),
  getDisplay: (): Promise<{ width: number; height: number; scaleFactor: number }> =>
    ipcRenderer.invoke("get-display"),
  openDevTools: (): void => ipcRenderer.send("open-dev-tools"),
  onDisplayChanged: (
    cb: (size: { width: number; height: number }) => void
  ): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, size: { width: number; height: number }): void => cb(size);
    ipcRenderer.on("display-changed", handler);
    return () => ipcRenderer.removeListener("display-changed", handler);
  },
  onOpenChat: (cb: () => void): (() => void) => {
    const handler = (): void => cb();
    ipcRenderer.on("open-chat", handler);
    return () => ipcRenderer.removeListener("open-chat", handler);
  },
};

// M_12 P3 — petMode IPC API (§5.2, Q-9 B안)
// dragMove·dragEnd 2종 추가: mouseup 감지를 위해 dragStart만으로는 dragEnd 시점을 알 수 없음.
// renderer에서 window.mousemove / mouseup 이벤트를 받아 IPC로 전달하는 구조 필요.
const petModeApi = {
  enable: (): Promise<void> => ipcRenderer.invoke('pet:enable'),
  disable: (): Promise<void> => ipcRenderer.invoke('pet:disable'),
  setClickThrough: (on: boolean, forward: boolean): Promise<void> =>
    ipcRenderer.invoke('pet:setClickThrough', on, forward),
  setAlwaysOnTop: (on: boolean): Promise<void> =>
    ipcRenderer.invoke('pet:setAlwaysOnTop', on),
  dragStart: (payload: { x: number; y: number }): Promise<void> =>
    ipcRenderer.invoke('pet:dragStart', payload),
  dragMove: (payload: { screenX: number; screenY: number }): Promise<void> =>
    ipcRenderer.invoke('pet:dragMove', payload),
  dragEnd: (): Promise<void> => ipcRenderer.invoke('pet:dragEnd'),
};

const api = {
  setIgnoreMouseEvents: (ignore: boolean) => {
    ipcRenderer.send('set-ignore-mouse-events', ignore);
  },
  toggleForceIgnoreMouse: () => {
    ipcRenderer.send('toggle-force-ignore-mouse');
  },
  onForceIgnoreMouseChanged: (callback: (isForced: boolean) => void) => {
    const handler = (_event: any, isForced: boolean) => callback(isForced);
    ipcRenderer.on('force-ignore-mouse-changed', handler);
    return () => ipcRenderer.removeListener('force-ignore-mouse-changed', handler);
  },
  showContextMenu: () => {
    console.log('Preload showContextMenu');
    ipcRenderer.send('show-context-menu');
  },
  onModeChanged: (callback: (mode: string) => void) => {
    ipcRenderer.on('mode-changed', (_, mode) => callback(mode));
  },
  onMicToggle: (callback: () => void) => {
    const handler = (_event: any) => callback();
    ipcRenderer.on('mic-toggle', handler);
    return () => ipcRenderer.removeListener('mic-toggle', handler);
  },
  onInterrupt: (callback: () => void) => {
    const handler = (_event: any) => callback();
    ipcRenderer.on('interrupt', handler);
    return () => ipcRenderer.removeListener('interrupt', handler);
  },
  updateComponentHover: (componentId: string, isHovering: boolean) => {
    ipcRenderer.send('update-component-hover', componentId, isHovering);
  },
  onToggleInputSubtitle: (callback: () => void) => {
    const handler = (_event: any) => callback();
    ipcRenderer.on('toggle-input-subtitle', handler);
    return () => ipcRenderer.removeListener('toggle-input-subtitle', handler);
  },
  onToggleScrollToResize: (callback: () => void) => {
    const handler = (_event: any) => callback();
    ipcRenderer.on('toggle-scroll-to-resize', handler);
    return () => ipcRenderer.removeListener('toggle-scroll-to-resize', handler);
  },
  onSwitchCharacter: (callback: (filename: string) => void) => {
    const handler = (_event: any, filename: string) => callback(filename);
    ipcRenderer.on('switch-character', handler);
    return () => ipcRenderer.removeListener('switch-character', handler);
  },
  setMode: (mode: 'window' | 'pet') => {
    ipcRenderer.send('pre-mode-changed', mode);
  },
  getConfigFiles: () => ipcRenderer.invoke('get-config-files'),
  updateConfigFiles: (files: ConfigFile[]) => {
    ipcRenderer.send('update-config-files', files);
  },
};

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', {
      ...electronAPI,
      desktopCapturer: {
        getSources: (options) => desktopCapturer.getSources(options),
      },
      ipcRenderer: {
        invoke: (channel, ...args) => ipcRenderer.invoke(channel, ...args),
        on: (channel, func) => ipcRenderer.on(channel, func),
        once: (channel, func) => ipcRenderer.once(channel, func),
        removeListener: (channel, func) => ipcRenderer.removeListener(channel, func),
        removeAllListeners: (channel) => ipcRenderer.removeAllListeners(channel),
        send: (channel, ...args) => ipcRenderer.send(channel, ...args),
      },
      process: {
        platform: process.platform,
      },
    });
    contextBridge.exposeInMainWorld('api', api);
    // M_12 §9.4: petMode IPC 노출 (contextBridge.exposeInMainWorld)
    contextBridge.exposeInMainWorld('petMode', petModeApi);
    // M_12 P4 §8.3.2: shell IPC 노출
    contextBridge.exposeInMainWorld('shell', shellApi);
    // 새싹이 web UI용 electronAPI (quit, setIgnoreMouseEvents, getDisplay 등)
    contextBridge.exposeInMainWorld('electronAPI', saessagiElectronApi);
  } catch (error) {
    console.error(error);
  }
} else {
  window.electron = electronAPI;
  (window as any).api = api;
  (window as any).petMode = petModeApi;
  (window as any).shell = shellApi;
  (window as any).electronAPI = saessagiElectronApi;
}
