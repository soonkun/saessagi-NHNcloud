// doc_id → 과제 제목 조회 (CR-64)
//
// 답변 본문의 인용 칩에 **파일명 대신 제목**을 보여준다. 파일명은
// `TRKO202500017741_농업분야기후변화실태조사고도화및영향취약성정보서비스체계개발.pdf_afbc4f63`
// 처럼 길고 읽기 어렵다 — 문장 사이에 그대로 박히면 본문을 못 읽는다.
//
// 제목은 M_23 후보 저장소에만 있다(벡터 스토어 스키마에는 없다). 여러 메시지가 같은
// 문서를 인용하므로 **캐시 + 배치 조회**로 요청 수를 줄인다.

import { API_BASE } from "./api";

const cache = new Map<string, string>();
const pending = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();

/** 캐시가 갱신되면 알린다 — 훅이 리렌더한다. */
export function subscribeDocTitles(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getCachedTitle(docId: string): string | undefined {
  return cache.get(docId);
}

/** 아직 모르는 id만 모아 한 번에 조회한다. */
export async function ensureDocTitles(docIds: string[]): Promise<void> {
  const missing = docIds.filter((d) => d && !cache.has(d) && !pending.has(d));
  if (missing.length === 0) return;

  const job = (async () => {
    try {
      const res = await fetch(API_BASE + "/api/kg/doc-titles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_ids: missing }),
      });
      if (res.ok) {
        const data = (await res.json()) as Record<string, string>;
        for (const [id, title] of Object.entries(data)) {
          if (title) cache.set(id, title);
        }
      }
    } catch {
      // 조회 실패는 치명적이지 않다 — 호출자가 파일명으로 폴백한다.
    } finally {
      // 못 찾은 id도 pending에서 빼야 매 렌더마다 재요청하지 않는다.
      for (const id of missing) pending.delete(id);
      for (const fn of listeners) fn();
    }
  })();

  for (const id of missing) pending.set(id, job);
  await job;
}

/** 파일명에서 읽을 만한 제목을 만든다 (제목 조회 실패 시 폴백). */
export function titleFromFilename(name: string): string {
  return (name || "")
    .replace(/\.[^.]+$/, "") // 확장자
    .replace(/_[0-9a-f]{6,}$/i, "") // doc_id 꼬리 해시
    .replace(/^(?:TRKO|KAR)\d+[_\-\s]*/i, "") // 수집기관 일련번호
    .replace(/[_]+/g, " ")
    .trim();
}

/**
 * 칩에 넣을 길이로 줄인다 — **글자 7자까지**, 넘으면 `…`.
 *
 * 처음에 낱말 7개로 만들었더니 여전히 길었다(사용자: "칩 안의 내용은 6-7글자만
 * 표시하고 나머지는 …으로"). 연구과제 제목은
 * "농업분야 기후변화 실태조사 고도화 및 영향·취약성 정보서비스 체계 개발"처럼 길어서
 * 문장 사이에 박히면 본문을 읽을 수 없다. 전체 제목은 툴팁으로 본다.
 */
export function shortenTitle(title: string, maxChars = 7): string {
  const t = (title || "").trim();
  if (!t) return "";
  if (t.length <= maxChars) return t;
  return t.slice(0, maxChars).trimEnd() + "…";
}
