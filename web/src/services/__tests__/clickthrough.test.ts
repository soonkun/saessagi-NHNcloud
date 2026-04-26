import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { initClickthrough } from "../clickthrough";

// ── helpers ───────────────────────────────────────────────────

function makeElectronAPI() {
  return {
    isElectron: true as const,
    setIgnoreMouseEvents: vi.fn(),
    getDisplay: vi.fn().mockResolvedValue({ width: 1920, height: 1080, scaleFactor: 1 }),
    quit: vi.fn(),
    openDevTools: vi.fn(),
    onDisplayChanged: vi.fn().mockReturnValue(() => {}),
    onOpenChat: vi.fn().mockReturnValue(() => {}),
  };
}

// jsdom does not implement elementFromPoint — writable stub
let fromPointMock: ReturnType<typeof vi.fn>;

// Manual rAF control — avoids vitest fake-timer rAF edge cases
let pendingRaf: FrameRequestCallback | null = null;
let rafIdCounter = 0;

function flushRaf() {
  const cb = pendingRaf;
  pendingRaf = null;
  if (cb) cb(performance.now());
}

function fireMouseMove(x: number, y: number) {
  window.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: y, bubbles: true }));
}

// ── setup ─────────────────────────────────────────────────────

beforeEach(() => {
  pendingRaf = null;
  rafIdCounter = 0;

  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    pendingRaf = cb;
    return ++rafIdCounter;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {
    pendingRaf = null;
  });

  vi.stubGlobal("electronAPI", makeElectronAPI());

  // jsdom lacks elementFromPoint — install a configurable stub
  fromPointMock = vi.fn().mockReturnValue(document.body);
  Object.defineProperty(document, "elementFromPoint", {
    value: fromPointMock,
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  pendingRaf = null;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ── Normal cases ──────────────────────────────────────────────

describe("initClickthrough — normal cases", () => {
  it("N1: returns no-op handle when electronAPI is absent", () => {
    vi.stubGlobal("electronAPI", undefined);
    const h = initClickthrough();
    expect(() => h.setDragLock(true)).not.toThrow();
    expect(() => h.dispose()).not.toThrow();
  });

  it("N2: sets ignore=true on boot", () => {
    initClickthrough();
    expect(window.electronAPI!.setIgnoreMouseEvents).toHaveBeenCalledWith(true);
  });

  it("N3: stays in click-through mode over transparent area (body)", () => {
    fromPointMock.mockReturnValue(document.body);
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    fireMouseMove(100, 100);
    flushRaf();

    // lastIgnore was already true — no new call
    expect(spy).not.toHaveBeenCalled();
    h.dispose();
  });

  it("N4: switches to interactive when cursor lands on a real element", () => {
    const el = document.createElement("div");
    fromPointMock.mockReturnValue(el);
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    fireMouseMove(50, 50);
    flushRaf();

    expect(spy).toHaveBeenCalledWith(false);
    h.dispose();
  });

  it("N5: returns to click-through when cursor moves from element back to body", () => {
    const el = document.createElement("div");
    fromPointMock.mockReturnValue(el);
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    fireMouseMove(50, 50);
    flushRaf();
    expect(spy).toHaveBeenLastCalledWith(false);

    fromPointMock.mockReturnValue(document.body);
    fireMouseMove(200, 200);
    flushRaf();
    expect(spy).toHaveBeenLastCalledWith(true);
    h.dispose();
  });
});

// ── Edge cases ────────────────────────────────────────────────

describe("initClickthrough — edge cases", () => {
  it("E1: rAF throttle — multiple rapid mousemoves coalesce into one evaluate", () => {
    const positions: [number, number][] = [];
    fromPointMock.mockImplementation((x: number, y: number) => {
      positions.push([x, y]);
      return document.body;
    });
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    // Three moves before the rAF fires
    fireMouseMove(10, 10);
    fireMouseMove(20, 20);
    fireMouseMove(30, 30);
    flushRaf(); // one flush = one evaluate

    // Only ONE elementFromPoint call (uses last event: 30,30)
    expect(positions.length).toBe(1);
    expect(positions[0]).toEqual([30, 30]);
    h.dispose();
  });

  it("E2: dispose removes mousemove listener and sends ignore=true", () => {
    const el = document.createElement("div");
    fromPointMock.mockReturnValue(el);
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    h.dispose();
    expect(spy).toHaveBeenCalledWith(true);

    // After dispose, mousemove must not trigger IPC
    spy.mockClear();
    fireMouseMove(50, 50);
    flushRaf();
    expect(spy).not.toHaveBeenCalled();
  });

  it("E3: drag lock forces interactive even over transparent area", () => {
    fromPointMock.mockReturnValue(document.body);
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    h.setDragLock(true);
    fireMouseMove(400, 400);
    flushRaf();

    expect(spy).toHaveBeenCalledWith(false); // interactive during drag
    h.dispose();
  });

  it("E4: drag lock release re-evaluates cursor position instead of blindly ignoring (B5 fix)", () => {
    const el = document.createElement("div");
    fromPointMock.mockReturnValue(el);
    const h = initClickthrough();

    // Build up lastEvent by moving over a real element during drag
    h.setDragLock(true);
    fireMouseMove(50, 50); // updates lastEvent
    flushRaf();

    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    // Release drag while cursor is still over the element
    fromPointMock.mockReturnValue(el);
    h.setDragLock(false);

    // Must NOT blindly set ignore=true; must stay interactive
    expect(spy).toHaveBeenCalledWith(false);
    h.dispose();
  });

  it("E5: drag lock release with no prior mousemove falls back to ignore=true safely", () => {
    const h = initClickthrough();
    h.setDragLock(true);
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    // No mousemove → lastEvent is null → safe default
    h.setDragLock(false);
    expect(spy).toHaveBeenCalledWith(true);
    h.dispose();
  });
});

// ── Adversarial cases ─────────────────────────────────────────

describe("initClickthrough — adversarial cases", () => {
  it("A1: elementFromPoint returning null is treated as non-interactive (no IPC change)", () => {
    fromPointMock.mockReturnValue(null);
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;
    spy.mockClear();

    fireMouseMove(0, 0);
    flushRaf();

    // null → not interactive → nextIgnore=true == lastIgnore → no IPC call
    expect(spy).not.toHaveBeenCalled();
    h.dispose();
  });

  it("A2: redundant setDragLock(true) calls do not emit extra IPC", () => {
    fromPointMock.mockReturnValue(document.body);
    const h = initClickthrough();
    const spy = window.electronAPI!.setIgnoreMouseEvents as ReturnType<typeof vi.fn>;

    h.setDragLock(true);
    fireMouseMove(10, 10);
    flushRaf(); // sets interactive (drag lock active)
    spy.mockClear();

    h.setDragLock(true); // duplicate lock call
    // lastIgnore is already false (interactive) — no IPC emitted
    expect(spy).not.toHaveBeenCalled();
    h.dispose();
  });

  it("A3: dispose is idempotent — second call does not throw", () => {
    const h = initClickthrough();
    h.dispose();
    expect(() => h.dispose()).not.toThrow();
  });
});
