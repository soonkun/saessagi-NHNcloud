import { useEffect, useRef, useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  FileText,
  Image as ImageIcon,
  Mic,
  Menu,
  X,
  Plus,
  RotateCcw,
  ExternalLink,
  Paperclip,
  Network,
} from "lucide-react";
import { useStore } from "../store";
import { send } from "../services/websocket";
import { startNewHistoryIfSafe } from "../services/history";
import { invalidateDocsCache } from "../services/websocket";
import { openDocument, uploadDocument } from "../services/api";
import {
  ensureDocTitles,
  getCachedTitle,
  shortenTitle,
  subscribeDocTitles,
  titleFromFilename,
} from "../services/docTitles";
import type { MessageAttachment, MessageImage } from "../types";

// CR-64: 인용 마커를 **본문 제자리에 칩으로** 렌더한다.
// `[[doc:<id>]]` → 마크다운 링크로 바꿔 `a` 렌더러가 칩으로 그린다.
// 예전에는 본문에서 전부 지우고 답변 끝에 몰아 붙였는데, 그러면 어느 문장이 어느
// 자료 근거인지 알 수 없고 재진입 시 칩이 통째로 사라졌다(런타임 상태였다).
// 이제 마커가 **본문 텍스트에 남아** 히스토리에서 복원해도 그대로 살아난다.
const DOC_LINK_SCHEME = "saessagi-doc:";
const NOTE_LINK_SCHEME = "saessagi-note:";

/**
 * 마커를 마크다운 링크로. **URL에는 번호만 넣는다.**
 *
 * doc_id를 URL에 넣었더니 `12. (고정) 드론…` 처럼 **괄호가 든 파일명**에서 마크다운
 * 링크 파서가 깨져 `[근거](saessagi-doc:12.%20(%EA%B3%A0…` 가 본문에 날것으로 떴다.
 * percent-encoding으로는 못 막는다 — `encodeURIComponent`는 `()`를 그대로 둔다.
 * 번호는 숫자뿐이라 어떤 파일명이 와도 깨지지 않는다.
 */
export function markersToLinks(text: string, docIds: string[], noteIds: string[] = []): string {
  return (text || "")
    .replace(/\[\[doc:([^[\]]+)\]\]/g, (_m, id: string) => {
      const i = docIds.indexOf(String(id).trim());
      return i < 0 ? "" : ` [근거](${DOC_LINK_SCHEME}${i}) `;
    })
    .replace(/\[\[note:([^[\]]+)\]\]/g, (_m, slug: string) => {
      const i = noteIds.indexOf(String(slug).trim());
      return i < 0 ? "" : ` [노트](${NOTE_LINK_SCHEME}${i}) `;
    });
}

/**
 * 인용 칩을 **문단·섹션 끝으로 모은다** (사용자 요청).
 *
 * 모델이 문장마다 마커를 달면 본문이 칩으로 끊긴다 — "그냥 '2.' 하이라키 마지막에
 * 저 세개를 넣으면 되잖아. 문장마다 넣는건 과해."
 * 섹션(번호 항목·제목)이 새로 시작하는 자리에서 그때까지 모인 마커를 한 줄로 낸다.
 */
export function groupMarkersByBlock(text: string): string {
  const MARKER = /\[\[(?:doc|note):[^[\]]+\]\]/g;
  // **상위 하이라키만** 경계로 본다 (사용자 요청). 예전에는 빈 줄·불릿·①까지 경계로
  // 삼아 칩이 다시 문장 단위로 흩어졌다. 지금은 제목(`#`)과 번호 항목(`1.`)만 본다.
  const isTopSection = (ln: string): boolean => /^\s*(?:#{1,6}\s|\d+[.)]\s)/.test(ln);
  // 표 줄. 마커가 표 안으로 들어가면 셀에 칩이 박히고 "관련 근거" 열이 생겨 표가 깨진다.
  const isTableRow = (ln: string): boolean => /^\s*\|/.test(ln);

  const lines = (text || "").split("\n");
  const out: string[] = [];
  let buf: string[] = [];

  const flush = (): void => {
    if (!buf.length) return;
    // 표 바로 뒤라면 빈 줄을 하나 두어야 마크다운이 표에 붙이지 않는다.
    if (out.length && isTableRow(out[out.length - 1])) out.push("");
    out.push(buf.join(" "));
    buf = [];
  };

  for (const line of lines) {
    const found = line.match(MARKER) ?? [];
    const stripped = line.replace(MARKER, "").replace(/[ \t]{2,}/g, " ").trimEnd();
    // 표 안에서는 절대 내보내지 않는다 — 표가 끝난 뒤로 미룬다.
    if (isTopSection(stripped) && !isTableRow(stripped)) flush();
    out.push(stripped);
    for (const m of found) if (!buf.includes(m)) buf.push(m);
  }
  flush();
  return out.join("\n");
}

/** 본문에 살아 있는 doc 마커의 id 목록 (제목 조회·프리페치용). */
export function docIdsInText(text: string): string[] {
  const out: string[] = [];
  for (const m of (text || "").matchAll(/\[\[doc:([^[\]]+)\]\]/g)) {
    const id = m[1].trim();
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

/** 본문 속 인용 칩. 제목을 7낱말로 줄여 보여주고, 누르면 원문을 연다. */
function CiteChip({ docId }: { docId: string }): React.ReactElement {
  const [, force] = useState(0);
  useEffect(() => {
    void ensureDocTitles([docId]);
    return subscribeDocTitles(() => force((v) => v + 1));
  }, [docId]);

  const full = getCachedTitle(docId) ?? titleFromFilename(docId);
  const label = shortenTitle(full) || "근거";
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        void openDocument(docId, full);
      }}
      title={`원본 열기: ${full}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        margin: "0 2px",
        padding: "0 6px",
        fontSize: "var(--fs-11)",
        lineHeight: 1.7,
        borderRadius: 9,
        background: "var(--chip-doc-bg)",
        border: "1px solid var(--chip-doc-border)",
        color: "var(--chip-doc-text)",
        cursor: "pointer",
        fontFamily: "inherit",
        verticalAlign: "baseline",
        maxWidth: "100%",
      }}
    >
      <ExternalLink size={10} style={{ flexShrink: 0 }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
    </button>
  );
}

function stripNoteMarkers(text: string): string {
  return text
    // [[note:slug]] / [[doc:id]] (정상 이중괄호, 닫힘 0~2개 깨진 것 포함)
    .replace(/\[\[(?:note|doc):[^[\]]*\]{0,2}/g, "")
    // 접두사를 빼먹은 마커 `[[TRKO….pdf_abc]]` (E-103). 백엔드가 살릴 수 있으면
    // 살리지만, 못 살린 것이 화면에 날것으로 뜨는 것을 여기서 막는다.
    .replace(/\[\[[^[\]]*\.(?:pdf|hwpx?|docx?|pptx?|xlsx?|txt|md)[^[\]]*\]{0,2}/gi, "")
    // [doc:id] / [note:slug] (단일괄호 — 8B 모델이 자주 이렇게 잘못 출력)
    .replace(/\[(?:note|doc):[^[\]]*\]/g, "")
    // 모델이 임의로 끼워넣는 HTML 태그(<span style=...> 등). react-markdown은
    // raw HTML을 렌더하지 않아 그대로 찌꺼기로 보인다 (E-51).
    .replace(/<\/?[a-zA-Z][^>]*>/g, "")
    // 가로 공백(스페이스·탭)만 정리한다. \s를 쓰면 줄바꿈(\n)까지 뭉개져
    // 마크다운 블록(제목·표·목록)이 한 줄로 붙어 렌더링되지 않는다 (E-50).
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}
import { startVoice, stopVoice } from "../services/voice";
import { CalendarView } from "./CalendarView";
import { DeepResearchView } from "./DeepResearchView";
import { DocumentsView } from "./DocumentsView";
import { MeetingView } from "./MeetingView";
import { NotesView } from "./NotesView";
import { SettingsView } from "./SettingsView";
import { lazy, Suspense } from "react";

// M_19: 그래프 탭 — force-graph 번들은 탭 진입 시에만 로드
const GraphRagView = lazy(() => import("./GraphRagView"));
import type { Position } from "../types";
import { CHAT_TABS } from "../chatTabs";
import { HistoryList } from "./HistoryList";

// 그래프 탭 추가로 좌우 여유 확보 (사용자 요청 2026-07-16: 580 → 700)
const PANEL_W = 700;
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

const TABS = CHAT_TABS.map(({ id, petLabel, Icon }) => ({ id, label: petLabel, Icon }));

// CR-48 입력창 자동 높이 — ChatGPT처럼 줄바꿈되며 늘어나다 일정 줄 수부터 스크롤.
export const LINE_HEIGHT = 22;
export const MAX_ROWS = 7;
export const TEXTAREA_PAD = 16; // 위아래 padding 8+8

/** 내용 높이(scrollHeight)를 실제로 적용할 높이로 바꾼다 — MAX_ROWS를 넘으면 스크롤. */
export function composerHeight(scrollHeight: number): number {
  return Math.min(scrollHeight, MAX_ROWS * LINE_HEIGHT + TEXTAREA_PAD);
}

// ────────────────────────────────────────────────────────────
// Chat content
// ────────────────────────────────────────────────────────────

export function ChatContent({
  emptyHero,
  leading,
}: {
  emptyHero?: React.ReactNode;
  /** 상태줄 맨 앞에 넣을 요소 (좁은 화면의 메뉴 버튼). CR-55에서 타이틀 바를 없애며 추가. */
  leading?: React.ReactNode;
} = {}): React.ReactElement {
  const messages = useStore((s) => s.messages);
  const aiStatus = useStore((s) => s.aiStatus);
  const llmInfo = useStore((s) => s.llmInfo);
  const agentStatus = useStore((s) => s.agentStatus);
  const addMessage = useStore((s) => s.addMessage);
  const requestGraphEvidence = useStore((s) => s.requestGraphEvidence);

  const [input, setInput] = useState("");
  const [voiceActive, setVoiceActive] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // CR-48: +메뉴에서 이미지/파일을 따로 고를 수 있게 선택기를 나눠 둔다.
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const docInputRef = useRef<HTMLInputElement | null>(null);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);

  // 첨부 자료 (업로드 완료된 doc 목록 + 업로드 진행 중 항목)
  const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
  const [uploadingItems, setUploadingItems] = useState<
    { key: string; filename: string; progress: number; error?: string }[]
  >([]);
  // 동시 업로드 카운터 — 펫 캐릭터 emotion("uploading" 영상) 전환/복귀 제어.
  // 마지막 업로드가 끝날 때만 neutral로 되돌려, 연속 첨부 중 깜빡임을 막는다.
  const activeUploadsRef = useRef(0);
  // 첨부 이미지 (clipboard paste 또는 향후 드래그) — 비전 LLM에 직접 전달
  const [pendingImages, setPendingImages] = useState<MessageImage[]>([]);

  function handleNewHistory(): void {
    // 백엔드 응답(new-history-created)으로 clearMessages가 호출된다.
    // 생성 중에는 무시된다 — 바꾸면 도착할 답변이 새 히스토리로 흘러간다 (E-75).
    startNewHistoryIfSafe();
  }


  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, agentStatus]);

  useEffect(() => {
    // 터치 기기에서는 자동 포커스하지 않는다.
    // iOS Safari는 포커스된 입력창을 보이게 하려고 페이지를 아래로 밀어 올리는데,
    // body가 overflow:hidden이라 되돌릴 수가 없다 — 화면 맨 위(타이틀 바·상태줄)가
    // 잘린 채 고정되어 햄버거 메뉴와 새 대화 버튼에 아예 손이 닿지 않았다.
    // 게다가 열자마자 키보드가 올라와 화면 절반을 덮는다.
    const isTouch =
      typeof window !== "undefined" &&
      (window.matchMedia?.("(pointer: coarse)").matches || "ontouchstart" in window);
    if (isTouch) return;
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, []);

  function handleSend(): void {
    const text = input.trim();
    if (!text && attachments.length === 0 && pendingImages.length === 0) return;
    // 화면 표시는 사용자 원문 + attachments + images 미리보기
    addMessage({
      role: "human",
      text: text || (pendingImages.length > 0
        ? "(첨부 이미지를 보고 업무 노트로 정리해 주세요)"
        : "(첨부 자료를 정리해 주세요)"),
      attachments: attachments.length > 0 ? attachments : undefined,
      images: pendingImages.length > 0 ? pendingImages : undefined,
    });
    // 백엔드에는 prefix로 doc_id 메타 자동 삽입 — LLM이 related_docs에 활용
    let payload = text || (pendingImages.length > 0
      ? "(첨부 이미지 안에 보이는 모든 텍스트·표·수치·날짜·담당자를 빠짐없이 그대로 읽어서, 그 내용을 summary에 정리하고 save_knowledge_note를 호출해줘. 화면 상황을 추측하지 말고 실제로 보이는 글자를 근거로 작성해.)"
      : "(이 첨부 자료를 검토해서 업무 노트로 정리해줘.)");
    if (attachments.length > 0) {
      const meta = attachments
        .map((a) => `${a.filename} (doc_id: ${a.id})`)
        .join("; ");
      payload = `[첨부 자료: ${meta}]\n${payload}`;
    }
    if (pendingImages.length > 0) {
      const imgMeta = pendingImages.map((i) => i.filename).join(", ");
      payload = `[첨부 이미지: ${imgMeta}]\n${payload}`;
    }
    send({
      type: "text-input",
      text: payload,
      images: pendingImages.length > 0
        ? pendingImages.map((i) => ({
            source: "clipboard" as const,
            data: i.dataUrl,
            mime_type:
              (i.dataUrl.match(/^data:([^;]+);/)?.[1]) ?? "image/png",
          }))
        : undefined,
    });
    setInput("");
    setAttachments([]);
    setPendingImages([]);
  }

  // 내용에 맞춰 높이를 다시 잰다. 먼저 auto로 되돌려야 글을 지웠을 때 줄어든다
  // (scrollHeight는 현재 높이보다 작아지지 않으므로 한 번 커지면 그대로 남는다).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${composerHeight(el.scrollHeight)}px`;
  }, [input]);

  // 보낼 내용이 있는가 — 전송 버튼 활성 조건이 여러 곳에 흩어지면 어긋난다.
  const canSend =
    input.trim().length > 0 || attachments.length > 0 || pendingImages.length > 0;

  // +메뉴는 바깥을 누르거나 Esc로 닫힌다. 메뉴가 열린 채 남으면 입력창을 가린다.
  useEffect(() => {
    if (!attachMenuOpen) return;
    const onDown = (e: MouseEvent): void => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-testid="attach-menu"],[data-testid="attach-button"]')) return;
      setAttachMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setAttachMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [attachMenuOpen]);

  async function handleFilesPicked(files: FileList | null): Promise<void> {
    if (!files || files.length === 0) return;
    window.electronAPI?.restoreFocus();
    for (const file of Array.from(files)) {
      if (file.type.startsWith("image/")) {
        const dataUrl = await fileToDataUrl(file);
        setPendingImages((prev) => [...prev, { dataUrl, filename: file.name || "image.png" }]);
      } else {
        await uploadOneFile(file);
      }
    }
    // 같은 파일 재선택 가능하도록 reset.
    // 선택기가 셋이므로 전부 비운다 — 값이 남은 쪽은 같은 파일을 다시 골라도
    // change 이벤트가 안 나서 아무 일도 일어나지 않는다.
    for (const ref of [imageInputRef, docInputRef]) {
      if (ref.current) ref.current.value = "";
    }
  }

  function removeAttachment(id: string): void {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }

  function removeImage(idx: number): void {
    setPendingImages((prev) => prev.filter((_, i) => i !== idx));
  }

  // 클립보드 paste — 이미지/파일 분기
  async function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>): Promise<void> {
    const cd = e.clipboardData;
    if (!cd) return;
    const items = Array.from(cd.items);
    const fileItems = items.filter((it) => it.kind === "file");
    if (fileItems.length === 0) return; // 일반 텍스트 paste는 기본 동작
    e.preventDefault();
    window.electronAPI?.restoreFocus();

    for (const item of fileItems) {
      const file = item.getAsFile();
      if (!file) continue;
      if (item.type.startsWith("image/")) {
        // 이미지: data URL로 변환해 비전 LLM에 직접 전달
        const dataUrl = await fileToDataUrl(file);
        const filename = file.name && file.name !== "image.png"
          ? file.name
          : `screenshot_${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
        setPendingImages((prev) => [...prev, { dataUrl, filename }]);
      } else {
        // 일반 파일: 기존 업로드 흐름 (RAG 저장)
        await uploadOneFile(file);
      }
    }
  }

  function fileToDataUrl(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  async function uploadOneFile(file: File): Promise<void> {
    const key = `${Date.now()}_${file.name}`;
    setUploadingItems((prev) => [...prev, { key, filename: file.name, progress: 0 }]);
    // 책장 포털에 책을 꽂는 새싹이 영상 — RAG 등록 동안 펫 캐릭터 자리에 재생
    if (activeUploadsRef.current === 0) {
      useStore.getState().setEmotion("uploading");
    }
    activeUploadsRef.current += 1;
    try {
      // 채팅에서 첨부한 파일은 "업무노트" 폴더로 자동 분류 (백엔드가 폴더 없으면 생성)
      const doc = await uploadDocument(file, null, (pct) => {
        setUploadingItems((prev) =>
          prev.map((it) => (it.key === key ? { ...it, progress: pct } : it))
        );
      }, { folderName: "업무노트" });
      setAttachments((prev) => [...prev, { id: doc.id, filename: doc.filename }]);
      setUploadingItems((prev) => prev.filter((it) => it.key !== key));
      invalidateDocsCache();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "업로드 실패";
      setUploadingItems((prev) =>
        prev.map((it) => (it.key === key ? { ...it, error: msg, progress: -1 } : it))
      );
      setTimeout(() => {
        setUploadingItems((prev) => prev.filter((it) => it.key !== key));
      }, 5000);
    } finally {
      activeUploadsRef.current = Math.max(0, activeUploadsRef.current - 1);
      // 마지막 업로드가 끝났고 아직 uploading 상태면 평상시(neutral)로 복귀
      if (activeUploadsRef.current === 0) {
        const { emotion: cur, setEmotion } = useStore.getState();
        if (cur === "uploading") setEmotion("neutral");
      }
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
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
    <div style={{ display: "flex", flexDirection: "column", height: "100%", position: "relative" }}>
      {/* 상태 표시 줄 */}
      <div
        style={{
          padding: "2px 10px",
          // 줄 높이를 내용에 맡기면 메뉴 버튼(40px)이 있는 좁은 화면에서만 넉넉하고,
          // 버튼이 없는 넓은 화면에서는 20px로 쪼그라들어 글자가 위쪽 테두리에 붙는다.
          // 두 경우의 높이를 같게 고정한다 (CR-55 후속, 사용자 지적).
          minHeight: 44,
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexShrink: 0,
        }}
      >
        {leading}
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
        <span style={{ color: "var(--color-text-muted)", fontSize: "var(--fs-12)" }}>
          {STATUS_LABEL[aiStatus] ?? ""}
        </span>
        {/* CR-55: 타이틀 바에 있던 사용 중인 모델명을 여기로 옮겼다. */}
        {llmInfo && (
          <span
            title={`현재 LLM: ${llmInfo.provider === "openai" ? "OpenAI" : "Ollama"} / ${llmInfo.model}`}
            style={{
              fontSize: "var(--fs-12)",
              color: llmInfo.provider === "openai" ? "#10a37f" : "#7aa8ff",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              minWidth: 0,
            }}
          >
            · {llmInfo.model}
          </span>
        )}
        {/* LLM 표시는 상단 바(펫: 패널 헤더, 데스크톱: 타이틀 바)로 이동 — 상태줄에선 제거 */}
        {voiceActive && (
          <span
            className="status-blink"
            style={{ fontSize: "var(--fs-12)", color: "#e74c3c", marginLeft: "auto" }}
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
                fontSize: "var(--fs-13)",
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
                fontSize: "var(--fs-13)",
                lineHeight: 1.5,
                wordBreak: "break-word",
              }}
            >
              {msg.role === "human" ? (
                <div>
                  {msg.images && msg.images.length > 0 && (
                    <div style={{ marginBottom: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {msg.images.map((img, i) => (
                        <img
                          key={`img-${i}`}
                          src={img.dataUrl}
                          alt={img.filename}
                          title={img.filename}
                          style={{
                            maxWidth: 220,
                            maxHeight: 220,
                            objectFit: "contain",
                            borderRadius: 8,
                            border: "1px solid rgba(255,255,255,0.15)",
                          }}
                        />
                      ))}
                    </div>
                  )}
                  <span style={{ whiteSpace: "pre-wrap" }}>{msg.text}</span>
                  {msg.attachments && msg.attachments.length > 0 && (
                    <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {msg.attachments.map((a) => (
                        <button
                          key={a.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            openDocument(a.id, a.filename);
                          }}
                          title={`첨부 열기: ${a.filename}`}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            padding: "2px 8px",
                            fontSize: "var(--fs-11)",
                            borderRadius: 10,
                            background: "rgba(255,255,255,0.12)",
                            border: "1px solid rgba(255,255,255,0.2)",
                            color: "var(--color-text)",
                            cursor: "pointer",
                            fontFamily: "inherit",
                            maxWidth: 220,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          <Paperclip size={11} />
                          {a.filename}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  // react-markdown의 기본 URL 정제기는 http(s)·mailto 외의 스킴을
                  // **지운다.** 우리 인용 링크(`saessagi-doc:`)도 지워져 href가 비고,
                  // `a` 렌더러가 칩 대신 맨 링크를 그렸다(실측: 백엔드는 8개를 보냈는데
                  // 화면 칩 0개). 우리 스킴만 통과시키고 나머지는 기본 정제를 유지한다.
                  urlTransform={(url) =>
                    url.startsWith(DOC_LINK_SCHEME) || url.startsWith(NOTE_LINK_SCHEME)
                      ? url
                      : defaultUrlTransform(url)
                  }
                  components={{
                    p: ({ children }) => (
                      <p style={{ margin: "0 0 6px", whiteSpace: "pre-wrap" }}>{children}</p>
                    ),
                    table: ({ children }) => (
                      <div style={{ overflowX: "auto", margin: "6px 0" }}>
                        <table style={{ borderCollapse: "collapse", fontSize: "var(--fs-12)", width: "100%" }}>
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
                      <pre style={{ background: "rgba(0,0,0,0.3)", borderRadius: 6, padding: "8px 10px", overflowX: "auto", fontSize: "var(--fs-12)", margin: "6px 0", fontFamily: "monospace" }}>
                        {children}
                      </pre>
                    ),
                    code: ({ children }) => (
                      <code style={{ background: "rgba(255,255,255,0.08)", borderRadius: 3, padding: "1px 4px", fontSize: "var(--fs-12)", fontFamily: "monospace" }}>
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
                    // CR-64: 인용 마커에서 만든 링크를 칩으로 그린다.
                    a: ({ href, children }) => {
                      const h = String(href ?? "");
                      if (h.startsWith(DOC_LINK_SCHEME)) {
                        const id = docIdsInText(msg.text)[Number(h.slice(DOC_LINK_SCHEME.length))];
                        return id ? <CiteChip docId={id} /> : <></>;
                      }
                      if (h.startsWith(NOTE_LINK_SCHEME)) {
                        return (
                          <span style={{ fontSize: "var(--fs-11)", color: "var(--chip-note-text)" }}>
                            📝 노트
                          </span>
                        );
                      }
                      return <a href={h} target="_blank" rel="noreferrer">{children}</a>;
                    },
                  }}
                >
                  {/* 순서 주의: 마커를 **먼저** 링크로 바꾼 뒤 남은 찌꺼기를 지운다.
                      반대로 하면 stripNoteMarkers가 마커를 먼저 지워 칩이 사라진다. */}
                  {stripNoteMarkers(
                    markersToLinks(groupMarkersByBlock(msg.text), docIdsInText(msg.text))
                  )}
                </ReactMarkdown>
              )}
              {/* CR-64: 문서 칩 무더기를 없앴다 — 인용은 본문 제자리 칩으로 보여준다.
                  맨 아래는 근거 그래프 하나만 둔다(사용자 요청). */}
              {/* 근거가 **실제로 있는 답변에만** 붙인다. `text.length > 0`으로 두었더니
                  "자료를 찾아볼게요!" 같은 안내 말풍선에도 붙어 엉뚱한 자리에 떴다.
                  인용 마커가 본문에 있다는 것이 곧 "이 답변에 근거가 있다"는 신호다. */}
              {msg.role === "ai" && docIdsInText(msg.text).length > 0 && (
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
                  {/* M_19: 이 답변의 근거 서브그래프를 그래프 탭에서 하이라이트 */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      requestGraphEvidence();
                    }}
                    title="이 답변이 어떤 개체·문서를 근거로 나왔는지 그래프로 보기"
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      padding: "2px 8px",
                      fontSize: "var(--fs-11)",
                      borderRadius: 10,
                      background: "transparent",
                      border: "1px dashed var(--color-border)",
                      color: "var(--color-text-muted)",
                      cursor: "pointer",
                      fontFamily: "inherit",
                    }}
                  >
                    <Network size={11} />
                    근거 그래프
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
        {/* 진행 상태 말풍선 — 백엔드가 보내는 단계별 상태 ("문서를 찾는 중…" 등) */}
        {agentStatus && (
          <div className="msg-enter" style={{ display: "flex", justifyContent: "flex-start" }}>
            <div
              style={{
                maxWidth: "80%",
                padding: "8px 12px",
                borderRadius: "12px 12px 12px 4px",
                background: "var(--color-msg-ai)",
                border: "1px dashed var(--color-border)",
                fontSize: "var(--fs-13)",
                lineHeight: 1.5,
                color: "var(--color-text-muted)",
                fontStyle: "italic",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <span className="status-blink" style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--color-accent)", display: "inline-block", flexShrink: 0 }} />
              {agentStatus}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 첨부 자료 / 업로드 진행 칩 */}
      {(attachments.length > 0 || uploadingItems.length > 0 || pendingImages.length > 0) && (
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
                fontSize: "var(--fs-11)",
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
                fontSize: "var(--fs-11)",
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
              <span style={{ fontSize: "var(--fs-10)" }}>
                {it.error ? "실패" : `${it.progress}%`}
              </span>
            </span>
          ))}
          {pendingImages.map((img, idx) => (
            <span
              key={`img-${idx}`}
              title={`이미지 첨부: ${img.filename} (비전 LLM에 직접 전달)`}
              style={{
                position: "relative",
                display: "inline-block",
                border: "1px solid rgba(255,180,80,0.45)",
                borderRadius: 8,
                padding: 2,
                background: "rgba(255,180,80,0.08)",
              }}
            >
              <img
                src={img.dataUrl}
                alt={img.filename}
                style={{
                  display: "block",
                  width: 56,
                  height: 56,
                  objectFit: "cover",
                  borderRadius: 6,
                }}
              />
              <button
                onClick={() => removeImage(idx)}
                title="이미지 제거"
                style={{
                  position: "absolute",
                  top: -6,
                  right: -6,
                  width: 18,
                  height: 18,
                  borderRadius: "50%",
                  background: "rgba(0,0,0,0.7)",
                  color: "#fff",
                  border: "1px solid rgba(255,255,255,0.2)",
                  cursor: "pointer",
                  fontSize: "var(--fs-11)",
                  lineHeight: 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* 입력 영역 — Gemini 스타일 알약 한 덩어리 (CR-48).
          +(첨부) · 입력 · 마이크 · 전송 순서. 스트리밍(실시간 대화)은 지원하지 않으므로
          Gemini에 있는 라이브 버튼은 두지 않는다. */}
      <div
        style={{
          // 아래쪽을 넉넉히 띄운다. 바닥에 붙어 있으면 눌리기 불편하고, 모바일에서는
          // 브라우저 툴바·홈 인디케이터와 겹친다(env(safe-area-inset-bottom)).
          padding: "10px 12px calc(22px + env(safe-area-inset-bottom, 0px))",
          display: "flex",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {/* 숨은 파일 선택기 — +메뉴의 '이미지'와 '파일'이 각각 연다.
            accept를 나눠 두면 사진 앱/문서 폴더가 바로 열려 고르기 쉽다.
            고른 뒤 처리는 기존 handleFilesPicked 하나로 합류한다(이미지는 비전 입력,
            문서는 RAG 업로드로 자동 분기). */}
        <input
          ref={imageInputRef}
          type="file"
          multiple
          accept="image/*"
          style={{ display: "none" }}
          onChange={(e) => void handleFilesPicked(e.target.files)}
        />
        <input
          ref={docInputRef}
          type="file"
          multiple
          accept=".txt,.md,.pdf,.docx,.pptx,.hwpx,.markdown"
          style={{ display: "none" }}
          onChange={(e) => void handleFilesPicked(e.target.files)}
        />

        <div
          data-testid="composer"
          style={{
            position: "relative",
            width: "100%",
            maxWidth: 860,
            display: "flex",
            // 입력창이 여러 줄로 늘어나면 버튼은 아래쪽에 붙어 있어야 자연스럽다.
            alignItems: "flex-end",
            gap: 6,
            background: "var(--color-panel)",
            border: "1px solid var(--color-border)",
            // 한 줄일 때 딱 알약이 되는 값(높이 52 ÷ 2). 여러 줄로 커지면 자연스럽게
            // 둥근 사각형이 된다 — 999로 두면 길어졌을 때 양끝이 기괴하게 부푼다.
            borderRadius: 26,
            padding: "6px 8px 6px 6px",
            boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
          }}
        >
          {/* + 첨부 메뉴 */}
          <div style={{ position: "relative", flexShrink: 0 }}>
            {attachMenuOpen && (
              <div
                data-testid="attach-menu"
                role="menu"
                style={{
                  position: "absolute",
                  bottom: "calc(100% + 10px)",
                  left: 0,
                  minWidth: 210,
                  background: "var(--color-panel)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 12,
                  boxShadow: "0 8px 24px rgba(0,0,0,0.16)",
                  padding: 6,
                  zIndex: 30,
                }}
              >
                <AttachMenuItem
                  icon={<ImageIcon size={16} />}
                  label="이미지"
                  hint="화면 캡처·사진"
                  onClick={() => {
                    setAttachMenuOpen(false);
                    imageInputRef.current?.click();
                  }}
                />
                <AttachMenuItem
                  icon={<FileText size={16} />}
                  label="파일"
                  hint="문서 등록 후 답변에 활용"
                  onClick={() => {
                    setAttachMenuOpen(false);
                    docInputRef.current?.click();
                  }}
                />
              </div>
            )}
            <button
              data-testid="attach-button"
              onClick={() => setAttachMenuOpen((o) => !o)}
              aria-haspopup="menu"
              aria-expanded={attachMenuOpen}
              title="첨부"
              style={{
                ...roundBtnStyle,
                background: attachMenuOpen ? "var(--color-bg)" : "transparent",
                color: "var(--color-text-muted)",
                transform: attachMenuOpen ? "rotate(45deg)" : "none",
                transition: "transform 0.15s, background 0.15s",
              }}
            >
              <Plus size={20} />
            </button>
          </div>

          <textarea
            ref={inputRef}
            value={input}
            rows={1}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={(e) => void handlePaste(e)}
            onClick={() => window.electronAPI?.restoreFocus()}
            placeholder="새싹이에게 물어보기"
            style={{
              flex: 1,
              minWidth: 0,
              background: "transparent",
              border: "none",
              color: "var(--color-text)",
              padding: "8px 4px",
              fontSize: "var(--fs-14)",
              lineHeight: `${LINE_HEIGHT}px`,
              outline: "none",
              // 긴 글은 줄바꿈되며 MAX_ROWS줄까지 늘어나고 그 뒤에는 스크롤한다.
              // 높이는 아래 useEffect가 내용에 맞춰 계산한다(rows만으로는 줄지 않는다).
              resize: "none",
              overflowY: "auto",
              maxHeight: MAX_ROWS * LINE_HEIGHT + TEXTAREA_PAD,
              fontFamily: "inherit",
            }}
          />

          <button
            onClick={handleMicToggle}
            title={voiceActive ? "녹음 중단" : "음성으로 입력"}
            style={{
              ...roundBtnStyle,
              background: voiceActive ? "rgba(231,76,60,0.14)" : "transparent",
              color: voiceActive ? "#e74c3c" : "var(--color-text-muted)",
            }}
          >
            {/* 평소에도 그냥 마이크를 보여준다. MicOff(빗금)를 기본으로 두면
                "음성 입력을 쓸 수 없다"는 뜻으로 읽혀 아무도 누르지 않는다. */}
            <Mic size={18} />
          </button>

          <button
            onClick={handleSend}
            disabled={!canSend}
            title="전송"
            style={{
              ...roundBtnStyle,
              background: canSend ? "var(--color-accent)" : "var(--color-border)",
              color: "#fff",
              cursor: canSend ? "pointer" : "default",
              transition: "background 0.15s",
            }}
          >
            <ArrowUp size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}

/** 알약 안의 동그란 아이콘 버튼 — 크기를 한 곳에서 맞춘다. */
const roundBtnStyle: React.CSSProperties = {
  width: 36,
  height: 36,
  borderRadius: "50%",
  border: "none",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  cursor: "pointer",
  flexShrink: 0,
  padding: 0,
};

function AttachMenuItem({
  icon,
  label,
  hint,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  hint: string;
  onClick: () => void;
}): React.ReactElement {
  const [hover, setHover] = useState(false);
  return (
    <button
      role="menuitem"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        width: "100%",
        background: hover ? "var(--color-bg)" : "transparent",
        border: "none",
        borderRadius: 8,
        padding: "9px 10px",
        cursor: "pointer",
        color: "var(--color-text)",
        textAlign: "left",
      }}
    >
      <span style={{ color: "var(--color-text-muted)", display: "flex" }}>{icon}</span>
      <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <span style={{ fontSize: "var(--fs-13)", fontWeight: 600 }}>{label}</span>
        <span
          style={{
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            whiteSpace: "nowrap",
          }}
        >
          {hint}
        </span>
      </span>
    </button>
  );
}

// ────────────────────────────────────────────────────────────
// ChatPanel
// ────────────────────────────────────────────────────────────

export function ChatPanel({ charPosition, charSize }: ChatPanelProps): React.ReactElement {
  const setChatOpen = useStore((s) => s.setChatOpen);
  const chatTab = useStore((s) => s.chatTab);
  const setChatTab = useStore((s) => s.setChatTab);
  // CR-23: 펫 모드 좌측 메뉴·대화방 드로어
  const [navOpen, setNavOpen] = useState(false);
  const llmInfoTop = useStore((s) => s.llmInfo);

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
      {/* CR-23: 좌측 대화방 드로어 (햄버거) */}
      {navOpen && (
        <>
          <div
            onClick={() => setNavOpen(false)}
            style={{
              position: "absolute",
              inset: 0,
              background: "rgba(0,0,0,0.35)",
              zIndex: 50,
            }}
          />
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              bottom: 0,
              width: 240,
              zIndex: 51,
              background: "var(--color-panel)",
              borderRight: "1px solid var(--color-border)",
              display: "flex",
              flexDirection: "column",
              boxShadow: "4px 0 16px rgba(0,0,0,0.3)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 12px 6px",
                flexShrink: 0,
              }}
            >
              <span style={{ fontSize: "var(--fs-13)", fontWeight: 700 }}>새싹이</span>
              <button
                onClick={() => setNavOpen(false)}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", display: "flex", padding: 2 }}
              >
                <X size={14} />
              </button>
            </div>

            {/* 탭 메뉴 (상단 탭 바 → 드로어로 이동) */}
            <div style={{ padding: "2px 6px 8px", flexShrink: 0 }}>
              {TABS.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  onClick={() => {
                    // "새 대화" 탭: 대화가 있고 생성 중이 아닐 때만 (E-75)
                    if (id === "chat") startNewHistoryIfSafe();
                    setChatTab(id);
                    setNavOpen(false);
                  }}
                  style={{
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    gap: 9,
                    padding: "8px 10px",
                    marginBottom: 1,
                    background: chatTab === id ? "rgba(100,140,220,0.12)" : "transparent",
                    border: "none",
                    borderRadius: 7,
                    color: chatTab === id ? "var(--color-accent)" : "var(--color-text)",
                    cursor: "pointer",
                    fontSize: "var(--fs-13)",
                    fontWeight: chatTab === id ? 600 : 400,
                    textAlign: "left",
                    fontFamily: "inherit",
                  }}
                >
                  <Icon size={14} style={{ flexShrink: 0 }} />
                  {label}
                </button>
              ))}
            </div>

            <div
              style={{
                fontSize: "var(--fs-11)",
                fontWeight: 700,
                color: "var(--color-text-muted)",
                padding: "8px 14px 0",
                borderTop: "1px solid var(--color-border)",
                letterSpacing: "0.06em",
                flexShrink: 0,
              }}
            >
              대화
            </div>
            <HistoryList onSelect={() => setNavOpen(false)} />
          </div>
        </>
      )}

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
        <button
          onClick={() => setNavOpen(true)}
          title="메뉴·대화 목록"
          style={{
            display: "flex",
            alignItems: "center",
            padding: "11px 10px 11px 12px",
            border: "none",
            background: "none",
            cursor: "pointer",
            color: "var(--color-text-muted)",
            flexShrink: 0,
          }}
        >
          <Menu size={15} />
        </button>

        {/* 현재 탭 이름 + LLM 표시 (탭 버튼들은 햄버거 드로어로 이동) */}
        <span style={{ fontSize: "var(--fs-13)", fontWeight: 700, color: "var(--color-text)", flexShrink: 0 }}>
          {chatTab === "chat" ? "새싹이" : (TABS.find((t) => t.id === chatTab)?.label ?? "새싹이")}
        </span>
        {llmInfoTop && (
          <span
            title={`현재 LLM: ${llmInfoTop.provider === "openai" ? "OpenAI" : "Ollama"} / ${llmInfoTop.model}`}
            style={{
              marginLeft: 8,
              fontSize: "var(--fs-11)",
              fontWeight: 600,
              padding: "2px 7px",
              borderRadius: 10,
              background: llmInfoTop.provider === "openai" ? "rgba(16,163,127,0.18)" : "rgba(100,140,220,0.18)",
              color: llmInfoTop.provider === "openai" ? "#10a37f" : "#7aa8ff",
              border: `1px solid ${llmInfoTop.provider === "openai" ? "rgba(16,163,127,0.4)" : "rgba(100,140,220,0.4)"}`,
              whiteSpace: "nowrap",
              maxWidth: 180,
              overflow: "hidden",
              textOverflow: "ellipsis",
              flexShrink: 1,
            }}
          >
            {llmInfoTop.provider === "openai" ? "GPT" : "Ollama"} · {llmInfoTop.model}
          </span>
        )}

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
        {chatTab === "graph" && (
          <Suspense
            fallback={
              <div style={{ padding: 20, fontSize: "var(--fs-12)", color: "var(--color-text-muted)" }}>
                그래프 로딩 중...
              </div>
            }
          >
            <GraphRagView />
          </Suspense>
        )}
        {/* DeepResearchView 항상 마운트 — 진행 중 리서치 state 보존 (E-19/E-20) */}
        <div style={{
          display: chatTab === "research" ? "flex" : "none",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
          minHeight: 0,
        }}>
          <DeepResearchView />
        </div>
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
