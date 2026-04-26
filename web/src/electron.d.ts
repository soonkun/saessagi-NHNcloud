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

interface PetModeAPI {
  enable(): Promise<void>;
  disable(): Promise<void>;
  setClickThrough(on: boolean, forward: boolean): Promise<void>;
  setAlwaysOnTop(on: boolean): Promise<void>;
  dragStart(payload: { x: number; y: number }): Promise<void>;
  dragMove(payload: { screenX: number; screenY: number }): Promise<void>;
  dragEnd(): Promise<void>;
}

interface Window {
  electronAPI?: ElectronAPI;
  petMode?: PetModeAPI;
}
