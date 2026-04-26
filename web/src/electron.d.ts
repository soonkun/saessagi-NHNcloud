interface ElectronAPI {
  readonly isElectron: true;
  setIgnoreMouseEvents(ignore: boolean): void;
  /** 파일 피커 등 네이티브 다이얼로그 종료 후 macOS pet 모드에서 키보드 포커스 복구 */
  restoreFocus(): void;
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
