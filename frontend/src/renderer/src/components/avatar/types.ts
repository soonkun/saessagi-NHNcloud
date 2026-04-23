// M_12 §4 / §5.1 — AvatarRenderer public interface

export type Emotion =
  | 'neutral'
  | 'happy'
  | 'surprised'
  | 'sad'
  | 'worried'
  | 'thinking'
  | 'sleepy'
  | 'study'
  | 'writing';

export const VALID_EMOTIONS: readonly Emotion[] = [
  'neutral',
  'happy',
  'surprised',
  'sad',
  'worried',
  'thinking',
  'sleepy',
  'study',
  'writing',
] as const;

export interface AvatarRendererErrorEvent {
  code: 'asset_missing' | 'invalid_emotion' | 'invalid_crossfade_ms' | 'mount_failed';
  detail: string;
  offendingEmotion?: Emotion | string;
}

export interface AvatarRenderer {
  /** Preload sprite images. Should be called before mount. */
  preload(images: readonly string[]): Promise<void>;

  /** Mount to DOM container. Throws if called twice. */
  mount(container: HTMLElement): void;

  /** Change emotion. Falls back to neutral for unknown values + emits onError. */
  setEmotion(emotion: Emotion, crossfadeMs: number): void;

  /** Toggle speaking pulse. speaking=false restores immediately. */
  setSpeaking(on: boolean): void;

  /** Subscribe to error events. Returns unsubscribe callback. */
  onError(cb: (e: AvatarRendererErrorEvent) => void): () => void;

  /** Unmount and clean up timers. All subsequent calls are no-ops. */
  dispose(): void;
}
