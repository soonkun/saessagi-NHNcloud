import { useRef, useState } from "react";
import { FileAudio, Download, Loader } from "lucide-react";
import { generateMeetingMinutes } from "../services/api";

const AUDIO_TYPES = [".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"];
const AUDIO_MIME = [
  "audio/wav",
  "audio/mpeg",
  "audio/mp4",
  "audio/ogg",
  "audio/webm",
  "audio/flac",
  "audio/x-wav",
  "audio/x-flac",
];

function isAudioFile(file: File): boolean {
  const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
  return AUDIO_TYPES.includes(ext) || AUDIO_MIME.includes(file.type);
}

export function MeetingView(): React.ReactElement {
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [transcript, setTranscript] = useState("");
  const [pages, setPages] = useState<2 | 3>(2);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">(
    "idle"
  );
  const [downloadUrl, setDownloadUrl] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function onDragOver(e: React.DragEvent): void {
    e.preventDefault();
    setDragging(true);
  }

  function onDragLeave(): void {
    setDragging(false);
  }

  function onDrop(e: React.DragEvent): void {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f && isAudioFile(f)) setAudioFile(f);
  }

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>): void {
    const f = e.target.files?.[0];
    if (f && isAudioFile(f)) setAudioFile(f);
    e.target.value = "";
  }

  async function handleGenerate(): Promise<void> {
    if (!transcript.trim() && !audioFile) return;
    setStatus("loading");
    setErrorMsg("");
    try {
      const result = await generateMeetingMinutes({
        transcript: transcript.trim() || undefined,
        audio_file: audioFile ?? undefined,
        pages,
      });
      setDownloadUrl(result.download_url);
      setStatus("done");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "생성 실패");
      setStatus("error");
    }
  }

  return (
    <div
      style={{
        height: "100%",
        overflowY: "auto",
        padding: "16px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 14,
      }}
    >
      <div>
        <h3 style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
          회의록 생성
        </h3>
        <p style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
          오디오 파일을 업로드하거나 녹취 텍스트를 붙여넣으세요.
        </p>
      </div>

      {/* 오디오 파일 드롭존 */}
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        style={{
          border: `2px dashed ${dragging ? "var(--color-accent)" : audioFile ? "#4caf84" : "var(--color-border)"}`,
          borderRadius: 8,
          padding: "20px 16px",
          textAlign: "center",
          cursor: "pointer",
          background: dragging
            ? "rgba(201,100,66,0.06)"
            : audioFile
            ? "rgba(76,175,132,0.06)"
            : "transparent",
          transition: "all 0.15s",
          flexShrink: 0,
        }}
      >
        <FileAudio
          size={28}
          style={{
            marginBottom: 8,
            color: audioFile ? "#4caf84" : "var(--color-text-muted)",
          }}
        />
        {audioFile ? (
          <>
            <p style={{ fontSize: 13, fontWeight: 600, color: "#4caf84" }}>
              {audioFile.name}
            </p>
            <p style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
              {(audioFile.size / 1024 / 1024).toFixed(1)} MB — 클릭해서 변경
            </p>
          </>
        ) : (
          <>
            <p style={{ fontSize: 13, fontWeight: 600 }}>오디오 파일 드래그 또는 클릭</p>
            <p style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
              .wav · .mp3 · .m4a · .ogg
            </p>
          </>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept={AUDIO_TYPES.join(",")}
          style={{ display: "none" }}
          onChange={onFileChange}
        />
      </div>

      {/* 구분선 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          color: "var(--color-text-muted)",
          fontSize: 11,
        }}
      >
        <div style={{ flex: 1, height: 1, background: "var(--color-border)" }} />
        또는 녹취 텍스트 직접 입력
        <div style={{ flex: 1, height: 1, background: "var(--color-border)" }} />
      </div>

      {/* 녹취록 텍스트 입력 */}
      <textarea
        value={transcript}
        onChange={(e) => setTranscript(e.target.value)}
        placeholder="회의 녹취 내용을 붙여넣으세요..."
        style={{
          background: "var(--color-bg)",
          border: "1px solid var(--color-border)",
          borderRadius: 8,
          color: "var(--color-text)",
          padding: "10px 12px",
          fontSize: 12,
          outline: "none",
          resize: "vertical",
          minHeight: 110,
          lineHeight: 1.6,
          flexShrink: 0,
        }}
      />

      {/* 페이지 수 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <span style={{ fontSize: 13, color: "var(--color-text-muted)" }}>
          보고서 분량
        </span>
        {([2, 3] as const).map((n) => (
          <button
            key={n}
            onClick={() => setPages(n)}
            style={{
              padding: "5px 14px",
              borderRadius: 6,
              border: `1px solid ${pages === n ? "var(--color-accent)" : "var(--color-border)"}`,
              background: pages === n ? "rgba(201,100,66,0.15)" : "transparent",
              color: pages === n ? "var(--color-accent)" : "var(--color-text-muted)",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: pages === n ? 600 : 400,
            }}
          >
            {n}페이지
          </button>
        ))}
      </div>

      {/* 생성 버튼 */}
      <button
        onClick={() => void handleGenerate()}
        disabled={status === "loading" || (!transcript.trim() && !audioFile)}
        style={{
          background:
            status === "loading" || (!transcript.trim() && !audioFile)
              ? "var(--color-border)"
              : "var(--color-accent)",
          border: "none",
          borderRadius: 8,
          color: "#fff",
          cursor:
            status === "loading" || (!transcript.trim() && !audioFile)
              ? "default"
              : "pointer",
          padding: "10px",
          fontSize: 14,
          fontWeight: 600,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
          flexShrink: 0,
          transition: "background 0.15s",
        }}
      >
        {status === "loading" ? (
          <>
            <Loader size={15} className="spin" />
            생성 중...
          </>
        ) : (
          "회의록 생성"
        )}
      </button>

      {/* 결과 */}
      {status === "done" && downloadUrl && (
        <a
          href={downloadUrl}
          download
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            background: "rgba(76,175,132,0.15)",
            border: "1px solid #4caf84",
            borderRadius: 8,
            color: "#4caf84",
            padding: "10px",
            textDecoration: "none",
            fontSize: 14,
            fontWeight: 600,
            flexShrink: 0,
          }}
        >
          <Download size={16} />
          보고서 다운로드 (.hwpx)
        </a>
      )}

      {status === "error" && (
        <div
          style={{
            color: "#e05050",
            fontSize: 13,
            padding: "8px 12px",
            background: "rgba(224,80,80,0.1)",
            border: "1px solid rgba(224,80,80,0.3)",
            borderRadius: 8,
            flexShrink: 0,
          }}
        >
          오류: {errorMsg}
        </div>
      )}
    </div>
  );
}
