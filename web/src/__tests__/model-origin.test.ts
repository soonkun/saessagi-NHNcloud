// web/src/__tests__/model-origin.test.ts
/**
 * 모델 출처 표기 (CR-50).
 *
 * 접두사 매칭이라 순서가 중요하다 — 짧은 접두사가 앞에 오면 긴 것을 가로챈다.
 * 특히 gpt-oss(OpenAI 오픈웨이트)와 gpt-4o(API)는 둘 다 OpenAI지만 다른 항목이고,
 * qwen/yi처럼 짧은 이름이 다른 모델을 잘못 잡지 않아야 한다.
 */
import { describe, it, expect } from "vitest";

import { modelOrigin, modelOptionLabel } from "../modelOrigin";

describe("modelOrigin", () => {
  it("현재 설치된 모델들을 모두 알아본다", () => {
    expect(modelOrigin("gemma4:31b")).toEqual({ flag: "🇺🇸", vendor: "Google" });
    expect(modelOrigin("gemma4:26b")).toEqual({ flag: "🇺🇸", vendor: "Google" });
    expect(modelOrigin("granite4.1:30b")).toEqual({ flag: "🇺🇸", vendor: "IBM" });
    expect(modelOrigin("gpt-oss:120b")).toEqual({ flag: "🇺🇸", vendor: "OpenAI" });
    expect(modelOrigin("mistral-medium-3.5:128b")).toEqual({
      flag: "🇫🇷",
      vendor: "Mistral AI",
    });
  });

  it("gpt-oss가 gpt- 규칙에 먼저 잡히지 않는다(둘 다 OpenAI지만 별개 항목)", () => {
    expect(modelOrigin("gpt-oss:20b").vendor).toBe("OpenAI");
    expect(modelOrigin("gpt-4o").vendor).toBe("OpenAI");
  });

  it("나라별로 구분된다", () => {
    expect(modelOrigin("qwen3.5:32b").flag).toBe("🇨🇳");
    expect(modelOrigin("exaone-4.0:32b").flag).toBe("🇰🇷");
    expect(modelOrigin("command-r7b").flag).toBe("🇨🇦");
    expect(modelOrigin("llama4:16x17b").flag).toBe("🇺🇸");
    expect(modelOrigin("devstral:24b").flag).toBe("🇫🇷");
  });

  it("대소문자·공백에 흔들리지 않는다", () => {
    expect(modelOrigin("  GEMMA4:31B  ").vendor).toBe("Google");
  });

  it("모르는 모델은 빈 값 — 추측해서 잘못 붙이지 않는다", () => {
    expect(modelOrigin("some-unknown-model:7b")).toEqual({ flag: "", vendor: "" });
    expect(modelOrigin("")).toEqual({ flag: "", vendor: "" });
  });
});

describe("modelOptionLabel", () => {
  it("국기·회사를 이름 앞에 붙인다", () => {
    expect(modelOptionLabel("gemma4:31b")).toBe("🇺🇸 Google · gemma4:31b");
  });

  it("모르는 모델은 이름만 — 목록을 어지럽히지 않는다", () => {
    expect(modelOptionLabel("weird:1b")).toBe("weird:1b");
  });

  it("뒤에 덧붙일 설명을 받을 수 있다", () => {
    expect(modelOptionLabel("gemma4:31b", " (19GB)")).toBe(
      "🇺🇸 Google · gemma4:31b (19GB)",
    );
  });
});
