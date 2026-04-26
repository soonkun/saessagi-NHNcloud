export interface ClickthroughOptions {
  throttleMs?: number; // default 16 (rAF)
  rootSelector?: string; // default '#root'
}

export interface ClickthroughHandle {
  setDragLock(locked: boolean): void;
  /** 마우스가 인터랙티브 요소 위에 진입했을 때 즉시 호출 — rAF 대기 없이 click-through 해제 */
  setInteractive(on: boolean): void;
  dispose(): void;
}

export function initClickthrough(
  _opts?: ClickthroughOptions
): ClickthroughHandle {
  // Browser mode: no-op
  if (!window.electronAPI) {
    return {
      setDragLock: () => {},
      setInteractive: () => {},
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
      // Safety: never enable click-through while cursor is physically inside the
      // panel or character widget. elementFromPoint can transiently return body
      // during React re-renders / tab switches / file-picker dialogs, which would
      // wrongly activate click-through while the user is still interacting.
      if (nextIgnore) {
        const panel = document.getElementById("chat-panel");
        const char = document.getElementById("char-widget");
        for (const elem of [panel, char]) {
          if (!elem) continue;
          const r = elem.getBoundingClientRect();
          if (
            r.width > 0 &&
            e.clientX >= r.left && e.clientX <= r.right &&
            e.clientY >= r.top  && e.clientY <= r.bottom
          ) return; // cursor is inside an interactive widget — don't enable click-through
        }
      }
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
    /**
     * CharacterWidget / ChatPanel의 onMouseEnter·onMouseLeave에서 직접 호출.
     * rAF + IPC 왕복 지연 없이 즉시 ignore 상태를 전환해 "첫 클릭 통과" 버그를 방지.
     * (다른 앱에서 마우스를 쓰다 바로 클릭하면 mousemove 이벤트가 아직 도달 전일 수 있음)
     */
    setInteractive(on: boolean): void {
      const nextIgnore = !on;
      if (nextIgnore !== lastIgnore) {
        lastIgnore = nextIgnore;
        window.electronAPI?.setIgnoreMouseEvents(nextIgnore);
      }
    },
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
