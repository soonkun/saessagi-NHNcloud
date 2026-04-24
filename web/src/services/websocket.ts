import type { WsIncomingMessage, WsOutgoingMessage, Emotion } from "../types";
import { useStore } from "../store";

// ────────────────────────────────────────────────────────────
// 감정 태그 파싱 유틸
// ────────────────────────────────────────────────────────────

// LLM이 출력하는 표현 이름 → 우리 PNG 파일명 매핑
const EMOTION_MAP: Record<string, Emotion> = {
  joy: "happy",
  happy: "happy",
  excited: "happy",
  smile: "happy",
  sad: "sad",
  cry: "sad",
  surprised: "surprised",
  shock: "surprised",
  thinking: "thinking",
  ponder: "thinking",
  sleepy: "sleepy",
  tired: "sleepy",
  study: "study",
  worried: "worried_v2",
  worried_v2: "worried_v2",
  neutral: "neutral",
};

function parseEmotion(raw: string): Emotion | null {
  const key = raw.toLowerCase().trim();
  return EMOTION_MAP[key] ?? null;
}

// display_text에서 [emotion] 태그를 제거하고 emotion을 추출
function stripEmotionTags(text: string): { text: string; emotion: Emotion | null } {
  let detected: Emotion | null = null;
  const cleaned = text.replace(/\[([^\]]+)\]/g, (_, tag: string) => {
    const e = parseEmotion(tag);
    if (e && !detected) detected = e;
    return "";
  }).trim();
  return { text: cleaned, emotion: detected };
}

// ────────────────────────────────────────────────────────────
// WebSocket 싱글턴 서비스
// ────────────────────────────────────────────────────────────

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let thinkingTimer: ReturnType<typeof setTimeout> | null = null;
let currentUrl = "";
let reconnectAttempts = 0;

// ────────────────────────────────────────────────────────────
// TTS 오디오 큐
// ────────────────────────────────────────────────────────────

let audioCtx: AudioContext | null = null;
let audioQueue: AudioBuffer[] = [];
let currentSource: AudioBufferSourceNode | null = null;
let synthDone = false;

function getAudioCtx(): AudioContext {
  if (!audioCtx || audioCtx.state === "closed") {
    audioCtx = new AudioContext();
  }
  return audioCtx;
}

function base64ToBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

async function queueAudio(b64: string): Promise<void> {
  try {
    const ctx = getAudioCtx();
    if (ctx.state === "suspended") await ctx.resume();
    const decoded = await ctx.decodeAudioData(base64ToBuffer(b64));
    audioQueue.push(decoded);
    playNextChunk();
  } catch (e) {
    console.warn("[TTS] decode error:", e);
  }
}

function playNextChunk(): void {
  if (currentSource !== null || audioQueue.length === 0) return;
  const buf = audioQueue.shift()!;
  const ctx = getAudioCtx();
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.playbackRate.value = useStore.getState().ttsRate;
  src.connect(ctx.destination);
  src.onended = () => {
    currentSource = null;
    if (audioQueue.length > 0) {
      playNextChunk();
    } else if (synthDone) {
      synthDone = false;
      send({ type: "frontend-playback-complete" });
      useStore.getState().setAiStatus("idle");
    }
  };
  currentSource = src;
  src.start();
}

function stopAudio(): void {
  if (currentSource) {
    currentSource.onended = null;
    try { currentSource.stop(); } catch { /* ignore */ }
    currentSource = null;
  }
  audioQueue = [];
  synthDone = false;
}

const MAX_RECONNECT_ATTEMPTS = 8;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30_000;
const STUCK_THINKING_MS = 60_000;

function dispatch(msg: WsIncomingMessage): void {
  const store = useStore.getState();

  switch (msg.type) {
    case "control":
      if (msg.text === "start-mic") store.setMicOn(true);
      else if (msg.text === "stop-mic") store.setMicOn(false);
      else if (msg.text === "conversation-chain-start") {
        stopAudio();
        store.setAiStatus("thinking");
        clearThinkingTimer();
        thinkingTimer = setTimeout(() => {
          store.setAiStatus("idle");
          thinkingTimer = null;
        }, STUCK_THINKING_MS);
      } else if (msg.text === "conversation-chain-end") {
        clearThinkingTimer();
        store.setAiStatus("idle");
      }
      break;

    case "audio": {
      // expression 필드로 감정 전환 (upstream avatar-state 미수신 시 폴백)
      if (msg.expression) {
        const e = parseEmotion(msg.expression);
        if (e) store.setEmotion(e);
      }
      if (msg.display_text?.text) {
        const { text: clean, emotion } = stripEmotionTags(msg.display_text.text);
        if (emotion) store.setEmotion(emotion);
        store.addMessage({ role: "ai", text: clean || msg.display_text.text });
      }
      if (msg.audio) void queueAudio(msg.audio);
      break;
    }

    case "message":
      store.addMessage({ role: msg.role, text: msg.message });
      if (msg.role === "ai") {
        store.setAiStatus("idle");
      }
      break;

    case "avatar-state": {
      // upstream expression 이름이 우리 파일명과 다를 수 있으므로 매핑 경유
      const mappedEmotion = parseEmotion(msg.emotion) ?? msg.emotion;
      store.setEmotion(mappedEmotion as Emotion);
      store.setSpeaking(msg.speaking);
      store.setAiStatus(msg.speaking ? "speaking" : "idle");
      break;
    }

    case "backend-synth-complete":
      if (currentSource !== null || audioQueue.length > 0) {
        synthDone = true;
      } else {
        send({ type: "frontend-playback-complete" });
        store.setAiStatus("idle");
      }
      break;

    case "tool-call-status":
      // tool 상태는 현재 UI에 별도 표시 없음 — 무시
      break;

    case "new-history-created":
      // 백엔드에서 새 대화 이력이 생성됨 — 프론트 메시지도 초기화
      store.clearMessages();
      store.setEmotion("neutral");
      store.setAiStatus("idle");
      break;

    default:
      break;
  }
}

export function connect(url: string): void {
  if (ws && ws.readyState !== WebSocket.CLOSED) {
    if (currentUrl === url) return;
    ws.close();
  }

  currentUrl = url;

  try {
    ws = new WebSocket(url);
  } catch {
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.info("[WS] connected:", url);
    reconnectAttempts = 0;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  ws.onmessage = (event: MessageEvent) => {
    try {
      const msg = JSON.parse(event.data as string) as WsIncomingMessage;
      dispatch(msg);
    } catch {
      // 파싱 실패 무시
    }
  };

  ws.onerror = () => {
    console.warn("[WS] error — will reconnect");
  };

  ws.onclose = () => {
    console.info("[WS] closed — scheduling reconnect");
    clearThinkingTimer();
    useStore.getState().setAiStatus("idle");
    scheduleReconnect();
  };
}

function clearThinkingTimer(): void {
  if (thinkingTimer) {
    clearTimeout(thinkingTimer);
    thinkingTimer = null;
  }
}

function scheduleReconnect(): void {
  if (reconnectTimer) return;
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    console.warn("[WS] max reconnect attempts reached — giving up");
    return;
  }
  const delay = Math.min(RECONNECT_BASE_MS * 2 ** reconnectAttempts, RECONNECT_MAX_MS);
  reconnectAttempts += 1;
  console.info(`[WS] reconnect attempt ${reconnectAttempts} in ${delay}ms`);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect(currentUrl);
  }, delay);
}

export function disconnect(): void {
  reconnectAttempts = MAX_RECONNECT_ATTEMPTS; // 재연결 방지
  clearThinkingTimer();
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
}

export function send(msg: WsOutgoingMessage): void {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("[WS] send skipped — not connected");
    return;
  }
  ws.send(JSON.stringify(msg));
}

export function isConnected(): boolean {
  return ws !== null && ws.readyState === WebSocket.OPEN;
}
