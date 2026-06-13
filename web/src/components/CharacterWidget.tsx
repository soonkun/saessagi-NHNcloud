import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import type { ClickthroughHandle } from "../services/clickthrough";
import type { Position } from "../types";

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
  const isMeetingGenerating = useStore((s) => s.isMeetingGenerating);
  const position = useStore((s) => s.position);
  const setPosition = useStore((s) => s.setPosition);
  const charSize = useStore((s) => s.charSize);
  const setCharSize = useStore((s) => s.setCharSize);
  const toggleChat = useStore((s) => s.toggleChat);
  const setChatOpen = useStore((s) => s.setChatOpen);
  const windowMode = useStore((s) => s.windowMode);

  const [isHovered, setIsHovered] = useState(false);
  // imgKey가 바뀌면 <img>가 remount되어 이미지를 재요청함
  const [imgKey, setImgKey] = useState(0);
  // uploading 영상 로드 실패 시 정지 이미지(uploading.png)로 폴백
  const [videoFailed, setVideoFailed] = useState(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  const displayEmotion = isMeetingGenerating ? "writing" : emotion;
  const imageSrc = `${import.meta.env.BASE_URL}avatars/${displayEmotion}.png`;
  // RAG 등록(uploading) 중에는 "책장 포털에 책 넣는" 동영상을 정사각 아이콘 자리에 재생.
  // 동영상은 16:9 원본 비율 그대로(투명 처리 없음) — 세로는 charSize에 맞추고 가로는 중앙정렬로 오버플로.
  const showUploadVideo = displayEmotion === "uploading" && !videoFailed;
  const videoSrc = `${import.meta.env.BASE_URL}avatars/uploading.webm`;

  // uploading 상태를 벗어나면 다음 등록을 위해 폴백 플래그 초기화
  useEffect(() => {
    if (displayEmotion !== "uploading" && videoFailed) setVideoFailed(false);
  }, [displayEmotion, videoFailed]);

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
      onMouseEnter={() => {
        setIsHovered(true);
        // rAF 대기 없이 즉시 click-through 해제 — 다른 앱에서 바로 클릭해도 통과 방지
        // onMouseLeave에서는 setIgnoreMouseEvents(true)를 직접 호출하지 않음:
        // 패널·캐릭터 경계 근처에서 마우스가 조금만 벗어나도 입력이 차단되는 부작용이 있음.
        // click-through 복원은 mousemove+rAF 시스템(clickthrough.ts)이 담당.
        clickthroughHandle.current?.setInteractive(true);
      }}
      onMouseLeave={() => {
        setIsHovered(false);
      }}
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
      {showUploadVideo ? (
        <video
          key="uploading-video"
          src={videoSrc}
          autoPlay
          loop
          muted
          playsInline
          draggable={false}
          // 세로를 charSize에 맞추고 가로는 16:9 원본 비율로 둔 뒤, 박스 오른쪽 변에 정렬.
          // 박스 오른쪽 변 = charPos.x + charSize = 채팅 패널 오른쪽 단(ChatPanel.calcPanelStyle)이므로
          // right:0으로 붙이면 영상 오른쪽 단과 패널 오른쪽 단이 정확히 맞는다. 캐릭터는 프레임 오른쪽에
          // 있어 자연스럽게 오른쪽에 서고, 포털은 왼쪽으로 오버플로(부모에 overflow:hidden 없음).
          style={{
            position: "absolute",
            top: 0,
            right: 0,
            height: "100%",
            width: "auto",
            maxWidth: "none",
            objectFit: "contain",
            borderRadius: 8,
            pointerEvents: "none",
          }}
          onError={() => setVideoFailed(true)}
        />
      ) : (
        <img
          key={imgKey}
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
              // 감정 이미지 실패 → neutral로 폴백
              img.src = `${import.meta.env.BASE_URL}avatars/neutral.png`;
            } else {
              // neutral.png 자체 실패 → 백엔드가 아직 안 올라온 경우, 3초 후 재시도
              if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
              retryTimerRef.current = setTimeout(() => {
                setImgKey((k) => k + 1);
              }, 3000);
            }
          }}
        />
      )}

      {/* Mode toggle — bottom-left (안쪽 5px, hover 시 표시) */}
      {isHovered && (
        <div
          onMouseDown={(e) => {
            e.stopPropagation();
            e.preventDefault();
            if (windowMode === "pet") {
              void window.petMode?.disable();
            } else {
              void window.petMode?.enable();
            }
          }}
          title={windowMode === "pet" ? "창 모드로 전환" : "펫 모드로 전환"}
          style={{
            position: "absolute",
            left: 5,
            bottom: 5,
            width: 26,
            height: 26,
            borderRadius: "50%",
            background: "rgba(60,130,210,0.9)",
            border: "1.5px solid rgba(0,0,0,0.2)",
            cursor: "pointer",
            zIndex: 1001,
            boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontSize: "var(--fs-14)",
            fontWeight: 700,
            lineHeight: 1,
            userSelect: "none",
          }}
        >
          {windowMode === "pet" ? "⊞" : "◉"}
        </div>
      )}

      {/* Resize handle — bottom-right (안쪽 5px, hover 시 표시) */}
      {isHovered && (
        <div
          onMouseDown={onResizeMouseDown}
          title="크기 조절"
          style={{
            position: "absolute",
            right: 5,
            bottom: 5,
            width: 26,
            height: 26,
            borderRadius: "50%",
            background: "rgba(255,255,255,0.85)",
            border: "1.5px solid rgba(0,0,0,0.25)",
            cursor: "nwse-resize",
            zIndex: 1001,
            boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
          }}
        />
      )}

      {/* Quit button — top-right (안쪽 5px, hover 시 표시) */}
      {isHovered && (
        <div
          onMouseDown={handleQuit}
          title="새싹이 종료"
          style={{
            position: "absolute",
            right: 5,
            top: 5,
            width: 26,
            height: 26,
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
            fontSize: "var(--fs-14)",
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
