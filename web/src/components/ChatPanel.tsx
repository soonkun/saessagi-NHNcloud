import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  MessageCircle,
  Calendar,
  FolderOpen,
  FileAudio,
  Settings,
  BookOpen,
  Send,
  Mic,
  MicOff,
  X,
  RotateCcw,
  Download,
  Paperclip,
} from "lucide-react";
import { useStore } from "../store";
import { send } from "../services/websocket";
import { invalidateDocsCache } from "../services/websocket";
import { getDocumentDownloadUrl, uploadDocument } from "../services/api";
import type { MessageAttachment } from "../types";

// `[[note:slug]]` 마커는 칩으로 별도 표시되므로 본문 렌더에서는 제거
function stripNoteMarkers(text: string): string {
  return text.replace(/\[\[note:[^\]]+\]\]/g, "").replace(/\s{2,}/g, " ").trim();
}
import { startVoice, stopVoice } from "../services/voice";
import { CalendarView } from "./CalendarView";
import { DocumentsView } from "./DocumentsView";
import { MeetingView } from "./MeetingView";
import { NotesView } from "./NotesView";
import { SettingsView } from "./SettingsView";
import type { Position, ChatTab } from "../types";

const PANEL_W = 580;
const PANEL_H = 660;
const GAP = 8;

interface ChatPanelProps {
  charPosition: Position;
  charSize: number;
}

function calcPanelStyle(charPos: Position, charSize: number): React.CSSProperties {
  let left = charPos.x + charSize - PANEL_W;
  let top = charPos.y - PANEL_H - GAP;
  if (left < 8) left = 8;
  if (left + PANEL_W > window.innerWidth - 8)
    left = window.innerWidth - 8 - PANEL_W;
  if (top < 8) top = charPos.y + charSize + GAP;
  if (top + PANEL_H > window.innerHeight - 8)
    top = window.innerHeight - 8 - PANEL_H;
  return { position: "fixed", left, top, width: PANEL_W, height: PANEL_H };
}

const STATUS_LABEL: Record<string, string> = {
  idle: "대기 중",
  thinking: "생각 중...",
  speaking: "말하는 중...",
};

const STATUS_COLOR: Record<string, string> = {
  idle: "#888",
  thinking: "var(--color-accent)",
  speaking: "#4caf84",
};

const TABS: { id: ChatTab; label: string; Icon: React.ElementType }[] = [
  { id: "chat", label: "새싹이", Icon: MessageCircle },
  { id: "calendar", label: "일정표", Icon: Calendar },
  { id: "documents", label: "문서", Icon: FolderOpen },
  { id: "meeting", label: "회의록", Icon: FileAudio },
  { id: "notes", label: "노트", Icon: BookOpen },
  { id: "settings", label: "설정", Icon: Settings },
];

// ────────────────────────────────────────────────────────────
// Chat content
// ────────────────────────────────────────────────────────────

export function ChatContent({
  emptyHero,
}: {
  emptyHero?: React.ReactNode;
} = {}): React.ReactElement {
  const messages = useStore((s) => s.messages);
  const aiStatus = useStore((s) => s.aiStatus);
  const addMessage = useStore((s) => s.addMessage);
  const llmInfo = useStore((s) => s.llmInfo);
  const setChatTab = useStore((s) => s.setChatTab);
  const setSelectedNoteSlug = useStore((s) => s.setSelectedNoteSlug);

  const [input, setInput] = useState("");
  const [voiceActive, setVoiceActive] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // 첨부 자료 (업로드 완료된 doc 목록 + 업로드 진행 중 항목)
  const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
  const [uploadingItems, setUploadingItems] = useState<
    { key: string; filename: string; progress: number; error?: string }[]
  >([]);

  function handleNewHistory(): void {
    send({ type: "create-new-history" });
    // 백엔드 응답(new-history-created)으로 clearMessages가 호출됨
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 50);
  }, []);

  function handleSend(): void {
    const text = input.trim();
    if (!text && attachments.length === 0) return;
    // 화면 표시는 사용자 원문 + attachments 칩으로
    addMessage({
      role: "human",
      text: text || "(첨부 자료를 정리해 주세요)",
      attachments: attachments.length > 0 ? attachments : undefined,
    });
    // 백엔드에는 prefix로 doc_id 메타 자동 삽입 — LLM이 related_docs에 활용
    let payload = text || "(이 첨부 자료를 검토해서 업무 노트로 정리해줘.)";
    if (attachments.length > 0) {
      const meta = attachments
        .map((a) => `${a.filename} (doc_id: ${a.id})`)
        .join("; ");
      payload = `[첨부 자료: ${meta}]\n${payload}`;
    }
    send({ type: "text-input", text: payload });
    setInput("");
    setAttachments([]);
  }

  // 첨부 파일 업로드
  function handleAttachClick(): void {
    fileInputRef.current?.click();
  }

  async function handleFilesPicked(files: FileList | null): Promise<void> {
    if (!files || files.length === 0) return;
    window.electronAPI?.restoreFocus();
    for (const file of Array.from(files)) {
      const key = `${Date.now()}_${file.name}`;
      setUploadingItems((prev) => [...prev, { key, filename: file.name, progress: 0 }]);
      try {
        const doc = await uploadDocument(file, null, (pct) => {
          setUploadingItems((prev) =>
            prev.map((it) => (it.key === key ? { ...it, progress: pct } : it))
          );
        });
        // 업로드 완료 → 첨부 목록에 추가하고 진행 목록에서 제거
        setAttachments((prev) => [...prev, { id: doc.id, filename: doc.filename }]);
        setUploadingItems((prev) => prev.filter((it) => it.key !== key));
        invalidateDocsCache();
      } catch (err) {
        const msg = err instanceof Error ? err.message : "업로드 실패";
        setUploadingItems((prev) =>
          prev.map((it) => (it.key === key ? { ...it, error: msg, progress: -1 } : it))
        );
        // 5초 후 자동 제거
        setTimeout(() => {
          setUploadingItems((prev) => prev.filter((it) => it.key !== key));
        }, 5000);
      }
    }
    // 같은 파일 재선택 가능하도록 reset
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function removeAttachment(id: string): void {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    // nativeEvent.isComposing: 한국어/일본어 IME 조합 중 Enter는 무시
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleMicToggle(): void {
    if (voiceActive) {
      stopVoice();
      setVoiceActive(false);
      return;
    }
    void startVoice({
      onStart: () => setVoiceActive(true),
      onStop: () => setVoiceActive(false),
      onText: (text) => {
        addMessage({ role: "human", text });
        send({ type: "text-input", text });
      },
      onError: (msg) => {
        console.warn("[STT]", msg);
        setVoiceActive(false);
      },
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 상태 표시 줄 */}
      <div
        style={{
          padding: "8px 16px",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexShrink: 0,
        }}
      >
        <span
          className={aiStatus !== "idle" ? "status-blink" : ""}
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: STATUS_COLOR[aiStatus] ?? "#888",
            display: "inline-block",
            flexShrink: 0,
          }}
        />
        <span style={{ color: "var(--color-text-muted)", fontSize: 12 }}>
          {STATUS_LABEL[aiStatus] ?? ""}
        </span>
        {llmInfo && (
          <span
            title={`현재 LLM: ${llmInfo.provider === "openai" ? "OpenAI" : "Ollama"} / ${llmInfo.model}`}
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "2px 7px",
              borderRadius: 10,
              background: llmInfo.provider === "openai" ? "rgba(16,163,127,0.18)" : "rgba(100,140,220,0.18)",
              color: llmInfo.provider === "openai" ? "#10a37f" : "#7aa8ff",
              border: `1px solid ${llmInfo.provider === "openai" ? "rgba(16,163,127,0.4)" : "rgba(100,140,220,0.4)"}`,
              whiteSpace: "nowrap",
              maxWidth: 160,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {llmInfo.provider === "openai" ? "GPT" : "Ollama"} · {llmInfo.model}
          </span>
        )}
        {voiceActive && (
          <span
            className="status-blink"
            style={{ fontSize: 12, color: "#e74c3c", marginLeft: "auto" }}
          >
            ● 녹음 중
          </span>
        )}
        <button
          onClick={handleNewHistory}
          title="새 대화 시작 (대화 기억 초기화)"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-muted)",
            display: "flex",
            alignItems: "center",
            padding: "2px 4px",
            borderRadius: 4,
            flexShrink: 0,
          }}
        >
          <RotateCcw size={13} />
        </button>
      </div>

      {/* 메시지 목록 */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {messages.length === 0 && (
          emptyHero ?? (
            <div
              style={{
                color: "var(--color-text-muted)",
                textAlign: "center",
                marginTop: 40,
                fontSize: 13,
              }}
            >
              안녕하세요! 무엇을 도와드릴까요?
            </div>
          )
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className="msg-enter"
            style={{
              display: "flex",
              justifyContent: msg.role === "human" ? "flex-end" : "flex-start",
            }}
          >
            <div
              className="msg-bubble"
              style={{
                maxWidth: "80%",
                padding: "8px 12px",
                borderRadius:
                  msg.role === "human"
                    ? "12px 12px 4px 12px"
                    : "12px 12px 12px 4px",
                background:
                  msg.role === "human"
                    ? "var(--color-msg-human)"
                    : "var(--color-msg-ai)",
                border: "1px solid var(--color-border)",
                fontSize: 13,
                lineHeight: 1.5,
                wordBreak: "break-word",
              }}
            >
              {msg.role === "human" ? (
                <div>
                  <span style={{ whiteSpace: "pre-wrap" }}>{msg.text}</span>
                  {msg.attachments && msg.attachments.length > 0 && (
                    <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {msg.attachments.map((a) => (
                        <a
                          key={a.id}
                          href={getDocumentDownloadUrl(a.id)}
                          download={a.filename}
                          title={`첨부 다운로드: ${a.filename}`}
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            padding: "2px 8px",
                            fontSize: 11,
                            borderRadius: 10,
                            background: "rgba(255,255,255,0.12)",
                            border: "1px solid rgba(255,255,255,0.2)",
                            color: "var(--color-text)",
                            textDecoration: "none",
                            maxWidth: 220,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          <Paperclip size={11} />
                          {a.filename}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    p: ({ children }) => (
                      <p style={{ margin: "0 0 6px", whiteSpace: "pre-wrap" }}>{children}</p>
                    ),
                    table: ({ children }) => (
                      <div style={{ overflowX: "auto", margin: "6px 0" }}>
                        <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
                          {children}
                        </table>
                      </div>
                    ),
                    th: ({ children }) => (
                      <th style={{ border: "1px solid var(--color-border)", padding: "4px 8px", background: "var(--color-sidebar)", fontWeight: 600, textAlign: "left" }}>
                        {children}
                      </th>
                    ),
                    td: ({ children }) => (
                      <td style={{ border: "1px solid var(--color-border)", padding: "4px 8px" }}>
                        {children}
                      </td>
                    ),
                    pre: ({ children }) => (
                      <pre style={{ background: "rgba(0,0,0,0.3)", borderRadius: 6, padding: "8px 10px", overflowX: "auto", fontSize: 12, margin: "6px 0", fontFamily: "monospace" }}>
                        {children}
                      </pre>
                    ),
                    code: ({ children }) => (
                      <code style={{ background: "rgba(255,255,255,0.08)", borderRadius: 3, padding: "1px 4px", fontSize: 12, fontFamily: "monospace" }}>
                        {children}
                      </code>
                    ),
                    ul: ({ children }) => (
                      <ul style={{ margin: "4px 0", paddingLeft: 18 }}>{children}</ul>
                    ),
                    ol: ({ children }) => (
                      <ol style={{ margin: "4px 0", paddingLeft: 18 }}>{children}</ol>
                    ),
                    li: ({ children }) => (
                      <li style={{ margin: "2px 0" }}>{children}</li>
                    ),
                    strong: ({ children }) => (
                      <strong style={{ fontWeight: 700 }}>{children}</strong>
                    ),
                    blockquote: ({ children }) => (
                      <blockquote style={{ borderLeft: "3px solid var(--color-accent)", margin: "6px 0", paddingLeft: 10, opacity: 0.85 }}>
                        {children}
                      </blockquote>
                    ),
                  }}
                >
                  {stripNoteMarkers(msg.text)}
                </ReactMarkdown>
              )}
              {msg.role === "ai" && ((msg.citedDocs && msg.citedDocs.length > 0) || (msg.citedNotes && msg.citedNotes.length > 0)) && (
                <div
                  style={{
                    marginTop: 8,
                    paddingTop: 6,
                    borderTop: "1px dashed var(--color-border)",
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 4,
                  }}
                >
                  {msg.citedNotes?.map((n) => (
                    <button
                      key={`note-${n.slug}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedNoteSlug(n.slug);
                        setChatTab("notes");
                      }}
                      title={`업무 노트로 이동: ${n.title}`}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        padding: "2px 8px",
                        fontSize: 11,
                        borderRadius: 10,
                        background: "rgba(255,180,80,0.18)",
                        border: "1px solid rgba(255,180,80,0.5)",
                        color: "#ffc875",
                        cursor: "pointer",
                        maxWidth: 240,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontFamily: "inherit",
                      }}
                    >
                      <BookOpen size={11} />
                      노트 · {n.title}
                    </button>
                  ))}
                  {msg.citedDocs?.map((c) => (
                    <a
                      key={`doc-${c.id}`}
                      href={getDocumentDownloadUrl(c.id)}
                      download={c.filename}
                      title={`원본 다운로드: ${c.filename}`}
                      onClick={(e) => e.stopPropagation()}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        padding: "2px 8px",
                        fontSize: 11,
                        borderRadius: 10,
                        background: "rgba(100,140,220,0.18)",
                        border: "1px solid rgba(100,140,220,0.4)",
                        color: "#7aa8ff",
                        textDecoration: "none",
                        maxWidth: 220,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      <Download size={11} />
                      {c.filename}
                    </a>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* 첨부 자료 / 업로드 진행 칩 */}
      {(attachments.length > 0 || uploadingItems.length > 0) && (
        <div
          style={{
            padding: "6px 12px 0",
            display: "flex",
            flexWrap: "wrap",
            gap: 4,
            flexShrink: 0,
          }}
        >
          {attachments.map((a) => (
            <span
              key={a.id}
              title={`첨부: ${a.filename}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 4px 2px 8px",
                fontSize: 11,
                borderRadius: 10,
                background: "rgba(100,140,220,0.18)",
                border: "1px solid rgba(100,140,220,0.4)",
                color: "#7aa8ff",
                maxWidth: 220,
              }}
            >
              <Paperclip size={11} />
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 160,
                }}
              >
                {a.filename}
              </span>
              <button
                onClick={() => removeAttachment(a.id)}
                title="첨부 제거"
                style={{
                  background: "transparent",
                  border: "none",
                  color: "#7aa8ff",
                  cursor: "pointer",
                  padding: 0,
                  display: "flex",
                  alignItems: "center",
                  marginLeft: 2,
                }}
              >
                <X size={10} />
              </button>
            </span>
          ))}
          {uploadingItems.map((it) => (
            <span
              key={it.key}
              title={it.error ?? `업로드 중: ${it.filename} ${it.progress}%`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 8px",
                fontSize: 11,
                borderRadius: 10,
                background: it.error ? "rgba(231,76,60,0.18)" : "rgba(200,200,200,0.12)",
                border: `1px solid ${it.error ? "rgba(231,76,60,0.5)" : "rgba(200,200,200,0.3)"}`,
                color: it.error ? "#e74c3c" : "var(--color-text-muted)",
                maxWidth: 220,
              }}
            >
              <Paperclip size={11} />
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 140,
                }}
              >
                {it.filename}
              </span>
              <span style={{ fontSize: 10 }}>
                {it.error ? "실패" : `${it.progress}%`}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* 입력 영역 */}
      <div
        style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--color-border)",
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <button
          onClick={handleMicToggle}
          title={voiceActive ? "녹음 중단" : "마이크 누르고 말하기"}
          style={{
            background: voiceActive ? "rgba(231,76,60,0.2)" : "transparent",
            border: `1px solid ${voiceActive ? "#e74c3c" : "var(--color-border)"}`,
            borderRadius: 8,
            color: voiceActive ? "#e74c3c" : "var(--color-text-muted)",
            cursor: "pointer",
            padding: "6px 8px",
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          {voiceActive ? <Mic size={16} /> : <MicOff size={16} />}
        </button>

        {/* 파일 첨부 */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".txt,.md,.pdf,.docx,.pptx,.hwpx,.markdown"
          style={{ display: "none" }}
          onChange={(e) => void handleFilesPicked(e.target.files)}
        />
        <button
          onClick={handleAttachClick}
          title="파일 첨부 (RAG 자동 임베딩 + 노트 자동 정리)"
          style={{
            background: "transparent",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: "var(--color-text-muted)",
            cursor: "pointer",
            padding: "6px 8px",
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          <Paperclip size={16} />
        </button>

        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onClick={() => window.electronAPI?.restoreFocus()}
          placeholder="메시지를 입력하세요..."
          style={{
            flex: 1,
            background: "var(--color-bg)",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: "var(--color-text)",
            padding: "7px 12px",
            fontSize: 13,
            outline: "none",
          }}
        />

        <button
          onClick={handleSend}
          disabled={!input.trim() && attachments.length === 0}
          title="전송"
          style={{
            background: (input.trim() || attachments.length > 0)
              ? "var(--color-accent)"
              : "var(--color-border)",
            border: "none",
            borderRadius: 8,
            color: "#fff",
            cursor: (input.trim() || attachments.length > 0) ? "pointer" : "default",
            padding: "6px 10px",
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
            transition: "background 0.15s",
          }}
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// ChatPanel
// ────────────────────────────────────────────────────────────

export function ChatPanel({ charPosition, charSize }: ChatPanelProps): React.ReactElement {
  const setChatOpen = useStore((s) => s.setChatOpen);
  const chatTab = useStore((s) => s.chatTab);
  const setChatTab = useStore((s) => s.setChatTab);

  const panelStyle = calcPanelStyle(charPosition, charSize);

  return (
    <div
      id="chat-panel"
      onMouseEnter={() => window.electronAPI?.setIgnoreMouseEvents(false)}
      onMouseMove={() => window.electronAPI?.setIgnoreMouseEvents(false)}
      style={{
        ...panelStyle,
        zIndex: 999,
        background: "var(--color-panel)",
        borderRadius: 12,
        border: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
        // Override #root pointer-events:none so the panel is interactive
        pointerEvents: "auto",
      }}
    >
      {/* 네비게이션 탭 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          borderBottom: "1px solid var(--color-border)",
          flexShrink: 0,
          background: "var(--color-sidebar)",
          paddingRight: 8,
        }}
      >
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => setChatTab(id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              padding: "11px 14px",
              border: "none",
              borderBottom: chatTab === id
                ? "2px solid var(--color-accent)"
                : "2px solid transparent",
              background: "transparent",
              color: chatTab === id ? "var(--color-accent)" : "var(--color-text-muted)",
              cursor: "pointer",
              fontSize: 12,
              fontWeight: chatTab === id ? 600 : 400,
              transition: "color 0.15s",
              flexShrink: 0,
            }}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}

        {/* 닫기 버튼 — 우측 */}
        <button
          onClick={() => setChatOpen(false)}
          style={{
            marginLeft: "auto",
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-muted)",
            display: "flex",
            alignItems: "center",
            padding: "4px 6px",
            borderRadius: 4,
          }}
          title="닫기"
        >
          <X size={15} />
        </button>
      </div>

      {/* 컨텐츠 영역 */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {chatTab === "chat" && <ChatContent />}
        {chatTab === "calendar" && <CalendarView />}
        {chatTab === "documents" && <DocumentsView />}
        {/* MeetingView 항상 마운트 — 탭 전환 시 state 보존 (E-19 연장) */}
        <div style={{
          display: chatTab === "meeting" ? "flex" : "none",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
          minHeight: 0,
        }}>
          <MeetingView />
        </div>
        {/* NotesView도 항상 마운트 — 편집 buffer·선택 상태 보존 */}
        <div style={{
          display: chatTab === "notes" ? "flex" : "none",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
          minHeight: 0,
        }}>
          <NotesView />
        </div>
        {chatTab === "settings" && <SettingsView />}
      </div>
    </div>
  );
}
