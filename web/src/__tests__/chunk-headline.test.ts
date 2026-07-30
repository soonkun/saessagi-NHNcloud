/**
 * CR-45: 청크 목록 헤드라인 생성.
 *
 * 저장된 청크는 전부 `[출처: 파일명, N페이지] `로 시작한다. 이 접두어를 떼지 않으면
 * 목록의 모든 행이 똑같은 60여 글자로 시작해 어떤 청크인지 구분할 수 없다 —
 * 목록 화면 자체가 무의미해지므로 회귀 방지 테스트를 둔다.
 */
import { describe, it, expect } from "vitest";

import { chunkHeadline } from "../components/DocumentsView";

describe("chunkHeadline", () => {
  it("출처 메타 접두어를 제거한다", () => {
    const text = "[출처: TRKO202000030247_자유학기제와연계한농생명산업.pdf, 3페이지] 보고서 요약서";
    expect(chunkHeadline(text)).toBe("보고서 요약서");
  });

  it("같은 문서의 서로 다른 청크가 구분 가능한 헤드라인을 갖는다", () => {
    const prefix = "[출처: 같은문서.pdf, 1페이지] ";
    const a = chunkHeadline(prefix + "기후변화 영향 취약성평가는 지역별로 수행되었다");
    const b = chunkHeadline(prefix + "연구의 목적 및 내용은 다음과 같다");
    expect(a).not.toBe(b);
    expect(a.startsWith("[출처")).toBe(false);
  });

  it("개행·연속 공백을 한 칸으로 접어 한 줄로 만든다", () => {
    const text = "[출처: a.pdf, 1페이지] 제 줄\r\n농촌진흥청장   귀하\r\n본 보고서를";
    expect(chunkHeadline(text)).toBe("제 줄 농촌진흥청장 귀하 본 보고서를");
  });

  it("maxLen을 넘으면 말줄임표를 붙인다", () => {
    const body = "가".repeat(200);
    const out = chunkHeadline("[출처: a.pdf, 1페이지] " + body, 90);
    expect(out).toHaveLength(91); // 90자 + …
    expect(out.endsWith("…")).toBe(true);
  });

  it("maxLen 이하면 말줄임표를 붙이지 않는다", () => {
    expect(chunkHeadline("[출처: a.pdf, 1페이지] 짧은 본문")).toBe("짧은 본문");
  });

  it("접두어만 있고 본문이 없으면 빈 청크로 표시한다", () => {
    expect(chunkHeadline("[출처: a.pdf, 1페이지]   ")).toBe("(빈 청크)");
  });

  it("접두어가 없는 텍스트도 그대로 처리한다", () => {
    expect(chunkHeadline("접두어 없는 일반 텍스트")).toBe("접두어 없는 일반 텍스트");
  });

  it("빈 문자열·공백을 안전하게 처리한다", () => {
    expect(chunkHeadline("")).toBe("(빈 청크)");
    expect(chunkHeadline("   ")).toBe("(빈 청크)");
  });

  it("본문 안의 대괄호는 건드리지 않는다", () => {
    // 접두어 제거 정규식이 욕심내서 본문의 [ ]까지 먹어버리면 내용이 사라진다
    const out = chunkHeadline("[출처: a.pdf, 1페이지] 표 [1] 참조 결과");
    expect(out).toBe("표 [1] 참조 결과");
  });
});
