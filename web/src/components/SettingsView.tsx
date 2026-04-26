import { useEffect, useState } from "react";
import { useStore } from "../store";
import { connect } from "../services/websocket";
import { speakLocal } from "../services/speech";
import { speakMeloTTS } from "../services/tts";
import { API_BASE } from "../services/api";

async function fetchModels(): Promise<string[]> {
  try {
    const res = await fetch(API_BASE + "/api/settings/models");
    if (!res.ok) return [];
    const data = (await res.json()) as { models: string[] };
    return data.models;
  } catch {
    return [];
  }
}

async function fetchCurrentModel(): Promise<string> {
  try {
    const res = await fetch(API_BASE + "/api/settings/model");
    if (!res.ok) return "";
    const data = (await res.json()) as { model: string };
    return data.model;
  } catch {
    return "";
  }
}

async function setModel(model: string): Promise<boolean> {
  try {
    const res = await fetch(API_BASE + "/api/settings/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

const SAMPLE_TEXT = "안녕하세요! 저는 새싹이예요. 오늘도 잘 부탁드려요!";

const inputStyle: React.CSSProperties = {
  background: "var(--color-bg)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  color: "var(--color-text)",
  padding: "8px 12px",
  fontSize: 13,
  outline: "none",
  width: "100%",
};

const sectionStyle: React.CSSProperties = { marginTop: 28 };
const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--color-text-muted)",
  marginBottom: 6,
  display: "block",
};

export function SettingsView(): React.ReactElement {
  const wsUrl = useStore((s) => s.wsUrl);
  const setWsUrl = useStore((s) => s.setWsUrl);
  const ttsRate = useStore((s) => s.ttsRate);
  const setTtsRate = useStore((s) => s.setTtsRate);
  const ttsVoiceName = useStore((s) => s.ttsVoiceName);
  const setTtsVoiceName = useStore((s) => s.setTtsVoiceName);
  const ttsEngine = useStore((s) => s.ttsEngine);
  const setTtsEngine = useStore((s) => s.setTtsEngine);

  const [draft, setDraft] = useState(wsUrl);
  const [saved, setSaved] = useState(false);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  const [models, setModels] = useState<string[]>([]);
  const [currentModel, setCurrentModel] = useState("");
  const [modelSaving, setModelSaving] = useState(false);
  const [modelSaved, setModelSaved] = useState(false);

  // 모델 목록 로드
  useEffect(() => {
    void fetchModels().then(setModels);
    void fetchCurrentModel().then((m) => { if (m) setCurrentModel(m); });
  }, []);

  async function handleModelSave(): Promise<void> {
    if (!currentModel || modelSaving) return;
    setModelSaving(true);
    const ok = await setModel(currentModel);
    setModelSaving(false);
    if (ok) {
      setModelSaved(true);
      setTimeout(() => setModelSaved(false), 2500);
    }
  }

  // 음성 목록 로드
  useEffect(() => {
    function load() {
      const all = window.speechSynthesis?.getVoices() ?? [];
      // 한국어 음성 우선, 나머지는 뒤에
      const ko = all.filter((v) => v.lang.startsWith("ko"));
      const rest = all.filter((v) => !v.lang.startsWith("ko"));
      setVoices([...ko, ...rest]);
    }
    load();
    window.speechSynthesis?.addEventListener("voiceschanged", load);
    return () => window.speechSynthesis?.removeEventListener("voiceschanged", load);
  }, []);

  function handleSave(): void {
    const trimmed = draft.trim();
    if (!trimmed) return;
    setWsUrl(trimmed);
    connect(trimmed);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleTest(): void {
    if (ttsEngine === "system") {
      speakLocal(SAMPLE_TEXT);
    } else {
      void speakMeloTTS(SAMPLE_TEXT);
    }
  }

  return (
    <div style={{ padding: 24, maxWidth: 480, overflowY: "auto", height: "100%" }}>
      <h2 style={{ fontWeight: 700, fontSize: 18, marginBottom: 24 }}>설정</h2>

      {/* ── LLM 모델 선택 ── */}
      <section style={sectionStyle}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>LLM 모델</h3>
        <label style={labelStyle}>Ollama 모델</label>
        <select
          value={currentModel}
          onChange={(e) => setCurrentModel(e.target.value)}
          style={{ ...inputStyle, cursor: "pointer", appearance: "auto" }}
          disabled={models.length === 0}
        >
          {models.length === 0 ? (
            <option value="">모델 목록 로딩 중...</option>
          ) : (
            models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))
          )}
        </select>
        <button
          onClick={() => { void handleModelSave(); }}
          disabled={modelSaving || !currentModel}
          style={{
            marginTop: 10,
            background: modelSaved ? "var(--color-accent)" : "transparent",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: "var(--color-text)",
            cursor: modelSaving ? "not-allowed" : "pointer",
            padding: "7px 16px",
            fontSize: 13,
            width: "100%",
            opacity: modelSaving ? 0.6 : 1,
          }}
        >
          {modelSaving ? "전환 중..." : modelSaved ? "전환 완료 ✓" : "모델 전환 (백엔드 재초기화)"}
        </button>
      </section>

      {/* ── 음성 선택 ── */}
      <section style={sectionStyle}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>음성 선택</h3>

        {/* TTS 엔진 토글 */}
        <label style={labelStyle}>TTS 엔진</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
          {(["system", "melo"] as const).map((eng) => (
            <button
              key={eng}
              onMouseDown={(e) => { e.stopPropagation(); setTtsEngine(eng); }}
              style={{
                flex: 1,
                padding: "7px 10px",
                fontSize: 12,
                fontWeight: ttsEngine === eng ? 700 : 400,
                background: ttsEngine === eng ? "var(--color-accent)" : "transparent",
                border: `1px solid ${ttsEngine === eng ? "var(--color-accent)" : "var(--color-border)"}`,
                borderRadius: 8,
                color: ttsEngine === eng ? "#fff" : "var(--color-text)",
                cursor: "pointer",
              }}
            >
              {eng === "system" ? "시스템 TTS" : "MeloTTS KR"}
            </button>
          ))}
        </div>
        <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 16, lineHeight: 1.5 }}>
          {ttsEngine === "system"
            ? "macOS 시스템 음성 (유나 등) — 자연스러운 목소리"
            : "오프라인 MeloTTS — 단일 한국어 KR 음성 (더 낮은 지연)"}
        </p>

        {/* 시스템 TTS일 때만 목소리 드롭다운 표시 */}
        {ttsEngine === "system" && (
          <>
            <label style={labelStyle}>목소리</label>
            <select
              value={ttsVoiceName}
              onChange={(e) => setTtsVoiceName(e.target.value)}
              style={{ ...inputStyle, cursor: "pointer", appearance: "auto", marginBottom: 16 }}
            >
              <option value="">— 자동 (한국어 첫 번째) —</option>
              {voices.map((v) => (
                <option key={v.name} value={v.name}>
                  {v.lang.startsWith("ko") ? "🇰🇷 " : ""}{v.name}
                  {v.localService ? " (로컬)" : " (온라인)"}
                </option>
              ))}
            </select>
          </>
        )}

        <div style={{ marginTop: 0 }}>
          <label style={labelStyle}>
            재생 속도 / 음조 &nbsp;
            <span style={{ fontWeight: 700, color: "var(--color-accent)" }}>
              ×{ttsRate.toFixed(2)}
            </span>
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 11, color: "var(--color-text-muted)", minWidth: 28 }}>느림</span>
            <input
              type="range"
              min={0.5}
              max={1.8}
              step={0.05}
              value={ttsRate}
              onChange={(e) => setTtsRate(Number(e.target.value))}
              style={{ flex: 1, accentColor: "var(--color-accent)" }}
            />
            <span style={{ fontSize: 11, color: "var(--color-text-muted)", minWidth: 28 }}>빠름</span>
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 11,
              color: "var(--color-text-muted)",
              marginTop: 4,
              padding: "0 38px",
            }}
          >
            <span>0.5×</span>
            <span>1.0×</span>
            <span>1.8×</span>
          </div>
        </div>

        <button
          onClick={handleTest}
          style={{
            marginTop: 14,
            background: "transparent",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: "var(--color-text)",
            cursor: "pointer",
            padding: "7px 16px",
            fontSize: 13,
            width: "100%",
          }}
        >
          🔊 목소리 테스트
        </button>
      </section>

      {/* ── WebSocket ── */}
      <section style={sectionStyle}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>WebSocket 연결</h3>
        <label style={labelStyle}>서버 주소</label>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleSave(); }}
          style={inputStyle}
          placeholder="ws://127.0.0.1:12393/client-ws"
        />
        <button
          onClick={handleSave}
          style={{
            marginTop: 10,
            background: "var(--color-accent)",
            border: "none",
            borderRadius: 8,
            color: "#fff",
            cursor: "pointer",
            padding: "8px 18px",
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          {saved ? "저장됨 ✓" : "저장 및 재연결"}
        </button>
      </section>

      {/* ── 정보 ── */}
      <section style={{ ...sectionStyle, marginBottom: 24 }}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>정보</h3>
        <p style={{ fontSize: 13, color: "var(--color-text-muted)", lineHeight: 1.7 }}>
          새싹이 AI 비서 v1.0.0
          <br />
          오프라인 환경 전용 — 외부 CDN 미사용
        </p>
      </section>
    </div>
  );
}
