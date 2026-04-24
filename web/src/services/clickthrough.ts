export interface ClickthroughOptions {
  throttleMs?: number; // default 16 (rAF)
  rootSelector?: string; // default '#root'
}

export interface ClickthroughHandle {
  setDragLock(locked: boolean): void;
  dispose(): void;
}

export function initClickthrough(
  _opts?: ClickthroughOptions
): ClickthroughHandle {
  // Browser mode: no-op
  if (!window.electronAPI) {
    return {
      setDragLock: () => {},
      dispose: () => {},
    };
  }

  let lastIgnore = true;
  let dragLocked = false;
  let rafId: number | null = null;
  let pendingEvent: MouseEvent | null = null;
  let lastEvent: MouseEvent | null = null;

  // Boot with click-through ON (matches main.ts initial state)
  window.electronAPI.setIgnoreMouseEvents(true);

  function evaluate(e: MouseEvent): void {
    if (dragLocked) {
      // During drag: always interactive
      if (lastIgnore !== false) {
        lastIgnore = false;
        window.electronAPI!.setIgnoreMouseEvents(false);
      }
      return;
    }

    const el = document.elementFromPoint(e.clientX, e.clientY);
    const interactive =
      el !== null &&
      el !== document.body &&
      el !== document.documentElement;
    const nextIgnore = !interactive;

    if (nextIgnore !== lastIgnore) {
      lastIgnore = nextIgnore;
      window.electronAPI!.setIgnoreMouseEvents(nextIgnore);
    }
  }

  function onMouseMove(e: MouseEvent): void {
    lastEvent = e;
    pendingEvent = e;
    if (rafId !== null) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      if (pendingEvent) evaluate(pendingEvent);
      pendingEvent = null;
    });
  }

  window.addEventListener("mousemove", onMouseMove, { passive: true });

  return {
    setDragLock(locked: boolean): void {
      dragLocked = locked;
      if (!locked) {
        // Re-evaluate immediately using last known cursor position
        // instead of blindly enabling click-through (B5 fix)
        if (lastEvent) {
          const el = document.elementFromPoint(lastEvent.clientX, lastEvent.clientY);
          const interactive =
            el !== null &&
            el !== document.body &&
            el !== document.documentElement;
          lastIgnore = !interactive;
          window.electronAPI?.setIgnoreMouseEvents(lastIgnore);
        } else {
          lastIgnore = true;
          window.electronAPI?.setIgnoreMouseEvents(true);
        }
      }
    },
    dispose(): void {
      window.removeEventListener("mousemove", onMouseMove);
      if (rafId !== null) cancelAnimationFrame(rafId);
      window.electronAPI?.setIgnoreMouseEvents(true);
    },
  };
}
