// CR-59: 보고서 본문 정리 규칙.
//
// gemma4 계열이 한국어 보고서에도 LaTeX 기호(`$\rightarrow$`)를 섞어 쓰고, 한국어에서는
// `**'강조'**` 형태가 마크다운 규칙상 굵게 되지 않아 별표가 그대로 노출된다.
// 표시 직전에 다듬되, **본문에 실제로 쓰인 금액 표기 같은 것은 건드리면 안 된다.**

import { describe, it, expect } from "vitest";
import {
  cleanReportMarkdown,
  safeFileStem,
  escapeHtml,
  researchTitle,
  extractProjectTitle,
} from "../reportDoc";

describe("cleanReportMarkdown — LaTeX 흔적", () => {
  it("인라인 수식으로 감싼 화살표를 유니코드로 바꾼다", () => {
    expect(cleanReportMarkdown("동향 분석 $\\rightarrow$ 시사점 도출")).toBe(
      "동향 분석 → 시사점 도출"
    );
  });

  it("달러 없이 명령만 있어도 바꾼다", () => {
    expect(cleanReportMarkdown("기존 \\times 신규")).toBe("기존 × 신규");
  });

  it("여러 기호가 섞여도 모두 처리한다", () => {
    expect(cleanReportMarkdown("$\\alpha$ 값이 $\\le$ 0.05, 오차 $\\pm$ 2%")).toBe(
      "α 값이 ≤ 0.05, 오차 ± 2%"
    );
  });

  it("금액 표기는 수식으로 오인하지 않는다", () => {
    const text = "예산 $100 상당의 장비와 $200 상당의 소모품";
    expect(cleanReportMarkdown(text)).toBe(text);
  });

  it("모르는 수식은 손대지 않는다 — 잘못 벗기면 뜻이 바뀐다", () => {
    const text = "$\\frac{a}{b}$ 형태의 비율";
    expect(cleanReportMarkdown(text)).toBe(text);
  });

  it("줄바꿈을 건너뛰며 엉뚱한 구간을 묶지 않는다", () => {
    const text = "비용은 $100\n수익은 $200";
    expect(cleanReportMarkdown(text)).toBe(text);
  });

  it("이스케이프된 문장부호의 백슬래시를 없앤다", () => {
    expect(cleanReportMarkdown("증가율 30\\% 및 항목\\_A")).toBe("증가율 30% 및 항목_A");
  });
});

describe("cleanReportMarkdown — 한국어 강조", () => {
  it("따옴표를 강조 밖으로 빼 굵게 표시되게 한다", () => {
    // `**'기후변화'**이라는` 은 닫는 표시가 인정되지 않아 별표가 그대로 보인다.
    expect(cleanReportMarkdown("본 검토는 **'기후변화 대응'**이라는 주제로")).toBe(
      "본 검토는 '**기후변화 대응**'이라는 주제로"
    );
  });

  it("괄호도 같은 방식으로 처리한다", () => {
    expect(cleanReportMarkdown("**(중요)**항목")).toBe("(**중요**)항목");
  });

  it("멀쩡한 강조는 그대로 둔다", () => {
    const text = "이것은 **정말 중요한** 내용입니다";
    expect(cleanReportMarkdown(text)).toBe(text);
  });

  it("굵은 표 헤더처럼 문장부호가 없는 강조를 망가뜨리지 않는다", () => {
    const text = "| **구분** | **내용** |";
    expect(cleanReportMarkdown(text)).toBe(text);
  });

  it("빈 강조는 건드리지 않는다", () => {
    expect(cleanReportMarkdown("**''**")).toBe("**''**");
  });
});

describe("cleanReportMarkdown — 안전성", () => {
  it("빈 입력을 견딘다", () => {
    expect(cleanReportMarkdown("")).toBe("");
  });

  it("표·목록 구조를 보존한다", () => {
    const md = "## 제목\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- 항목\n- 항목2\n";
    expect(cleanReportMarkdown(md)).toBe(md);
  });
});

describe("파일 이름·이스케이프", () => {
  it("파일 이름에 못 쓰는 문자를 지운다", () => {
    expect(safeFileStem('보고서/초안: "v1"')).toBe("보고서 초안 v1");
  });

  it("길이를 제한한다", () => {
    expect(safeFileStem("가".repeat(80)).length).toBe(40);
  });

  it("HTML 특수문자를 이스케이프한다", () => {
    expect(escapeHtml('<b>"x" & y</b>')).toBe("&lt;b&gt;&quot;x&quot; &amp; y&lt;/b&gt;");
  });
});

describe("researchTitle — 딥 리서치 결과 제목 (CR-62)", () => {
  const REPORT = [
    "## 과제 중복성 및 개선방향 검토 보고서",
    "",
    "### 1. 검토 대상 요약",
    "- **과제명**: 마른논 써레질 직파 재배기술 확립 및 전과정 평가",
    "- **연구목표**: 기술체계를 확립하고 …",
  ].join("\n");

  it("보고서의 과제명을 제목으로 쓴다", () => {
    expect(researchTitle("duplication", REPORT, "")).toBe(
      "(중복성검토)마른논 써레질 직파 재배기술 확립 및 전과정 평가",
    );
  });

  it("모드마다 앞머리가 다르다", () => {
    expect(researchTitle("discovery", REPORT, "")).toMatch(/^\(신규과제발굴\)/);
    expect(researchTitle("proposal", REPORT, "")).toMatch(/^\(계획서초안\)/);
  });

  it("표기가 흔들려도 뽑는다", () => {
    for (const line of [
      "과제명 : 사과 육종시스템 개발",
      "○ 연구과제명: 사과 육종시스템 개발",
      "**과제명**：사과 육종시스템 개발",
    ]) {
      expect(extractProjectTitle(line)).toBe("사과 육종시스템 개발");
    }
  });

  it("과제명이 없으면 첨부 파일명으로 물러선다", () => {
    expect(researchTitle("duplication", "본문에 과제명이 없다", "", "벼 재배기술.pdf")).toBe(
      "(중복성검토)벼 재배기술",
    );
  });

  it("파일도 없으면 요청 문구를 쓴다", () => {
    expect(researchTitle("duplication", "없음", "스마트팜 데이터 표준화")).toBe(
      "(중복성검토)스마트팜 데이터 표준화",
    );
  });

  it("아무 단서가 없으면 날짜·시각으로 서로 달라진다", () => {
    const a = researchTitle("duplication", "", "", "", new Date(2026, 7, 4, 13, 53));
    const b = researchTitle("duplication", "", "", "", new Date(2026, 7, 4, 14, 10));
    expect(a).not.toBe(b);
    expect(a).toContain("2026-08-04 13:53");
  });

  it("같은 모드라도 과제가 다르면 제목이 다르다 — 목록 구분이 목적이다", () => {
    const r1 = "**과제명**: 사과 육종시스템 개발";
    const r2 = "**과제명**: 복숭아 육종시스템 개발";
    expect(researchTitle("duplication", r1, "")).not.toBe(researchTitle("duplication", r2, ""));
  });

  it("너무 긴 과제명은 자른다", () => {
    const long = "**과제명**: " + "가".repeat(120);
    expect(researchTitle("duplication", long, "").length).toBeLessThanOrEqual(70);
  });

  it("과제명이 없는 보고서에서 엉뚱한 줄을 잡지 않는다", () => {
    expect(extractProjectTitle("## 검토 결과\n연구목표: 무언가")).toBe("");
  });
});
