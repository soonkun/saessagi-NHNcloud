// CR-64 인용 칩 렌더 변환
import { describe, expect, it } from "vitest";
import { docIdsInText, groupMarkersByBlock, markersToLinks } from "../components/ChatPanel";
import { shortenTitle } from "../services/docTitles";

// 실측 사고 사례: 괄호가 든 doc_id가 마크다운 링크를 깨뜨려
// `[근거](saessagi-doc:12.%20(%EA%B3%A0…` 가 본문에 날것으로 떴다.
const PAREN_ID = "12. (고정) 드론영상 기반 관수.pdf_abc123";

describe("markersToLinks", () => {
  it("괄호가 든 파일명에서도 URL이 깨지지 않는다", () => {
    const out = markersToLinks(`본문 [[doc:${PAREN_ID}]]`, [PAREN_ID]);
    expect(out).toContain("[근거](saessagi-doc:0)");
    expect(out).not.toContain("(고정)");
    expect(out).not.toContain("%");
  });

  it("목록에 없는 id는 링크를 만들지 않는다", () => {
    expect(markersToLinks("[[doc:없는문서.pdf]]", [PAREN_ID])).not.toContain("근거");
  });
});

describe("groupMarkersByBlock", () => {
  it("문장마다 붙은 마커를 섹션 끝으로 모은다", () => {
    const text = [
      "2. 핵심 활용 기술",
      "**센서**: RGB 카메라를 씁니다. [[doc:A.pdf]]",
      "**분석**: 전처리 후 분류합니다. [[doc:B.pdf]]",
      "3. 기대 효과",
      "정밀 관수가 가능합니다.",
    ].join("\n");
    const out = groupMarkersByBlock(text);
    const lines = out.split("\n");
    // 마커는 본문 줄에서 빠지고, '3.' 앞에 모여 있어야 한다
    expect(lines.find((l) => l.includes("RGB 카메라"))).not.toContain("[[doc:");
    const markerLine = lines.findIndex((l) => l.includes("[[doc:A.pdf]]"));
    const nextSection = lines.findIndex((l) => l.startsWith("3."));
    expect(markerLine).toBeGreaterThan(-1);
    expect(markerLine).toBeLessThan(nextSection);
    expect(lines[markerLine]).toContain("[[doc:B.pdf]]");
  });

  it("같은 마커는 한 번만 모은다", () => {
    const out = groupMarkersByBlock("가. [[doc:A.pdf]]\n나. [[doc:A.pdf]]");
    expect(out.match(/\[\[doc:A\.pdf\]\]/g)).toHaveLength(1);
  });

  it("표 안에는 칩을 넣지 않고 표가 끝난 뒤로 뺀다", () => {
    const text = [
      "② 세부 분야별 대응 기술",
      "| 구분 | 내용 |",
      "| --- | --- |",
      "| 예측 | 작물 모형 개발 [[doc:A.pdf]] |",
      "| 위험 | 조기경보 운영 [[doc:B.pdf]] |",
      "3. 주요 국가별 사례",
    ].join("\n");
    const out = groupMarkersByBlock(text);
    const lines = out.split("\n");
    // 표의 어떤 줄에도 마커가 남으면 안 된다
    for (const l of lines.filter((x) => x.trim().startsWith("|"))) {
      expect(l).not.toContain("[[doc:");
    }
    const markerLine = lines.findIndex((l) => l.includes("[[doc:A.pdf]]"));
    const lastTable = lines.map((l) => l.trim().startsWith("|")).lastIndexOf(true);
    const next = lines.findIndex((l) => l.startsWith("3."));
    expect(markerLine).toBeGreaterThan(lastTable);
    expect(markerLine).toBeLessThan(next);
    expect(lines[markerLine]).toContain("[[doc:B.pdf]]");
  });

  it("상위 항목 안의 하위 불릿·빈 줄에서는 흩어지지 않는다", () => {
    const text = [
      "2. 핵심 기술",
      "**센서**: RGB 카메라. [[doc:A.pdf]]",
      "",
      "**분석**: 전처리 후 분류. [[doc:B.pdf]]",
      "① 세부: 회귀모델 적용. [[doc:C.pdf]]",
      "3. 기대 효과",
    ].join("\n");
    const lines = groupMarkersByBlock(text).split("\n");
    const markerLines = lines.filter((l) => l.includes("[[doc:"));
    expect(markerLines).toHaveLength(1);
    expect(markerLines[0]).toContain("[[doc:C.pdf]]");
  });

  it("마커가 없으면 본문을 바꾸지 않는다", () => {
    const text = "1. 제목\n내용입니다.\n\n2. 다음";
    expect(groupMarkersByBlock(text)).toBe(text);
  });
});

describe("shortenTitle", () => {
  it("7글자를 넘으면 줄이고 …을 붙인다", () => {
    expect(shortenTitle("농업분야 기후변화 실태조사 고도화")).toBe("농업분야 기후…");
    expect(shortenTitle("짧은제목")).toBe("짧은제목");
  });
});

describe("대괄호가 든 파일명 (E-106)", () => {
  const BR = "[이암허브]농식품R&BD기획지원사업 최종보고서.pdf_e3072e8b";

  it("마커로 인식하고 칩 링크를 만든다", () => {
    expect(docIdsInText(`본문 [[doc:${BR}]]`)).toEqual([BR]);
    expect(markersToLinks(`본문 [[doc:${BR}]]`, [BR])).toContain("[근거](saessagi-doc:0)");
  });

  it("섹션 묶기에서도 본문에 날것으로 남지 않는다", () => {
    const out = groupMarkersByBlock(`1. 제목\n내용 [[doc:${BR}]]\n2. 다음`);
    expect(out.split("\n").find((l) => l.startsWith("내용"))).not.toContain("이암허브");
    expect(out).toContain(`[[doc:${BR}]]`);
  });
});

describe("docIdsInText", () => {
  it("중복 없이 등장 순서대로 모은다", () => {
    expect(docIdsInText("[[doc:A]] x [[doc:B]] y [[doc:A]]")).toEqual(["A", "B"]);
  });
});
