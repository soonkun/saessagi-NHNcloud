import { useEffect, useState } from "react";
import { useStore } from "../store";
import { connect } from "../services/websocket";
import { speakLocal } from "../services/speech";
import { speakMeloTTS } from "../services/tts";
import { API_BASE } from "../services/api";

// ── Ollama ────────────────────────────────────────────────────────────────────

async function fetchOllamaModels(): Promise<string[]> {
  try {
    const res = await fetch(API_BASE + "/api/settings/models");
    if (!res.ok) return [];
    const data = (await res.json()) as { models: string[] };
    return data.models;
  } catch {
    return [];
  }
}

// ── Meeting prompt ───────────────────────────────────────────────────────────

interface MeetingPromptState {
  prompt: string;
  is_custom: boolean;
  default_prompt: string;
}

async function fetchMeetingPrompt(): Promise<MeetingPromptState | null> {
  try {
    const res = await fetch(API_BASE + "/api/settings/meeting-prompt");
    if (!res.ok) return null;
    return (await res.json()) as MeetingPromptState;
  } catch {
    return null;
  }
}

async function saveMeetingPrompt(prompt: string): Promise<boolean> {
  try {
    const res = await fetch(API_BASE + "/api/settings/meeting-prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// ── LLM provider ─────────────────────────────────────────────────────────────

interface LlmProviderState {
  provider: "ollama" | "openai";
  openai_api_key_set: boolean;
  openai_model: string;
  ollama_model: string;
}

async function fetchLlmProvider(): Promise<LlmProviderState | null> {
  try {
    const res = await fetch(API_BASE + "/api/settings/llm-provider");
    if (!res.ok) return null;
    return (await res.json()) as LlmProviderState;
  } catch {
    return null;
  }
}

async function apiSetLlmProvider(body: {
  provider: string;
  openai_api_key?: string;
  openai_model?: string;
  ollama_model?: string;
}): Promise<boolean> {
  try {
    const res = await fetch(API_BASE + "/api/settings/llm-provider", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// ── Styles ────────────────────────────────────────────────────────────────────

const SAMPLE_TEXT = "안녕하세요! 저는 새싹이예요. 오늘도 잘 부탁드려요!";

// GPT 모델 4종 — 계열별로 안정·고성능 / 가볍고 빠름.
// OpenAI가 신규 모델 출시 시 여기를 갱신하면 됨.
const OPENAI_MODELS: { value: string; label: string }[] = [
  { value: "gpt-5", label: "GPT-5 (안정·고성능)" },
  { value: "gpt-5-mini", label: "GPT-5 mini (가볍고 빠름)" },
  { value: "gpt-4o", label: "GPT-4o (안정·고성능)" },
  { value: "gpt-4o-mini", label: "GPT-4o mini (가볍고 빠름)" },
];

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

// ── Component ─────────────────────────────────────────────────────────────────

export function SettingsView(): React.ReactElement {
  const wsUrl = useStore((s) => s.wsUrl);
  const setWsUrl = useStore((s) => s.setWsUrl);
  const ttsRate = useStore((s) => s.ttsRate);
  const setTtsRate = useStore((s) => s.setTtsRate);
  const ttsVoiceName = useStore((s) => s.ttsVoiceName);
  const setTtsVoiceName = useStore((s) => s.setTtsVoiceName);
  const ttsEngine = useStore((s) => s.ttsEngine);
  const setTtsEngine = useStore((s) => s.setTtsEngine);
  const llmInfo = useStore((s) => s.llmInfo);
  const setLlmInfo = useStore((s) => s.setLlmInfo);

  const [draft, setDraft] = useState(wsUrl);
  const [saved, setSaved] = useState(false);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  // 회의록 프롬프트 상태
  const [meetingPrompt, setMeetingPrompt] = useState("");
  const [meetingPromptDefault, setMeetingPromptDefault] = useState("");
  const [isCustomPrompt, setIsCustomPrompt] = useState(false);
  const [promptSaving, setPromptSaving] = useState(false);
  const [promptSaved, setPromptSaved] = useState(false);

  // LLM 공급자 상태 — store(localStorage)와 동기화돼 탭 전환 후에도 유지
  const [llmProvider, setLlmProvider] = useState<"ollama" | "openai">(
    llmInfo?.provider ?? "ollama"
  );
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaModel, setOllamaModel] = useState(
    llmInfo?.provider === "ollama" ? llmInfo.model : ""
  );
  const [openaiModel, setOpenaiModel] = useState(
    llmInfo?.provider === "openai" ? llmInfo.model : "gpt-4o-mini"
  );
  const [openaiKey, setOpenaiKey] = useState("");
  const [openaiKeyPlaceholder, setOpenaiKeyPlaceholder] = useState("sk-...");
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmSaved, setLlmSaved] = useState(false);

  // 회의록 프롬프트 초기 로드
  useEffect(() => {
    void fetchMeetingPrompt().then((s) => {
      if (!s) return;
      setMeetingPrompt(s.prompt);
      setMeetingPromptDefault(s.default_prompt);
      setIsCustomPrompt(s.is_custom);
    });
  }, []);

  async function handlePromptSave(): Promise<void> {
    if (promptSaving) return;
    setPromptSaving(true);
    const ok = await saveMeetingPrompt(meetingPrompt);
    setPromptSaving(false);
    if (ok) {
      setIsCustomPrompt(meetingPrompt.trim() !== meetingPromptDefault.trim());
      setPromptSaved(true);
      setTimeout(() => setPromptSaved(false), 2500);
    }
  }

  function handlePromptReset(): void {
    setMeetingPrompt(meetingPromptDefault);
  }

  // 초기 로드 — 백엔드 값을 가져와 UI·store 모두 동기화
  useEffect(() => {
    void fetchLlmProvider().then((s) => {
      if (!s) return;
      const provider = s.provider === "openai" ? "openai" : "ollama";
      setLlmProvider(provider);
      setOllamaModel(s.ollama_model);
      setOpenaiModel(s.openai_model);
      if (s.openai_api_key_set) setOpenaiKeyPlaceholder("••••••••••••••••");
      // store 동기화 — 백엔드 = source of truth
      setLlmInfo({
        provider,
        model: provider === "openai" ? s.openai_model : s.ollama_model,
      });
    });
    void fetchOllamaModels().then(setOllamaModels);
  }, [setLlmInfo]);

  async function handleLlmSave(): Promise<void> {
    if (llmSaving) return;
    setLlmSaving(true);
    const body =
      llmProvider === "openai"
        ? {
            provider: "openai",
            openai_api_key: openaiKey || undefined,
            openai_model: openaiModel,
          }
        : {
            provider: "ollama",
            ollama_model: ollamaModel,
          };
    const ok = await apiSetLlmProvider(body);
    setLlmSaving(false);
    if (ok) {
      setLlmSaved(true);
      // store + localStorage 즉시 갱신 — 탭 이동·재시작 후에도 표시 유지
      setLlmInfo({
        provider: llmProvider,
        model: llmProvider === "openai" ? openaiModel : ollamaModel,
      });
      if (openaiKey) {
        setOpenaiKey("");
        setOpenaiKeyPlaceholder("••••••••••••••••");
      }
      setTimeout(() => setLlmSaved(false), 2500);
    }
  }

  // 음성 목록 로드
  useEffect(() => {
    function load() {
      const all = window.speechSynthesis?.getVoices() ?? [];
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

  const providerBtnStyle = (active: boolean): React.CSSProperties => ({
    flex: 1,
    padding: "8px 10px",
    fontSize: 13,
    fontWeight: active ? 700 : 400,
    background: active ? "var(--color-accent)" : "transparent",
    border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
    borderRadius: 8,
    color: active ? "#fff" : "var(--color-text)",
    cursor: "pointer",
  });

  return (
    <div style={{ padding: 24, maxWidth: 480, overflowY: "auto", height: "100%" }}>
      <h2 style={{ fontWeight: 700, fontSize: 18, marginBottom: 24 }}>설정</h2>

      {/* ── LLM 공급자 선택 ── */}
      <section style={sectionStyle}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>LLM 설정</h3>

        {/* 공급자 토글 */}
        <label style={labelStyle}>LLM 공급자</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          <button
            onMouseDown={(e) => { e.stopPropagation(); setLlmProvider("ollama"); }}
            style={providerBtnStyle(llmProvider === "ollama")}
          >
            Ollama (로컬)
          </button>
          <button
            onMouseDown={(e) => { e.stopPropagation(); setLlmProvider("openai"); }}
            style={providerBtnStyle(llmProvider === "openai")}
          >
            ChatGPT (OpenAI)
          </button>
        </div>

        {llmProvider === "ollama" ? (
          <>
            <label style={labelStyle}>Ollama 모델</label>
            <select
              value={ollamaModel}
              onChange={(e) => setOllamaModel(e.target.value)}
              style={{ ...inputStyle, cursor: "pointer", appearance: "auto" }}
              disabled={ollamaModels.length === 0}
            >
              {ollamaModels.length === 0 ? (
                <option value="">모델 목록 로딩 중...</option>
              ) : (
                ollamaModels.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))
              )}
            </select>
            <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 6, lineHeight: 1.5 }}>
              로컬 Ollama 서버의 모델을 사용합니다. 완전 오프라인 동작.
            </p>
          </>
        ) : (
          <>
            <label style={labelStyle}>OpenAI API 키</label>
            <input
              type="text"
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              onMouseDown={(e) => e.stopPropagation()}
              onCopy={(e) => e.preventDefault()}
              onCut={(e) => e.preventDefault()}
              placeholder={openaiKeyPlaceholder}
              autoComplete="off"
              spellCheck={false}
              style={{
                ...inputStyle,
                marginBottom: 10,
                userSelect: "none",
              } as React.CSSProperties}
              // type="text"이지만 CSS로 시각적 마스킹 — Electron password 입력 호환 문제 우회
              ref={(el) => { if (el) el.style.setProperty("-webkit-text-security", "disc"); }}
            />
            <label style={labelStyle}>GPT 모델</label>
            <select
              value={openaiModel}
              onChange={(e) => setOpenaiModel(e.target.value)}
              style={{ ...inputStyle, cursor: "pointer", appearance: "auto" }}
            >
              {/* 저장된 값이 4개 목록에 없으면 표시용으로 한 번만 추가 */}
              {!OPENAI_MODELS.some((m) => m.value === openaiModel) && openaiModel && (
                <option value={openaiModel}>{openaiModel} (저장된 값)</option>
              )}
              {OPENAI_MODELS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
            <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 6, lineHeight: 1.5 }}>
              OpenAI API를 통해 ChatGPT 모델을 사용합니다. API 키가 필요하며 인터넷 연결이 있어야 합니다.
              비전(이미지) 처리는 GPT-4o 이상에서 지원됩니다.
            </p>
          </>
        )}

        <button
          onClick={() => { void handleLlmSave(); }}
          disabled={llmSaving}
          style={{
            marginTop: 10,
            background: llmSaved ? "var(--color-accent)" : "transparent",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: "var(--color-text)",
            cursor: llmSaving ? "not-allowed" : "pointer",
            padding: "7px 16px",
            fontSize: 13,
            width: "100%",
            opacity: llmSaving ? 0.6 : 1,
          }}
        >
          {llmSaving ? "전환 중..." : llmSaved ? "전환 완료 ✓" : "LLM 적용 (백엔드 재초기화)"}
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

      {/* ── 회의록 작성 지침 ── */}
      <section style={sectionStyle}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>회의록 작성 지침</h3>
        <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 10, lineHeight: 1.5 }}>
          회의록 생성 시 LLM에 전달되는 지침입니다. 직접 편집하거나 기본값으로 초기화할 수 있습니다.
          {isCustomPrompt && (
            <span style={{ marginLeft: 6, color: "var(--color-accent)", fontWeight: 600 }}>
              (커스텀 적용 중)
            </span>
          )}
        </p>
        <textarea
          value={meetingPrompt}
          onChange={(e) => setMeetingPrompt(e.target.value)}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={() => window.electronAPI?.restoreFocus()}
          rows={18}
          style={{
            ...inputStyle,
            resize: "vertical",
            fontFamily: "monospace",
            fontSize: 11,
            lineHeight: 1.55,
            whiteSpace: "pre",
          }}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button
            onClick={handlePromptReset}
            style={{
              flex: 1,
              background: "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text-muted)",
              cursor: "pointer",
              padding: "7px 10px",
              fontSize: 12,
            }}
          >
            기본값으로 초기화
          </button>
          <button
            onClick={() => { void handlePromptSave(); }}
            disabled={promptSaving}
            style={{
              flex: 2,
              background: promptSaved ? "var(--color-accent)" : "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: promptSaved ? "#fff" : "var(--color-text)",
              cursor: promptSaving ? "not-allowed" : "pointer",
              padding: "7px 10px",
              fontSize: 12,
              opacity: promptSaving ? 0.6 : 1,
            }}
          >
            {promptSaving ? "저장 중..." : promptSaved ? "저장됨 ✓" : "지침 저장"}
          </button>
        </div>
      </section>

      {/* ── 정보 ── */}
      <section style={{ ...sectionStyle, marginBottom: 24 }}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>정보</h3>
        <p style={{ fontSize: 13, color: "var(--color-text-muted)", lineHeight: 1.7 }}>
          새싹이 AI 비서 v1.0.0
        </p>
      </section>
    </div>
  );
}
