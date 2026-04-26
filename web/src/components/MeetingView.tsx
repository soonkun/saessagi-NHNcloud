import { useRef, useState } from "react";
import { FileAudio, Download, Loader, ChevronRight } from "lucide-react";
import { API_BASE } from "../services/api";
import { useStore } from "../store";
import { speak } from "../services/tts";

// ────────────────────────────────────────────────────────────
// 오디오 파일 유효성
// ────────────────────────────────────────────────────────────
const AUDIO_TYPES = [".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac", ".qta"];
const AUDIO_MIME = ["audio/wav","audio/mpeg","audio/mp4","audio/ogg","audio/webm","audio/flac","audio/x-wav","audio/x-flac"];

function isAudioFile(file: File): boolean {
  const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
  return AUDIO_TYPES.includes(ext) || AUDIO_MIME.includes(file.type);
}

// ────────────────────────────────────────────────────────────
// SSE 스트림 읽기
// ────────────────────────────────────────────────────────────
interface SseEvent {
  stage: string;
  message?: string;
  transcript?: string;
  meeting_notes?: string;
  file_id?: string;
  download_url?: string;
  expires_at?: string;
}

async function readSseStream(
  res: Response,
  onEvent: (evt: SseEvent) => void,
): Promise<void> {
  if (!res.body) throw new Error("응답 스트림이 없습니다.");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      let evt: SseEvent;
      try {
        evt = JSON.parse(line.slice(5).trim()) as SseEvent;
      } catch {
        continue; // JSON 파싱 실패만 무시
      }
      onEvent(evt); // 콜백 에러는 호출자로 전파
    }
  }
}

// ────────────────────────────────────────────────────────────
// 공통 컴포넌트
// ────────────────────────────────────────────────────────────
function ProgressLog({ steps }: { steps: string[] }) {
  if (steps.length === 0) return null;
  return (
    <div style={{
      background: "rgba(201,100,66,0.06)",
      border: "1px solid rgba(201,100,66,0.2)",
      borderRadius: 8, padding: "8px 12px",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      {steps.map((s, i) => (
        <div key={i} style={{
          fontSize: 11,
          color: i === steps.length - 1 ? "var(--color-accent)" : "var(--color-text-muted)",
          display: "flex", alignItems: "center", gap: 5,
        }}>
          <span>{i === steps.length - 1 ? "▶" : "✓"}</span>{s}
        </div>
      ))}
    </div>
  );
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div style={{
      color: "#e05050", fontSize: 12, padding: "8px 12px",
      background: "rgba(224,80,80,0.1)", border: "1px solid rgba(224,80,80,0.3)",
      borderRadius: 8,
    }}>
      오류: {msg}
    </div>
  );
}

function StepHeader({ n, title }: { n: number; title: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
      <div style={{
        width: 22, height: 22, borderRadius: "50%",
        background: "var(--color-accent)", color: "#fff",
        fontSize: 11, fontWeight: 700,
        display: "flex", alignItems: "center", justifyContent: "center",
        flexShrink: 0,
      }}>{n}</div>
      <span style={{ fontWeight: 700, fontSize: 14 }}>{title}</span>
    </div>
  );
}

function divider() {
  return <div style={{ height: 1, background: "var(--color-border)", margin: "4px 0" }} />;
}

function downloadTextFile(text: string, filename: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
}

// ────────────────────────────────────────────────────────────
// MeetingView
// ────────────────────────────────────────────────────────────
export function MeetingView(): React.ReactElement {
  const setMeetingGenerating = useStore((s) => s.setMeetingGenerating);

  // ── Step 1: 전사 ──────────────────────────────────────────
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [step1Status, setStep1Status] = useState<"idle"|"loading"|"done"|"error">("idle");
  const [step1Steps, setStep1Steps] = useState<string[]>([]);
  const [step1Error, setStep1Error] = useState("");
  const [transcript, setTranscript] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // ── Step 2: 회의록 ────────────────────────────────────────
  const [step2Input, setStep2Input] = useState("");
  const [pages, setPages] = useState<1 | 2>(1);
  const [step2Status, setStep2Status] = useState<"idle"|"loading"|"done"|"error">("idle");
  const [step2Steps, setStep2Steps] = useState<string[]>([]);
  const [step2Error, setStep2Error] = useState("");
  const [meetingNotes, setMeetingNotes] = useState("");

  // ── Step 3: 결과보고서 ────────────────────────────────────
  const [step3Input, setStep3Input] = useState("");
  const [step3Status, setStep3Status] = useState<"idle"|"loading"|"done"|"error">("idle");
  const [step3Steps, setStep3Steps] = useState<string[]>([]);
  const [step3Error, setStep3Error] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");

  // ── 오디오 선택 ───────────────────────────────────────────
  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f && isAudioFile(f)) setAudioFile(f);
    e.target.value = "";
  }

  // ── Step 1 실행 ───────────────────────────────────────────
  async function runStep1() {
    if (!audioFile) return;
    setStep1Status("loading"); setStep1Steps([]); setStep1Error("");
    setMeetingGenerating(true);
    try {
      const form = new FormData();
      form.append("audio_file", audioFile);
      const res = await fetch(API_BASE + "/api/meeting-minutes/transcribe-stream", { method: "POST", body: form });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      let result = "";
      await readSseStream(res, (evt) => {
        if (evt.message) setStep1Steps((p) => [...p, evt.message!]);
        if (evt.stage === "done" && evt.transcript) result = evt.transcript;
        if (evt.stage === "error") throw new Error(evt.message ?? "전사 실패");
      });
      if (!result) throw new Error("전사 결과가 비어 있습니다.");
      setTranscript(result);
      setStep1Status("done");
      void speak("전사가 완료되었어요. 확인해 보시고 회의록 작성을 시작해주세요.");
    } catch (e) {
      setStep1Error(e instanceof Error ? e.message : "전사 실패");
      setStep1Status("error");
    } finally {
      setMeetingGenerating(false);
    }
  }

  // ── Step 2 실행 ───────────────────────────────────────────
  async function runStep2() {
    const input = step2Input.trim() || transcript.trim();
    if (!input) return;
    setStep2Status("loading"); setStep2Steps([]); setStep2Error("");
    setMeetingGenerating(true);
    try {
      const form = new FormData();
      form.append("transcript", input);
      form.append("pages", String(pages));
      const res = await fetch(API_BASE + "/api/meeting-minutes/summarize-stream", { method: "POST", body: form });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      let result = "";
      await readSseStream(res, (evt) => {
        if (evt.message) setStep2Steps((p) => [...p, evt.message!]);
        if (evt.stage === "done" && evt.meeting_notes) result = evt.meeting_notes;
        if (evt.stage === "error") throw new Error(evt.message ?? "회의록 작성 실패");
      });
      if (!result) throw new Error("회의록 결과가 비어 있습니다.");
      setMeetingNotes(result);
      setStep2Status("done");
      void speak("회의록 작성이 완료되었습니다. 확인해보시고 결과보고서 작성을 시작하세요.");
    } catch (e) {
      setStep2Error(e instanceof Error ? e.message : "회의록 작성 실패");
      setStep2Status("error");
    } finally {
      setMeetingGenerating(false);
    }
  }

  // ── Step 3 실행 ───────────────────────────────────────────
  async function runStep3() {
    const input = step3Input.trim() || meetingNotes.trim();
    if (!input) return;
    setStep3Status("loading"); setStep3Steps([]); setStep3Error(""); setDownloadUrl("");
    setMeetingGenerating(true);
    try {
      const form = new FormData();
      form.append("transcript", input);
      form.append("pages", String(pages));
      const res = await fetch(API_BASE + "/api/meeting-minutes/generate-stream", { method: "POST", body: form });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      let dlUrl = "";
      await readSseStream(res, (evt) => {
        if (evt.message) setStep3Steps((p) => [...p, evt.message!]);
        if (evt.stage === "done" && evt.download_url) dlUrl = evt.download_url;
        if (evt.stage === "error") throw new Error(evt.message ?? "결과보고서 생성 실패");
      });
      if (!dlUrl) throw new Error("다운로드 URL을 받지 못했습니다.");
      setDownloadUrl(dlUrl);
      setStep3Status("done");
      void speak("결과보고서를 완성하였습니다. 내용을 검토하세요.");
    } catch (e) {
      setStep3Error(e instanceof Error ? e.message : "결과보고서 생성 실패");
      setStep3Status("error");
    } finally {
      setMeetingGenerating(false);
    }
  }

  const btn = (label: string | React.ReactNode, onClick: () => void, disabled: boolean, accent = true) => (
    <button onClick={onClick} disabled={disabled} style={{
      padding: "8px 14px", borderRadius: 7, border: "none",
      background: disabled ? "var(--color-border)" : accent ? "var(--color-accent)" : "rgba(201,100,66,0.15)",
      color: disabled ? "var(--color-text-muted)" : accent ? "#fff" : "var(--color-accent)",
      cursor: disabled ? "default" : "pointer",
      fontSize: 13, fontWeight: 600,
      display: "flex", alignItems: "center", gap: 6,
    }}>{label}</button>
  );

  return (
    <div style={{
      height: "100%", overflowY: "auto",
      padding: "14px 14px",
      display: "flex", flexDirection: "column", gap: 14,
    }}>

      {/* ── STEP 1: 전사 ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <StepHeader n={1} title="전사 (오디오 → 텍스트)" />

        <div
          onClick={() => fileInputRef.current?.click()}
          style={{
            border: `2px dashed ${audioFile ? "#4caf84" : "var(--color-border)"}`,
            borderRadius: 8, padding: "14px 12px", textAlign: "center",
            cursor: "pointer", background: audioFile ? "rgba(76,175,132,0.06)" : "transparent",
            transition: "all 0.15s", flexShrink: 0,
          }}
        >
          <FileAudio size={22} style={{ marginBottom: 4, color: audioFile ? "#4caf84" : "var(--color-text-muted)" }} />
          {audioFile ? (
            <>
              <p style={{ fontSize: 12, fontWeight: 600, color: "#4caf84" }}>{audioFile.name}</p>
              <p style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                {(audioFile.size / 1024 / 1024).toFixed(1)} MB — 클릭해서 변경
              </p>
            </>
          ) : (
            <p style={{ fontSize: 12, fontWeight: 600 }}>클릭하여 오디오 파일 선택</p>
          )}
          <input ref={fileInputRef} type="file" accept={AUDIO_TYPES.join(",")} style={{ display: "none" }} onChange={onFileChange} />
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {btn(
            step1Status === "loading" ? <><Loader size={13} className="spin" />전사 중...</> : "전사 시작",
            () => void runStep1(),
            step1Status === "loading" || !audioFile,
          )}
          {step1Status === "done" && btn(
            <><Download size={13} />TXT</>,
            () => downloadTextFile(transcript, "전사결과.txt"),
            false, false,
          )}
        </div>

        <ProgressLog steps={step1Steps} />
        {step1Status === "error" && <ErrorBox msg={step1Error} />}

        {step1Status === "done" && (
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            style={{
              background: "var(--color-bg)", border: "1px solid var(--color-border)",
              borderRadius: 6, color: "var(--color-text)", padding: "8px 10px",
              fontSize: 11, outline: "none", resize: "vertical", minHeight: 80,
              lineHeight: 1.6,
            }}
          />
        )}

        {step1Status === "done" && (
          <button
            onClick={() => { setStep2Input(transcript); }}
            style={{
              alignSelf: "flex-end", background: "transparent", border: "none",
              color: "var(--color-accent)", fontSize: 12, cursor: "pointer",
              display: "flex", alignItems: "center", gap: 4, fontWeight: 600,
            }}
          >
            2단계로 <ChevronRight size={13} />
          </button>
        )}
      </div>

      {divider()}

      {/* ── STEP 2: 회의록 ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <StepHeader n={2} title="회의록 작성 (텍스트 요약)" />
        <p style={{ fontSize: 11, color: "var(--color-text-muted)", margin: 0 }}>
          1단계 결과 또는 직접 입력한 녹취 텍스트를 회의록으로 정리합니다.
        </p>

        {/* 분량 선택 — 결과보고서 페이지 수 결정 */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>결과보고서 분량</span>
          {([1, 2] as const).map((n) => (
            <button key={n} onClick={() => setPages(n)} style={{
              padding: "4px 12px", borderRadius: 6,
              border: `1px solid ${pages === n ? "var(--color-accent)" : "var(--color-border)"}`,
              background: pages === n ? "rgba(201,100,66,0.15)" : "transparent",
              color: pages === n ? "var(--color-accent)" : "var(--color-text-muted)",
              cursor: "pointer", fontSize: 12, fontWeight: pages === n ? 600 : 400,
            }}>{n}페이지</button>
          ))}
        </div>

        <textarea
          value={step2Input}
          onChange={(e) => setStep2Input(e.target.value)}
          placeholder="녹취 텍스트 붙여넣기 (또는 1단계 결과 자동 입력)"
          style={{
            background: "var(--color-bg)", border: "1px solid var(--color-border)",
            borderRadius: 6, color: "var(--color-text)", padding: "8px 10px",
            fontSize: 11, outline: "none", resize: "vertical", minHeight: 80,
            lineHeight: 1.6,
          }}
        />

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {btn(
            step2Status === "loading" ? <><Loader size={13} className="spin" />작성 중...</> : "회의록 작성",
            () => void runStep2(),
            step2Status === "loading" || !(step2Input.trim() || transcript.trim()),
          )}
          {step2Status === "done" && btn(
            <><Download size={13} />TXT</>,
            () => downloadTextFile(meetingNotes, "회의록.txt"),
            false, false,
          )}
        </div>

        <ProgressLog steps={step2Steps} />
        {step2Status === "error" && <ErrorBox msg={step2Error} />}

        {step2Status === "done" && (
          <textarea
            value={meetingNotes}
            onChange={(e) => setMeetingNotes(e.target.value)}
            style={{
              background: "var(--color-bg)", border: "1px solid var(--color-border)",
              borderRadius: 6, color: "var(--color-text)", padding: "8px 10px",
              fontSize: 11, outline: "none", resize: "vertical", minHeight: 100,
              lineHeight: 1.6,
            }}
          />
        )}

        {step2Status === "done" && (
          <button
            onClick={() => { setStep3Input(meetingNotes); }}
            style={{
              alignSelf: "flex-end", background: "transparent", border: "none",
              color: "var(--color-accent)", fontSize: 12, cursor: "pointer",
              display: "flex", alignItems: "center", gap: 4, fontWeight: 600,
            }}
          >
            3단계로 <ChevronRight size={13} />
          </button>
        )}
      </div>

      {divider()}

      {/* ── STEP 3: 결과보고서 ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <StepHeader n={3} title={`결과보고서 (.hwpx · ${pages}페이지)`} />
        <p style={{ fontSize: 11, color: "var(--color-text-muted)", margin: 0 }}>
          2단계 회의록 또는 직접 입력한 내용을 공문서 양식으로 변환합니다.
        </p>

        <textarea
          value={step3Input}
          onChange={(e) => setStep3Input(e.target.value)}
          placeholder="회의록 내용 붙여넣기 (또는 2단계 결과 자동 입력)"
          style={{
            background: "var(--color-bg)", border: "1px solid var(--color-border)",
            borderRadius: 6, color: "var(--color-text)", padding: "8px 10px",
            fontSize: 11, outline: "none", resize: "vertical", minHeight: 80,
            lineHeight: 1.6,
          }}
        />

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {btn(
            step3Status === "loading" ? <><Loader size={13} className="spin" />생성 중...</> : "결과보고서 생성",
            () => void runStep3(),
            step3Status === "loading" || !(step3Input.trim() || meetingNotes.trim()),
          )}
        </div>

        <ProgressLog steps={step3Steps} />
        {step3Status === "error" && <ErrorBox msg={step3Error} />}

        {step3Status === "done" && downloadUrl && (
          <a
            href={downloadUrl}
            download
            style={{
              display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
              background: "rgba(76,175,132,0.15)", border: "1px solid #4caf84",
              borderRadius: 8, color: "#4caf84", padding: "10px",
              textDecoration: "none", fontSize: 14, fontWeight: 600,
            }}
          >
            <Download size={15} />보고서 다운로드 (.hwpx)
          </a>
        )}
      </div>

    </div>
  );
}
