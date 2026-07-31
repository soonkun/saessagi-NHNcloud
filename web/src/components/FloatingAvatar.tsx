// web/src/components/FloatingAvatar.tsx
/**
 * CR-47 — 웹 페이지 위에 떠 있는 새싹이.
 *
 * 펫 모드(CR-38에서 제거)에서 잃어버린 것은 "지금 새싹이가 뭘 하고 있는지"를 한눈에 보는
 * 감각이었다. 그 역할만 되살린다:
 *   · 오른쪽 아래에 떠 있고
 *   · 드래그로 옮기고 모서리로 크기만 조절할 수 있으며
 *   · 클릭해도 창이 열리지 않는다 (펫 모드와의 결정적 차이)
 *   · 상태(emotion/speaking)에 따라 모습만 바뀐다
 *
 * 상태 파이프라인은 이미 살아 있어 그대로 쓴다:
 *   백엔드 AvatarState → ws `avatar-state` → store.emotion/speaking → 여기.
 * 위치·크기도 store의 position/charSize(localStorage 영속)를 재사용한다.
 */
import React from "react";

import { COMPACT_MAX_WIDTH, useIsCompact } from "../hooks/useMediaQuery";
import { useStore } from "../store";
import type { Emotion } from "../types";

/** 사라지는 연출 길이. 이 시간이 지나야 DOM에서 뺀다. */
const HIDE_ANIM_MS = 260;

const MIN_SIZE = 60;
const MAX_SIZE = 300;
const MARGIN = 8;

/** 모달(1000~9999)보다 아래 — 대화상자가 뜨면 캐릭터가 가리지 않아야 한다. */
const Z_INDEX = 900;

/** 상태별 한국어 설명. 마우스를 올렸을 때만 보인다(별도 UI를 늘리지 않는다). */
const EMOTION_LABEL: Record<string, string> = {
  neutral: "대기 중",
  happy: "기분 좋음",
  sad: "시무룩",
  surprised: "놀람",
  thinking: "생각하는 중",
  sleepy: "졸린 중",
  study: "문서 살펴보는 중",
  writing: "회의록 쓰는 중",
  note_writing: "업무 노트 쓰는 중",
  uploading: "문서 등록하는 중",
  worried: "걱정하는 중",
};

/** uploading만 영상(webm)이 있다 — 나머지는 정지 이미지. */
const VIDEO_EMOTIONS = new Set<string>(["uploading"]);

export function avatarSrcFor(
  emotion: string,
  ext: "png" | "webm" = "png",
): string {
  return `${import.meta.env.BASE_URL}avatars/${emotion}.${ext}`;
}

/** 화면 밖으로 나가지 않게 가둔다. 창을 줄이면 캐릭터가 사라져 되찾을 수 없기 때문이다. */
export function clampToViewport(
  pos: { x: number; y: number },
  size: number,
  vw: number,
  vh: number,
): { x: number; y: number } {
  const maxX = Math.max(MARGIN, vw - size - MARGIN);
  const maxY = Math.max(MARGIN, vh - size - MARGIN);
  return {
    x: Math.min(Math.max(pos.x, MARGIN), maxX),
    y: Math.min(Math.max(pos.y, MARGIN), maxY),
  };
}

export function clampSize(size: number): number {
  return Math.min(Math.max(Math.round(size), MIN_SIZE), MAX_SIZE);
}

type DragState =
  | { kind: "none" }
  | { kind: "move"; dx: number; dy: number }
  | { kind: "resize"; startX: number; startY: number; startSize: number };

export function FloatingAvatar(): React.ReactElement | null {
  const emotion = useStore((s) => s.emotion);
  const speaking = useStore((s) => s.speaking);
  const agentStatus = useStore((s) => s.agentStatus);
  const position = useStore((s) => s.position);
  const charSize = useStore((s) => s.charSize);
  const setPosition = useStore((s) => s.setPosition);
  const setPositionSilent = useStore((s) => s.setPositionSilent);
  const setCharSize = useStore((s) => s.setCharSize);

  // 좁은 화면에서는 캐릭터가 입력줄·버튼을 가린다. 그럴 땐 "쇽~" 하고 사라진다.
  // 곧바로 unmount하면 툭 끊기므로 연출이 끝날 때까지만 남겨 둔다.
  const isCompact = useIsCompact();
  const [mounted, setMounted] = React.useState(!isCompact);
  React.useEffect(() => {
    if (!isCompact) {
      setMounted(true);
      return;
    }
    const t = window.setTimeout(() => setMounted(false), HIDE_ANIM_MS);
    return () => window.clearTimeout(t);
  }, [isCompact]);

  const [drag, setDrag] = React.useState<DragState>({ kind: "none" });
  // 영상 재생에 실패했는가 — 실패하면 같은 감정의 정지 그림으로 대체한다.
  const [videoFailed, setVideoFailed] = React.useState(false);
  // 감정이 바뀌면 이전 그림을 잠깐 겹쳐 두고 새 그림을 페이드인한다(뚝 끊기지 않게).
  const [prevEmotion, setPrevEmotion] = React.useState<Emotion | null>(null);
  const lastEmotion = React.useRef<Emotion>(emotion);

  React.useEffect(() => {
    if (lastEmotion.current === emotion) return;
    setPrevEmotion(lastEmotion.current);
    lastEmotion.current = emotion;
    setVideoFailed(false); // 감정이 바뀌면 다시 영상부터 시도한다
    const t = window.setTimeout(() => setPrevEmotion(null), 250);
    return () => window.clearTimeout(t);
  }, [emotion]);

  // 창 크기가 바뀌면 화면 안으로 되돌린다. 위치 자체가 바뀐 게 아니므로 저장은 하지 않는다
  // (저장하면 창을 잠깐 줄였다 키운 것만으로 사용자가 정한 자리를 잃는다).
  React.useEffect(() => {
    const onResize = (): void => {
      // 숨어 있는 동안에는 가둘 이유가 없다. 여기서 끌어들이면 화면을 넓혔을 때
      // 사용자가 정해둔 자리가 아니라 좁은 화면 기준으로 밀린 자리에 남는다.
      //
      // isCompact(state)가 아니라 폭을 직접 본다 — resize 이벤트가 matchMedia로 인한
      // 리렌더보다 먼저 도착해, state로 판단하면 이 가드가 한 박자 늦어 그대로 통과한다.
      if (window.innerWidth < COMPACT_MAX_WIDTH) return;
      const cur = useStore.getState();
      const next = clampToViewport(
        cur.position,
        cur.charSize,
        window.innerWidth,
        window.innerHeight,
      );
      if (next.x !== cur.position.x || next.y !== cur.position.y)
        cur.setPositionSilent(next);
    };
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const onPointerMove = React.useCallback(
    (e: React.PointerEvent) => {
      if (drag.kind === "move") {
        setPositionSilent(
          clampToViewport(
            { x: e.clientX - drag.dx, y: e.clientY - drag.dy },
            charSize,
            window.innerWidth,
            window.innerHeight,
          ),
        );
      } else if (drag.kind === "resize") {
        // 오른쪽/아래로 끌면 커진다. 좌상단을 고정점으로 두어 위치가 튀지 않는다.
        const delta = Math.max(
          e.clientX - drag.startX,
          e.clientY - drag.startY,
        );
        setCharSize(clampSize(drag.startSize + delta));
      }
    },
    [drag, charSize, setPositionSilent, setCharSize],
  );

  const endDrag = React.useCallback(
    (e: React.PointerEvent) => {
      if (drag.kind === "none") return;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* 이미 해제된 경우 무시 */
      }
      // 조작이 끝난 시점에만 저장한다(드래그 중 매 프레임 localStorage 쓰기 방지).
      setPosition(useStore.getState().position);
      setDrag({ kind: "none" });
    },
    [drag, setPosition],
  );

  // 훅은 모두 위에서 호출한 뒤에 빠져나간다 (조건부 훅 금지).
  if (!mounted) return null;

  const label = agentStatus || EMOTION_LABEL[emotion] || "대기 중";
  const isVideo = VIDEO_EMOTIONS.has(emotion);
  const dragging = drag.kind !== "none";

  return (
    <div
      data-testid="floating-avatar"
      role="status"
      aria-live="polite"
      aria-label={`새싹이: ${label}`}
      title={`새싹이 — ${label}`}
      onPointerDown={(e) => {
        // 왼쪽 버튼(또는 터치)만. 캐릭터 본체를 잡으면 이동.
        if (e.button !== 0) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        setDrag({
          kind: "move",
          dx: e.clientX - position.x,
          dy: e.clientY - position.y,
        });
      }}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      style={{
        position: "fixed",
        left: position.x,
        top: position.y,
        width: charSize,
        height: charSize,
        zIndex: Z_INDEX,
        cursor: dragging ? "grabbing" : "grab",
        userSelect: "none",
        touchAction: "none",
        // 등장·퇴장은 바깥 상자가, 떠 있는 움직임은 안쪽이 맡는다.
        // 한 요소에 두 animation을 얹으면 서로 transform을 덮어써 튄다.
        animation: isCompact
          ? `char-shrink-out ${HIDE_ANIM_MS}ms ease-in forwards`
          : "char-pop-in 260ms ease-out",
        // 사라지는 중에는 클릭·드래그를 받지 않는다.
        pointerEvents: isCompact ? "none" : "auto",
        // 클릭으로 무언가 열리지 않는다 — 이동·크기조절 외에는 동작이 없다(CR-47).
      }}
    >
      <div
        // 떠 있는 느낌은 펫 모드의 기존 애니메이션을 그대로 쓴다(중복 정의하지 않는다).
        // 조작 중에는 멈춰야 커서를 정확히 따라온다.
        className={
          dragging ? undefined : speaking ? "char-speaking" : "char-idle"
        }
        style={{ position: "absolute", inset: 0 }}
      >
        {/* 이전 감정 — 아래 깔려 페이드아웃되는 층 */}
        {prevEmotion && !isVideo && (
          <img
            src={avatarSrcFor(prevEmotion)}
            alt=""
            aria-hidden="true"
            style={{
              ...layerStyle,
              animation: "char-emotion-out 250ms ease forwards",
            }}
          />
        )}

        {isVideo && !videoFailed ? (
          <video
            key={emotion}
            src={avatarSrcFor(emotion, "webm")}
            poster={avatarSrcFor(emotion)}
            autoPlay
            loop
            muted
            playsInline
            // 영상을 못 읽으면 같은 감정의 정지 그림으로 물러선다. 이 안전장치가 없으면
            // 캐릭터가 통째로 사라진 것처럼 보인다 — 실제로 서버가 webm을 403으로
            // 막고 png도 없어서 그렇게 됐다 (E-77).
            onError={() => setVideoFailed(true)}
            style={layerStyle}
          />
        ) : (
          <img
            key={emotion}
            src={avatarSrcFor(emotion)}
            alt=""
            draggable={false}
            onError={(e) => {
              // 파일이 없는 감정이 오더라도 캐릭터가 사라지면 안 된다.
              e.currentTarget.src = avatarSrcFor("neutral");
            }}
            style={{
              ...layerStyle,
              animation: prevEmotion ? "char-emotion-in 250ms ease" : undefined,
            }}
          />
        )}
      </div>

      {/* 크기 조절 손잡이 — 오른쪽 아래 모서리.
          같이 둥실거리면 잡기 어려우므로 흔들리는 층 밖에 둔다. */}
      <div
        data-testid="floating-avatar-resize"
        aria-hidden="true"
        title="크기 조절"
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.stopPropagation(); // 본체의 이동 드래그가 같이 시작되지 않게
          e.currentTarget.setPointerCapture(e.pointerId);
          setDrag({
            kind: "resize",
            startX: e.clientX,
            startY: e.clientY,
            startSize: charSize,
          });
        }}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        style={{
          position: "absolute",
          right: -2,
          bottom: -2,
          width: 16,
          height: 16,
          borderRadius: 8,
          cursor: "nwse-resize",
          background: "var(--color-bg, #fff)",
          border: "1px solid var(--color-border, #ccc)",
          // 평소엔 눈에 띄지 않다가 캐릭터에 마우스를 올리면 드러난다.
          opacity: dragging ? 1 : 0.35,
          transition: "opacity 150ms ease",
          touchAction: "none",
        }}
      />
    </div>
  );
}

const layerStyle: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  width: "100%",
  height: "100%",
  objectFit: "contain",
  pointerEvents: "none",
  // 배경이 어두울 때도 캐릭터가 떠 보이도록 옅은 그림자
  filter: "drop-shadow(0 4px 10px rgba(0,0,0,0.25))",
};
