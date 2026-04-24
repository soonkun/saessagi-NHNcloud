import type { WsIncomingMessage, WsOutgoingMessage } from "../types";
import { useStore } from "../store";

// ────────────────────────────────────────────────────────────
// WebSocket 싱글턴 서비스
// ────────────────────────────────────────────────────────────

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let currentUrl = "";

const RECONNECT_DELAY_MS = 3000;

function dispatch(msg: WsIncomingMessage): void {
  const store = useStore.getState();

  switch (msg.type) {
    case "control":
      if (msg.command === "start-mic") store.setMicOn(true);
      else if (msg.command === "stop-mic") store.setMicOn(false);
      else if (msg.command === "conversation-chain-start")
        store.setAiStatus("thinking");
      else if (msg.command === "conversation-chain-end")
        store.setAiStatus("idle");
      break;

    case "audio":
      // 음성 재생은 미구현 — 텍스트만 처리
      if (msg.display_text?.text) {
        store.addMessage({ role: "ai", text: msg.display_text.text });
      }
      break;

    case "message":
      store.addMessage({ role: msg.role, text: msg.message });
      if (msg.role === "ai") {
        store.setAiStatus("idle");
      }
      break;

    case "avatar-state":
      store.setEmotion(msg.emotion);
      store.setSpeaking(msg.speaking);
      store.setAiStatus(msg.speaking ? "speaking" : "idle");
      break;

    case "tool-call-status":
      // tool 상태는 현재 UI에 별도 표시 없음 — 무시
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
    scheduleReconnect();
  };
}

function scheduleReconnect(): void {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect(currentUrl);
  }, RECONNECT_DELAY_MS);
}

export function disconnect(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null; // reconnect 방지
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
