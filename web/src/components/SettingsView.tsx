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

// ── Intent Gate ──────────────────────────────────────────────────────────────

interface IntentGateState {
  enabled: boolean;
  provider: "ollama" | "openai" | "same_as_chat";
  ollama_model: string;
  openai_model: string;
  confidence_threshold: number;
}

async function fetchIntentGate(): Promise<IntentGateState | null> {
  try {
    const res = await fetch(API_BASE + "/api/settings/intent-gate");
    if (!res.ok) return null;
    return (await res.json()) as IntentGateState;
  } catch {
    return null;
  }
}

async function apiSetIntentGate(body: {
  enabled?: boolean;
  provider?: string;
  ollama_model?: string;
  openai_model?: string;
  confidence_threshold?: number;
}): Promise<boolean> {
  try {
    const res = await fetch(API_BASE + "/api/settings/intent-gate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// ── Agent Prompts (M_17) ────────────────────────────────────────────────────

interface PromptInfo {
  prompt: string;
  is_custom: boolean | null;
  default: string | null;
  risk: "low" | "medium" | "high";
  label: string;
}

interface PromptsState {
  [key: string]: PromptInfo;
}

async function fetchAgentPrompts(): Promise<PromptsState | null> {
  try {
    const res = await fetch(API_BASE + "/api/settings/prompts");
    if (!res.ok) return null;
    const data = (await res.json()) as { prompts: PromptsState };
    return data.prompts;
  } catch {
    return null;
  }
}

async function saveAgentPrompt(key: string, prompt: string): Promise<{ ok: boolean; detail?: string }> {
  try {
    const res = await fetch(API_BASE + "/api/settings/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, prompt }),
    });
    if (!res.ok) {
      const err = (await res.json().catch(() => ({}))) as { detail?: string };
      return { ok: false, detail: err.detail };
    }
    return { ok: true };
  } catch {
    return { ok: false };
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

// GPT 모델 4종 — 계열별 안정·고성능 / 가볍고 빠름. value는 OpenAI alias (항상 최신 안정 버전으로 라우팅됨).
// 정확한 dated 버전(예: gpt-5-2025-08-07)을 쓰고 싶으면 "직접 입력" 선택.
const OPENAI_MODELS: { value: string; label: string }[] = [
  { value: "gpt-5", label: "GPT-5 (alias · 최신 안정·멀티모달)" },
  { value: "gpt-5-mini", label: "GPT-5 mini (alias · 가볍고 빠름)" },
  { value: "gpt-4o", label: "GPT-4o (alias · 안정·멀티모달)" },
  { value: "gpt-4o-mini", label: "GPT-4o mini (alias · 가볍고 빠름)" },
];
const CUSTOM_MODEL_VALUE = "__custom__";

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
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);

  const [draft, setDraft] = useState(wsUrl);
  const [saved, setSaved] = useState(false);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  // 의도 분류기 상태 (M_16)
  const [igEnabled, setIgEnabled] = useState(true);
  const [igProvider, setIgProvider] = useState<"ollama" | "openai" | "same_as_chat">("same_as_chat");
  const [igOllamaModel, setIgOllamaModel] = useState("");
  const [igSaving, setIgSaving] = useState(false);
  const [igSaved, setIgSaved] = useState(false);

  // M_17: 에이전트 지침 상태
  const [agentPrompts, setAgentPrompts] = useState<PromptsState>({});
  const [promptDrafts, setPromptDrafts] = useState<Record<string, string>>({});
  const [promptSavingKey, setPromptSavingKey] = useState<string | null>(null);
  const [promptSavedKey, setPromptSavedKey] = useState<string | null>(null);
  const [promptErrors, setPromptErrors] = useState<Record<string, string>>({});
  const [promptsOpen, setPromptsOpen] = useState(false);

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

  // 의도 분류기 초기 로드 (M_16)
  useEffect(() => {
    void fetchIntentGate().then((s) => {
      if (!s) return;
      setIgEnabled(s.enabled);
      setIgProvider(s.provider);
      setIgOllamaModel(s.ollama_model);
    });
  }, []);

  async function handleIgSave(): Promise<void> {
    if (igSaving) return;
    setIgSaving(true);
    const body: { enabled: boolean; provider: string; ollama_model?: string } = {
      enabled: igEnabled,
      provider: igProvider,
    };
    if (igProvider === "ollama" && igOllamaModel) {
      body.ollama_model = igOllamaModel;
    }
    const ok = await apiSetIntentGate(body);
    setIgSaving(false);
    if (ok) {
      setIgSaved(true);
      setTimeout(() => setIgSaved(false), 2500);
    }
  }

  // M_17: 에이전트 지침 초기 로드
  useEffect(() => {
    void fetchAgentPrompts().then((s) => {
      if (!s) return;
      setAgentPrompts(s);
      const drafts: Record<string, string> = {};
      for (const key of Object.keys(s)) {
        drafts[key] = s[key].prompt;
      }
      setPromptDrafts(drafts);
    });
  }, []);

  async function handlePromptSave(key: string): Promise<void> {
    if (promptSavingKey) return;
    setPromptSavingKey(key);
    setPromptErrors((prev) => ({ ...prev, [key]: "" }));
    const result = await saveAgentPrompt(key, promptDrafts[key] ?? "");
    setPromptSavingKey(null);
    if (result.ok) {
      setPromptSavedKey(key);
      // 상태 갱신
      setAgentPrompts((prev) => ({
        ...prev,
        [key]: {
          ...prev[key],
          prompt: promptDrafts[key] ?? "",
          is_custom: key === "persona" ? null : Boolean((promptDrafts[key] ?? "").trim()),
        },
      }));
      setTimeout(() => setPromptSavedKey(null), 2500);
    } else {
      setPromptErrors((prev) => ({ ...prev, [key]: result.detail ?? "저장 실패" }));
    }
  }

  function handlePromptReset(key: string): void {
    const def = agentPrompts[key]?.default ?? "";
    setPromptDrafts((prev) => ({ ...prev, [key]: def }));
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

      {/* ── 화면 테마 ── */}
      <section style={{ marginTop: 0 }}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>화면 테마</h3>
        <div style={{ display: "flex", gap: 8 }}>
          {([
            { id: "dark" as const, label: "🌙 다크" },
            { id: "light" as const, label: "☀️ 라이트" },
          ]).map(({ id, label }) => (
            <button
              key={id}
              onMouseDown={(e) => { e.stopPropagation(); setTheme(id); }}
              style={{
                flex: 1,
                padding: "8px 10px",
                fontSize: 13,
                fontWeight: theme === id ? 700 : 400,
                background: theme === id ? "var(--color-accent)" : "transparent",
                border: `1px solid ${theme === id ? "var(--color-accent)" : "var(--color-border)"}`,
                borderRadius: 8,
                color: theme === id ? "#fff" : "var(--color-text)",
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 6, lineHeight: 1.5 }}>
          전체 UI 색상이 즉시 전환됩니다. 선택은 기기에 저장돼 다음 실행 시에도 유지됩니다.
        </p>
      </section>

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
            {(() => {
              const isCustom = !OPENAI_MODELS.some((m) => m.value === openaiModel) && !!openaiModel;
              const selectValue = isCustom ? CUSTOM_MODEL_VALUE : openaiModel;
              return (
                <>
                  <select
                    value={selectValue}
                    onChange={(e) => {
                      if (e.target.value === CUSTOM_MODEL_VALUE) {
                        // 직접 입력 모드 진입 — 기존 alias가 있었으면 그 값을 시드로 두기
                        if (OPENAI_MODELS.some((m) => m.value === openaiModel)) {
                          setOpenaiModel(openaiModel + "-2025-08-07");
                        }
                      } else {
                        setOpenaiModel(e.target.value);
                      }
                    }}
                    style={{ ...inputStyle, cursor: "pointer", appearance: "auto", marginBottom: isCustom ? 6 : 0 }}
                  >
                    {OPENAI_MODELS.map((m) => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                    <option value={CUSTOM_MODEL_VALUE}>── 직접 입력 (정확한 dated 버전) ──</option>
                  </select>
                  {isCustom && (
                    <input
                      type="text"
                      value={openaiModel}
                      onChange={(e) => setOpenaiModel(e.target.value)}
                      onClick={() => window.electronAPI?.restoreFocus()}
                      placeholder="예: gpt-5-2025-08-07, gpt-4o-2024-11-20"
                      autoComplete="off"
                      spellCheck={false}
                      style={inputStyle}
                    />
                  )}
                </>
              );
            })()}
            <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 6, lineHeight: 1.5 }}>
              <strong>alias</strong>는 OpenAI가 항상 최신 안정 버전으로 라우팅하는 별칭입니다(예: <code>gpt-5</code> → 최신 GPT-5 안정 버전).
              특정 dated 버전을 고정하려면 "직접 입력"을 선택하고 OpenAI 콘솔의 모델 ID를 그대로 입력하세요.
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

      {/* ── 의도 분류기 (M_16) ── */}
      <section style={sectionStyle}>
        <h3 style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>의도 분류기</h3>
        <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 12, lineHeight: 1.5 }}>
          의도 분류기는 입력마다 1회 짧은 추론으로 일정/공용문서검색/내업무검색/노트저장 등을 구분해
          정확한 도구와 검색 범위를 고릅니다. '메인 모델과 동일'이 기본값입니다.
        </p>

        {/* 활성화 토글 */}
        <label style={labelStyle}>분류기 사용</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          {([
            { id: true, label: "사용" },
            { id: false, label: "사용 안 함 (레거시 키워드)" },
          ] as const).map(({ id, label }) => (
            <button
              key={String(id)}
              onMouseDown={(e) => { e.stopPropagation(); setIgEnabled(id); }}
              style={{
                flex: 1,
                padding: "8px 10px",
                fontSize: 13,
                fontWeight: igEnabled === id ? 700 : 400,
                background: igEnabled === id ? "var(--color-accent)" : "transparent",
                border: `1px solid ${igEnabled === id ? "var(--color-accent)" : "var(--color-border)"}`,
                borderRadius: 8,
                color: igEnabled === id ? "#fff" : "var(--color-text)",
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {/* provider 선택 */}
        {igEnabled && (
          <>
            <label style={labelStyle}>분류기 모델 공급자</label>
            <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
              {([
                { id: "same_as_chat" as const, label: "메인 모델과 동일" },
                { id: "ollama" as const, label: "Ollama (별도 모델)" },
                { id: "openai" as const, label: "OpenAI" },
              ]).map(({ id, label }) => (
                <button
                  key={id}
                  onMouseDown={(e) => { e.stopPropagation(); setIgProvider(id); }}
                  style={{
                    flex: 1,
                    padding: "7px 6px",
                    fontSize: 12,
                    fontWeight: igProvider === id ? 700 : 400,
                    background: igProvider === id ? "var(--color-accent)" : "transparent",
                    border: `1px solid ${igProvider === id ? "var(--color-accent)" : "var(--color-border)"}`,
                    borderRadius: 8,
                    color: igProvider === id ? "#fff" : "var(--color-text)",
                    cursor: "pointer",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>

            {igProvider === "ollama" && (
              <>
                <label style={labelStyle}>분류기 Ollama 모델</label>
                <select
                  value={igOllamaModel}
                  onChange={(e) => setIgOllamaModel(e.target.value)}
                  style={{ ...inputStyle, cursor: "pointer", appearance: "auto", marginBottom: 6 }}
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
                <p style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 8, lineHeight: 1.5 }}>
                  분류는 6지선다 + 짧은 출력이므로 가벼운 모델(예: gemma4:e2b)도 충분합니다.
                </p>
              </>
            )}
          </>
        )}

        <button
          onClick={() => { void handleIgSave(); }}
          disabled={igSaving}
          style={{
            marginTop: 6,
            background: igSaved ? "var(--color-accent)" : "transparent",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: igSaved ? "#fff" : "var(--color-text)",
            cursor: igSaving ? "not-allowed" : "pointer",
            padding: "7px 16px",
            fontSize: 13,
            width: "100%",
            opacity: igSaving ? 0.6 : 1,
          }}
        >
          {igSaving ? "적용 중..." : igSaved ? "적용됨 ✓" : "의도 분류기 적용"}
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

      {/* ── 지침 관리 (M_17 에이전트별) ── */}
      <section style={sectionStyle}>
        <button
          onMouseDown={(e) => { e.stopPropagation(); setPromptsOpen((v) => !v); }}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
            background: "transparent",
            border: "none",
            padding: 0,
            cursor: "pointer",
            marginBottom: promptsOpen ? 12 : 0,
          }}
        >
          <h3 style={{ fontWeight: 600, fontSize: 14, margin: 0 }}>
            지침 관리 (에이전트별)
          </h3>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            {promptsOpen ? "▲ 접기" : "▼ 펼치기"}
          </span>
        </button>

        {promptsOpen && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {(["persona", "knowledge_note", "doc_query_answer", "work_query_answer", "intent_classify", "meeting_minutes"] as const).map((key) => {
              const info = agentPrompts[key];
              if (!info) return null;
              const isSaving = promptSavingKey === key;
              const isSaved = promptSavedKey === key;
              const errorMsg = promptErrors[key] ?? "";
              const isHigh = info.risk === "high";
              const hasReset = key !== "persona" && info.default !== null;

              return (
                <div key={key} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 14 }}>
                  {/* 헤더: 레이블 + 배지 */}
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{info.label}</span>
                    {info.is_custom === true && (
                      <span style={{
                        fontSize: 10, fontWeight: 700,
                        background: "var(--color-accent)", color: "#fff",
                        borderRadius: 4, padding: "1px 5px",
                      }}>커스텀</span>
                    )}
                    {isHigh && (
                      <span style={{
                        fontSize: 10, fontWeight: 700,
                        background: "#c0392b", color: "#fff",
                        borderRadius: 4, padding: "1px 5px",
                      }}>고급</span>
                    )}
                  </div>
                  {isHigh && (
                    <p style={{ fontSize: 11, color: "#c0392b", marginBottom: 8, lineHeight: 1.5 }}>
                      주의: 잘못 편집 시 의도 분류 정확도가 하락할 수 있습니다. 문제 시 기본값으로 복원하세요.
                    </p>
                  )}
                  <textarea
                    value={promptDrafts[key] ?? ""}
                    onChange={(e) => setPromptDrafts((prev) => ({ ...prev, [key]: e.target.value }))}
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={() => window.electronAPI?.restoreFocus()}
                    rows={key === "persona" ? 6 : key === "intent_classify" ? 14 : 8}
                    style={{
                      ...inputStyle,
                      resize: "vertical",
                      fontFamily: "monospace",
                      fontSize: 11,
                      lineHeight: 1.55,
                      whiteSpace: "pre",
                      maxWidth: "100%",
                    }}
                    placeholder={key === "persona" ? "페르소나를 입력하세요 (비워두면 저장 불가)" : `${info.label} 기본값 사용 중`}
                  />
                  {errorMsg && (
                    <p style={{ fontSize: 11, color: "#c0392b", marginTop: 4 }}>{errorMsg}</p>
                  )}
                  <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                    {hasReset && (
                      <button
                        onMouseDown={(e) => { e.stopPropagation(); handlePromptReset(key); }}
                        style={{
                          flex: 1,
                          background: "transparent",
                          border: "1px solid var(--color-border)",
                          borderRadius: 8,
                          color: "var(--color-text-muted)",
                          cursor: "pointer",
                          padding: "7px 8px",
                          fontSize: 11,
                        }}
                      >
                        기본값으로 복원
                      </button>
                    )}
                    <button
                      onClick={() => { void handlePromptSave(key); }}
                      disabled={isSaving}
                      style={{
                        flex: 2,
                        background: isSaved ? "var(--color-accent)" : "transparent",
                        border: "1px solid var(--color-border)",
                        borderRadius: 8,
                        color: isSaved ? "#fff" : "var(--color-text)",
                        cursor: isSaving ? "not-allowed" : "pointer",
                        padding: "7px 10px",
                        fontSize: 12,
                        opacity: isSaving ? 0.6 : 1,
                      }}
                    >
                      {isSaving ? "적용 중..." : isSaved ? "저장됨 ✓" : "지침 저장"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
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
