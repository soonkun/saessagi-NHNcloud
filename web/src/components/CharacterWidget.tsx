import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import type { ClickthroughHandle } from "../services/clickthrough";
import type { Position } from "../types";

const QUIT_BTN_SIZE = 16;

const DRAG_THRESHOLD_PX = 5;
const MIN_CHAR_SIZE = 60;
const MAX_CHAR_SIZE = 300;

// ────────────────────────────────────────────────────────────
// Draggable hook — stable callbacks via refs (drag listeners
// survive React re-renders caused by position/screenSize updates)
// ────────────────────────────────────────────────────────────

interface UseDraggableResult {
  ref: React.RefObject<HTMLDivElement>;
  onMouseDown: (e: React.MouseEvent) => void;
}

function useDraggable(
  position: Position,
  screenSize: { width: number; height: number },
  charSize: number,
  onMove: (pos: Position) => void,
  onClickEnd: () => void,
  onDragStart: () => void,
  onDragEnd: () => void
): UseDraggableResult {
  const ref = useRef<HTMLDivElement>(null);
  const startMouse = useRef<{ x: number; y: number } | null>(null);
  const startPos = useRef<Position>({ x: 0, y: 0 });
  const isDragging = useRef(false);

  // Keep latest props in refs so callbacks below need zero deps (= stable)
  const positionRef = useRef(position);
  const screenSizeRef = useRef(screenSize);
  const charSizeRef = useRef(charSize);
  const onMoveRef = useRef(onMove);
  const onClickEndRef = useRef(onClickEnd);
  const onDragStartRef = useRef(onDragStart);
  const onDragEndRef = useRef(onDragEnd);

  positionRef.current = position;
  screenSizeRef.current = screenSize;
  charSizeRef.current = charSize;
  onMoveRef.current = onMove;
  onClickEndRef.current = onClickEnd;
  onDragStartRef.current = onDragStart;
  onDragEndRef.current = onDragEnd;

  // Stable clamp — reads screenSize and charSize from refs
  const clamp = useCallback((pos: Position): Position => {
    const { width, height } = screenSizeRef.current;
    const sz = charSizeRef.current;
    return {
      x: Math.max(0, Math.min(pos.x, width - sz)),
      y: Math.max(0, Math.min(pos.y, height - sz)),
    };
  }, []); // intentionally empty — reads from refs

  // Stable mousemove handler
  const onMouseMove = useCallback((e: MouseEvent) => {
    if (!startMouse.current) return;
    const dx = e.clientX - startMouse.current.x;
    const dy = e.clientY - startMouse.current.y;

    if (!isDragging.current) {
      if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
      isDragging.current = true;
      onDragStartRef.current();
    }

    const newPos = clamp({
      x: startPos.current.x + dx,
      y: startPos.current.y + dy,
    });
    onMoveRef.current(newPos);

    if (ref.current) ref.current.style.cursor = "grabbing";
  }, [clamp]); // clamp is stable, so onMouseMove is stable

  // Stable mouseup handler — captured in closure once at creation
  const onMouseUp = useCallback(() => {
    if (!isDragging.current) {
      onClickEndRef.current();
    }
    onDragEndRef.current();
    startMouse.current = null;
    isDragging.current = false;
    if (ref.current) ref.current.style.cursor = "grab";
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
  }, [onMouseMove]); // onMouseMove is stable, so onMouseUp is stable

  // Mousedown re-reads position from ref each call — no dep needed
  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      startMouse.current = { x: e.clientX, y: e.clientY };
      startPos.current = { ...positionRef.current }; // read current pos from ref
      isDragging.current = false;
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [onMouseMove, onMouseUp]
  );

  useEffect(() => {
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [onMouseMove, onMouseUp]);

  return { ref, onMouseDown };
}

// ────────────────────────────────────────────────────────────
// CharacterWidget
// ────────────────────────────────────────────────────────────

interface CharacterWidgetProps {
  clickthroughHandle: React.RefObject<ClickthroughHandle | null>;
}

export function CharacterWidget({
  clickthroughHandle,
}: CharacterWidgetProps): React.ReactElement {
  const emotion = useStore((s) => s.emotion);
  const speaking = useStore((s) => s.speaking);
  const position = useStore((s) => s.position);
  const setPosition = useStore((s) => s.setPosition);
  const charSize = useStore((s) => s.charSize);
  const setCharSize = useStore((s) => s.setCharSize);
  const toggleChat = useStore((s) => s.toggleChat);
  const setChatOpen = useStore((s) => s.setChatOpen);

  const [isHovered, setIsHovered] = useState(false);

  // Electron display bounds for correct clamping (B4 fix)
  const [screenSize, setScreenSize] = useState<{ width: number; height: number }>(
    () => ({ width: window.screen.width, height: window.screen.height })
  );

  useEffect(() => {
    if (!window.electronAPI) return;

    void window.electronAPI.getDisplay().then((display) => {
      setScreenSize({ width: display.width, height: display.height });

      // If position looks uninitialized (stale {0,0} from a bad previous run),
      // reset to bottom-right corner
      const saved = JSON.parse(
        localStorage.getItem("saessagi_char_pos") ?? "null"
      ) as Position | null;
      if (!saved || (saved.x === 0 && saved.y === 0)) {
        setPosition({
          x: display.width - 32 - charSize,
          y: display.height - 32 - charSize,
        });
      }
    });

    const unsub = window.electronAPI.onDisplayChanged((size) => {
      setScreenSize(size);
    });

    return unsub;
  }, [setPosition]);

  function handleMove(pos: Position): void {
    setPosition(pos);
  }

  function handleDragStart(): void {
    setChatOpen(false);
    clickthroughHandle.current?.setDragLock(true);
  }

  function handleDragEnd(): void {
    clickthroughHandle.current?.setDragLock(false);
  }

  const { ref, onMouseDown } = useDraggable(
    position,
    screenSize,
    charSize,
    handleMove,
    toggleChat,
    handleDragStart,
    handleDragEnd
  );

  // Resize handle — reads size from ref, stable callback
  const charSizeRef = useRef(charSize);
  charSizeRef.current = charSize;
  const setCharSizeRef = useRef(setCharSize);
  setCharSizeRef.current = setCharSize;

  const onResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startSize = charSizeRef.current;

    function onMove(ev: MouseEvent): void {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const delta = (dx + dy) / 2;
      const newSize = Math.max(MIN_CHAR_SIZE, Math.min(MAX_CHAR_SIZE, Math.round(startSize + delta)));
      setCharSizeRef.current(newSize);
    }

    function onUp(): void {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  const imageSrc = `${import.meta.env.BASE_URL}avatars/${emotion}.png`;

  function handleQuit(e: React.MouseEvent): void {
    e.stopPropagation();
    e.preventDefault();
    window.electronAPI?.quit();
  }

  return (
    <div
      id="char-widget"
      ref={ref}
      onMouseDown={onMouseDown}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        position: "fixed",
        left: position.x,
        top: position.y,
        width: charSize,
        height: charSize,
        cursor: "grab",
        userSelect: "none",
        zIndex: 1000,
        pointerEvents: "auto",
      }}
      title="새싹이 — 클릭해서 채팅"
    >
      <img
        src={imageSrc}
        alt={`새싹이 (${emotion})`}
        draggable={false}
        className={speaking ? "char-speaking" : "char-idle"}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "contain",
          borderRadius: 8,
          pointerEvents: "none",
        }}
        onError={(e) => {
          const img = e.currentTarget;
          if (
            !img.src.endsWith("neutral.png") &&
            !img.src.endsWith("neutral.png/")
          ) {
            img.src = `${import.meta.env.BASE_URL}avatars/neutral.png`;
          }
        }}
      />

      {/* Resize handle — bottom-right corner (hover 시 표시) */}
      {isHovered && (
        <div
          onMouseDown={onResizeMouseDown}
          title="크기 조절"
          style={{
            position: "absolute",
            right: -5,
            bottom: -5,
            width: 14,
            height: 14,
            borderRadius: "50%",
            background: "rgba(255,255,255,0.85)",
            border: "1.5px solid rgba(0,0,0,0.25)",
            cursor: "nwse-resize",
            zIndex: 1001,
            boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
          }}
        />
      )}

      {/* Quit button — top-right corner (hover 시 표시) */}
      {isHovered && (
        <div
          onMouseDown={handleQuit}
          title="새싹이 종료"
          style={{
            position: "absolute",
            right: -5,
            top: -5,
            width: QUIT_BTN_SIZE,
            height: QUIT_BTN_SIZE,
            borderRadius: "50%",
            background: "rgba(220,60,50,0.9)",
            border: "1.5px solid rgba(0,0,0,0.2)",
            cursor: "pointer",
            zIndex: 1001,
            boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontSize: 11,
            fontWeight: 700,
            lineHeight: 1,
            userSelect: "none",
          }}
        >
          ✕
        </div>
      )}
    </div>
  );
}
