// CR-23: 대화방 히스토리 목록 — 데스크톱 사이드바·펫 모드 드로어 공용.
// 최신 대화가 위. 클릭 = 그 대화방으로 전환 (메시지 복원 + 백엔드 메모리 전환).
import { useEffect } from "react";
import { Trash2 } from "lucide-react";
import { useStore } from "../store";
import type { HistoryInfo } from "../types";
import { send } from "../services/websocket";

function stripPreview(content: string | undefined | null): string {
  return (
    (content ?? "")
      .replace(/\[[a-z_]+\]/gi, "") // 감정 태그 제거
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 48) || "(빈 대화)"
  );
}

/**
 * 목록에 보일 한 줄 (CR-53).
 *
 * 서버가 준 제목(첫 사용자 질문)을 우선 쓴다. 예전에는 마지막 메시지(대개 답변)를 썼는데,
 * 답변이 "자료를 찾아볼게요!"로 시작하다 보니 목록 전체가 같은 문구로 보여 구분이 안 됐다.
 */
function historyLabel(h: HistoryInfo): string {
  const t = (h.title ?? "").trim();
  if (t) return t.length > 48 ? t.slice(0, 48) : t;
  return stripPreview(h.latest_message?.content); // 제목이 없는 예전 데이터 대비
}

export function HistoryList({ onSelect }: { onSelect?: () => void }): React.ReactElement {
  const histories = useStore((s) => s.histories);
  const currentHistoryUid = useStore((s) => s.currentHistoryUid);
  const aiStatus = useStore((s) => s.aiStatus);

  // 마운트 시 + 답변 완료(idle 전환)마다 목록 갱신 — 최신 미리보기 반영
  useEffect(() => {
    if (aiStatus === "idle") send({ type: "fetch-history-list" });
  }, [aiStatus]);

  function handleSwitch(uid: string): void {
    send({ type: "fetch-and-set-history", history_uid: uid });
    useStore.getState().setCurrentHistoryUid(uid);
    useStore.getState().setChatTab("chat");
    onSelect?.();
  }

  function handleDelete(uid: string): void {
    if (!window.confirm("이 대화를 삭제할까요? 되돌릴 수 없습니다.")) return;
    send({ type: "delete-history", history_uid: uid });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "6px 6px 8px" }}>
        {histories.length === 0 && (
          <div
            style={{
              padding: "12px 8px",
              fontSize: "var(--fs-11)",
              color: "var(--color-text-muted)",
              textAlign: "center",
            }}
          >
            저장된 대화가 없습니다.
          </div>
        )}
        {histories.map((h) => {
          const isCurrent = h.uid === currentHistoryUid;
          const ts = h.timestamp ? h.timestamp.replace("T", " ").slice(5, 16) : "";
          return (
            <div
              key={h.uid}
              onClick={() => handleSwitch(h.uid)}
              title={historyLabel(h)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: "6px 8px",
                marginBottom: 2,
                borderRadius: 7,
                cursor: "pointer",
                background: isCurrent ? "rgba(100,140,220,0.12)" : "transparent",
                border: `1px solid ${isCurrent ? "rgba(100,140,220,0.4)" : "transparent"}`,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: "var(--fs-12)",
                    color: isCurrent ? "var(--color-accent)" : "var(--color-text)",
                    fontWeight: isCurrent ? 600 : 400,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {historyLabel(h)}
                </div>
                <div style={{ fontSize: "var(--fs-10)", color: "var(--color-text-muted)", marginTop: 1 }}>
                  {ts}
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(h.uid);
                }}
                title="대화 삭제"
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--color-text-muted)",
                  display: "flex",
                  padding: 3,
                  flexShrink: 0,
                }}
              >
                <Trash2 size={11} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
