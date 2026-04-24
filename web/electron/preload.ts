import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  isElectron: true as const,

  setIgnoreMouseEvents: (ignore: boolean): void => {
    ipcRenderer.send("mascot:set-ignore-mouse", { ignore });
  },

  getDisplay: (): Promise<{ width: number; height: number; scaleFactor: number }> =>
    ipcRenderer.invoke("mascot:get-display"),

  quit: (): void => {
    ipcRenderer.send("mascot:quit");
  },

  openDevTools: (): void => {
    ipcRenderer.send("mascot:open-devtools");
  },

  onDisplayChanged: (
    cb: (size: { width: number; height: number }) => void
  ): (() => void) => {
    const handler = (
      _: unknown,
      size: { width: number; height: number }
    ): void => cb(size);
    ipcRenderer.on("display-changed", handler);
    return () => ipcRenderer.removeListener("display-changed", handler);
  },
});
