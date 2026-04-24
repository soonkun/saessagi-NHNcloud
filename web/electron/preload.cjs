'use strict';

// CommonJS preload — Electron loads this before the renderer.
// Keeping as .cjs avoids ESM-in-preload ambiguity in Electron 30 (B1 fix).
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,

  setIgnoreMouseEvents: (ignore) => {
    ipcRenderer.send('mascot:set-ignore-mouse', { ignore });
  },

  getDisplay: () => ipcRenderer.invoke('mascot:get-display'),

  quit: () => {
    ipcRenderer.send('mascot:quit');
  },

  openDevTools: () => {
    ipcRenderer.send('mascot:open-devtools');
  },

  onDisplayChanged: (cb) => {
    const handler = (_e, size) => cb(size);
    ipcRenderer.on('display-changed', handler);
    return () => ipcRenderer.removeListener('display-changed', handler);
  },

  // Tray "채팅 열기" → open chat panel (M4)
  onOpenChat: (cb) => {
    const handler = () => cb();
    ipcRenderer.on('tray:open-chat', handler);
    return () => ipcRenderer.removeListener('tray:open-chat', handler);
  },
});
