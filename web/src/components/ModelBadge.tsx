// 기능별로 "지금 실제 적용 중인 모델"을 제목 옆에 보여주는 뱃지 (CR-57).
//
// 모델 설정이 대화·비전·의도분류·그래프추출·딥리서치로 나뉜 뒤로, 어느 화면이 어떤
// 모델로 도는지 알 방법이 없었다("각 파트별로 실제로 어떤 모델이 적용중인지가 잘
// 표시가 안되고있어" — 사용자). 백엔드가 same_as_chat을 실제 모델명으로 풀어서
// 주므로 여기서는 그대로 보여주기만 한다.

import { useEffect } from "react";
import { useStore } from "../store";
import { modelOrigin } from "../modelOrigin";
import type { ActiveModel } from "../services/api";

/** 기능 키에 해당하는 적용 모델. 아직 못 읽었으면 null. */
export function useActiveModel(key: ActiveModel["key"]): ActiveModel | null {
  const models = useStore((s) => s.activeModels);
  const refresh = useStore((s) => s.refreshActiveModels);

  useEffect(() => {
    // 이미 받아 뒀으면 다시 부르지 않는다. 설정 저장 시에는 저장한 쪽이 갱신한다.
    if (models.length === 0) void refresh();
  }, [models.length, refresh]);

  return models.find((m) => m.key === key) ?? null;
}

export function ModelBadge({
  modelKey,
  compact = false,
}: {
  modelKey: ActiveModel["key"];
  compact?: boolean;
}): React.ReactElement | null {
  const info = useActiveModel(modelKey);
  if (!info || !info.model) return null;

  const { flag } = modelOrigin(info.model);
  const isOpenai = info.provider === "openai";
  // 꺼진 기능은 모델을 적어 봐야 오해만 준다 — 꺼졌다는 사실을 먼저 알린다.
  const off = info.enabled === false;

  return (
    <span
      title={
        off
          ? `${info.label}: 비활성 상태 (설정에서 켤 수 있습니다)`
          : `${info.label}에 적용 중인 모델: ${isOpenai ? "OpenAI" : "Ollama"} / ${info.model}`
      }
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: compact ? "1px 6px" : "2px 8px",
        borderRadius: 999,
        border: "1px solid var(--color-border)",
        background: "var(--color-bg-subtle, transparent)",
        fontSize: "var(--fs-11)",
        color: off ? "var(--color-text-muted)" : isOpenai ? "#10a37f" : "#7aa8ff",
        whiteSpace: "nowrap",
        maxWidth: "100%",
        overflow: "hidden",
        textOverflow: "ellipsis",
        verticalAlign: "middle",
      }}
    >
      {off ? "꺼짐" : `${flag ? flag + " " : ""}${info.model}`}
    </span>
  );
}
