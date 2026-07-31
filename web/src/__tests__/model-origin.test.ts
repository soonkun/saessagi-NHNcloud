// web/src/__tests__/model-origin.test.ts
/**
 * 모델 출처 표기 (CR-50).
 *
 * 접두사 매칭이라 순서가 중요하다 — 짧은 접두사가 앞에 오면 긴 것을 가로챈다.
 * 특히 gpt-oss(OpenAI 오픈웨이트)와 gpt-4o(API)는 둘 다 OpenAI지만 다른 항목이고,
 * qwen/yi처럼 짧은 이름이 다른 모델을 잘못 잡지 않아야 한다.
 */
import { describe, it, expect } from "vitest";

import {
  modelOrigin,
  modelOptionLabel,
  modelParamSize,
  sortModels,
} from "../modelOrigin";

describe("modelOrigin", () => {
  it("현재 설치된 모델들을 모두 알아본다", () => {
    expect(modelOrigin("gemma4:31b")).toEqual({
      flag: "🇺🇸",
      vendor: "Google",
      country: "미국",
    });
    expect(modelOrigin("gemma4:26b").vendor).toBe("Google");
    expect(modelOrigin("granite4.1:30b").vendor).toBe("IBM");
    expect(modelOrigin("gpt-oss:120b").vendor).toBe("OpenAI");
    expect(modelOrigin("mistral-medium-3.5:128b")).toEqual({
      flag: "🇫🇷",
      vendor: "Mistral AI",
      country: "프랑스",
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
    expect(modelOrigin("some-unknown-model:7b")).toEqual({
      flag: "",
      vendor: "",
      country: "",
    });
    expect(modelOrigin("")).toEqual({ flag: "", vendor: "", country: "" });
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

describe("modelParamSize", () => {
  it("태그에서 파라미터 크기를 뽑는다", () => {
    expect(modelParamSize("gemma4:26b")).toBe(26);
    expect(modelParamSize("granite4.1:8b")).toBe(8);
    expect(modelParamSize("gpt-oss:120b")).toBe(120);
    expect(modelParamSize("mistral-medium-3.5:128b")).toBe(128);
  });

  it("소수점·나노 표기도 읽는다", () => {
    expect(modelParamSize("qwen3:1.5b")).toBe(1.5);
    expect(modelParamSize("gemma4:e4b")).toBe(4);
    expect(modelParamSize("gemma4:e2b")).toBe(2);
  });

  it("MoE 표기는 전문가 하나 크기를 쓴다(표기만으로 총량을 알 수 없다)", () => {
    expect(modelParamSize("llama4:16x17b")).toBe(17);
  });

  it("이름의 버전 숫자를 크기로 착각하지 않는다", () => {
    // granite4.1의 4.1, mistral-medium-3.5의 3.5가 잡히면 정렬이 엉킨다
    expect(modelParamSize("granite4.1:30b")).toBe(30);
    expect(modelParamSize("mistral-medium-3.5:128b")).toBe(128);
  });

  it("크기가 없으면 null", () => {
    expect(modelParamSize("granite4.1:latest")).toBeNull();
    expect(modelParamSize("")).toBeNull();
  });
});

describe("sortModels", () => {
  it("국가 → 회사 → 크기(작은 것부터) 순으로 정렬한다", () => {
    const got = sortModels([
      "mistral-medium-3.5:128b",
      "gemma4:26b",
      "granite4.1:30b",
      "gpt-oss:120b",
      "gemma4:e4b",
      "granite4.1:8b",
      "gemma4:31b",
    ]);
    expect(got).toEqual([
      // 미국 · Google (4 → 26 → 31)
      "gemma4:e4b",
      "gemma4:26b",
      "gemma4:31b",
      // 미국 · IBM (8 → 30)
      "granite4.1:8b",
      "granite4.1:30b",
      // 미국 · OpenAI
      "gpt-oss:120b",
      // 프랑스 · Mistral AI
      "mistral-medium-3.5:128b",
    ]);
  });

  it("문자열 정렬로는 크기 순서가 뒤집힌다 — 숫자 비교가 실제로 필요하다", () => {
    expect(sortModels(["granite4.1:30b", "granite4.1:8b"])).toEqual([
      "granite4.1:8b",
      "granite4.1:30b",
    ]);
    expect([...["granite4.1:30b", "granite4.1:8b"]].sort()).toEqual([
      "granite4.1:30b",
      "granite4.1:8b",
    ]);
  });

  it("출처를 모르는 모델은 맨 뒤로 — 분류를 흐트러뜨리지 않게", () => {
    const got = sortModels(["zzz-unknown:7b", "gemma4:26b", "qwen3.5:32b"]);
    expect(got[got.length - 1]).toBe("zzz-unknown:7b");
  });

  it("원본 배열을 바꾸지 않는다", () => {
    const src = ["granite4.1:30b", "gemma4:26b"];
    sortModels(src);
    expect(src).toEqual(["granite4.1:30b", "gemma4:26b"]);
  });
});
