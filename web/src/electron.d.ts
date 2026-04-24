interface ElectronAPI {
  readonly isElectron: true;
  setIgnoreMouseEvents(ignore: boolean): void;
  getDisplay(): Promise<{ width: number; height: number; scaleFactor: number }>;
  quit(): void;
  openDevTools(): void;
  onDisplayChanged(
    cb: (size: { width: number; height: number }) => void
  ): () => void;
  onOpenChat(cb: () => void): () => void;
}

interface Window {
  electronAPI?: ElectronAPI;
}
