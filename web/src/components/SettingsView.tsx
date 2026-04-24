import { useState } from "react";
import { useStore } from "../store";
import { connect } from "../services/websocket";

export function SettingsView(): React.ReactElement {
  const wsUrl = useStore((s) => s.wsUrl);
  const setWsUrl = useStore((s) => s.setWsUrl);
  const [draft, setDraft] = useState(wsUrl);
  const [saved, setSaved] = useState(false);

  function handleSave(): void {
    const trimmed = draft.trim();
    if (!trimmed) return;
    setWsUrl(trimmed);
    connect(trimmed);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div style={{ padding: 24, maxWidth: 480 }}>
      <h2 style={{ fontWeight: 700, fontSize: 18, marginBottom: 24 }}>설정</h2>

      <section>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>
          WebSocket 연결
        </h3>
        <label
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            marginBottom: 12,
          }}
        >
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            WebSocket URL
          </span>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave();
            }}
            style={{
              background: "var(--color-bg)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text)",
              padding: "8px 12px",
              fontSize: 13,
              outline: "none",
              width: "100%",
            }}
            placeholder="ws://127.0.0.1:12393/client-ws"
          />
        </label>
        <button
          onClick={handleSave}
          style={{
            background: "var(--color-accent)",
            border: "none",
            borderRadius: 8,
            color: "#fff",
            cursor: "pointer",
            padding: "8px 18px",
            fontSize: 13,
            fontWeight: 600,
            transition: "opacity 0.15s",
          }}
        >
          {saved ? "저장됨" : "저장 및 재연결"}
        </button>
      </section>

      <section style={{ marginTop: 32 }}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>
          정보
        </h3>
        <p style={{ fontSize: 13, color: "var(--color-text-muted)", lineHeight: 1.7 }}>
          새싹이 AI 비서 웹 프론트엔드 v1.0.0
          <br />
          오프라인 환경 전용 — 외부 CDN 미사용
        </p>
      </section>
    </div>
  );
}
