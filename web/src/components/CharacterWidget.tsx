import { useCallback, useEffect, useRef } from "react";
import { useStore } from "../store";
import type { Position } from "../types";

// ────────────────────────────────────────────────────────────
// 드래그 훅
// ────────────────────────────────────────────────────────────

const DRAG_THRESHOLD_PX = 5;
const CHAR_SIZE = 120;

interface UseDraggableResult {
  ref: React.RefObject<HTMLDivElement>;
  onMouseDown: (e: React.MouseEvent) => void;
}

function useDraggable(
  position: Position,
  onMove: (pos: Position) => void,
  onClickEnd: () => void
): UseDraggableResult {
  const ref = useRef<HTMLDivElement>(null);
  const startMouse = useRef<{ x: number; y: number } | null>(null);
  const startPos = useRef<Position>({ x: 0, y: 0 });
  const isDragging = useRef(false);

  const clamp = useCallback((pos: Position): Position => {
    const maxX = window.innerWidth - CHAR_SIZE;
    const maxY = window.innerHeight - CHAR_SIZE;
    return {
      x: Math.max(0, Math.min(pos.x, maxX)),
      y: Math.max(0, Math.min(pos.y, maxY)),
    };
  }, []);

  const onMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!startMouse.current) return;
      const dx = e.clientX - startMouse.current.x;
      const dy = e.clientY - startMouse.current.y;

      if (!isDragging.current) {
        if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
        isDragging.current = true;
      }

      const newPos = clamp({
        x: startPos.current.x + dx,
        y: startPos.current.y + dy,
      });
      onMove(newPos);

      if (ref.current) {
        ref.current.style.cursor = "grabbing";
      }
    },
    [clamp, onMove]
  );

  const onMouseUp = useCallback(() => {
    if (!isDragging.current) {
      onClickEnd();
    }
    startMouse.current = null;
    isDragging.current = false;
    if (ref.current) {
      ref.current.style.cursor = "grab";
    }
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
  }, [onClickEnd, onMouseMove]);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      startMouse.current = { x: e.clientX, y: e.clientY };
      startPos.current = { ...position };
      isDragging.current = false;
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [position, onMouseMove, onMouseUp]
  );

  // cleanup on unmount
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

export function CharacterWidget(): React.ReactElement {
  const emotion = useStore((s) => s.emotion);
  const speaking = useStore((s) => s.speaking);
  const position = useStore((s) => s.position);
  const setPosition = useStore((s) => s.setPosition);
  const toggleChat = useStore((s) => s.toggleChat);

  const { ref, onMouseDown } = useDraggable(position, setPosition, toggleChat);

  const imageSrc = `/avatars/${emotion}.png`;

  return (
    <div
      ref={ref}
      onMouseDown={onMouseDown}
      style={{
        position: "fixed",
        left: position.x,
        top: position.y,
        width: CHAR_SIZE,
        height: CHAR_SIZE,
        cursor: "grab",
        userSelect: "none",
        zIndex: 1000,
      }}
      title="새싹이 — 클릭해서 채팅"
    >
      <img
        src={imageSrc}
        alt={`새싹이 (${emotion})`}
        draggable={false}
        className={speaking ? "char-speaking" : ""}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "contain",
          borderRadius: 8,
          pointerEvents: "none",
        }}
        onError={(e) => {
          // fallback to neutral if emotion image missing
          const img = e.currentTarget;
          if (!img.src.endsWith("neutral.png")) {
            img.src = "/avatars/neutral.png";
          }
        }}
      />
    </div>
  );
}
