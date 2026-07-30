// web/src/__tests__/floating-avatar.test.ts
/**
 * CR-47 떠 있는 새싹이 — 위치·크기 계산 규칙.
 *
 * 화면 밖으로 나가면 캐릭터를 되찾을 방법이 없다(클릭 메뉴가 없으므로). 창을 줄였을 때
 * 반드시 화면 안에 남는지가 이 기능의 핵심 불변식이다.
 */
import { describe, it, expect } from "vitest";

import { clampToViewport, clampSize, avatarSrcFor } from "../components/FloatingAvatar";

describe("clampToViewport", () => {
  it("화면 안에 있으면 그대로 둔다", () => {
    expect(clampToViewport({ x: 100, y: 200 }, 120, 1440, 900)).toEqual({ x: 100, y: 200 });
  });

  it("오른쪽·아래로 벗어나면 끌어들인다 — 창을 줄였을 때 캐릭터를 잃지 않아야 한다", () => {
    // 1440x900에서 정한 자리를 800x600 창에서 그대로 쓰면 화면 밖이다.
    const pos = clampToViewport({ x: 1300, y: 760 }, 120, 800, 600);
    expect(pos.x).toBe(800 - 120 - 8);
    expect(pos.y).toBe(600 - 120 - 8);
  });

  it("왼쪽·위로 벗어나도 여백만큼 남긴다", () => {
    expect(clampToViewport({ x: -50, y: -50 }, 120, 1440, 900)).toEqual({ x: 8, y: 8 });
  });

  it("캐릭터가 화면보다 크면 최소 여백 위치로 고정한다(음수 좌표 금지)", () => {
    const pos = clampToViewport({ x: 500, y: 500 }, 300, 200, 200);
    expect(pos.x).toBeGreaterThanOrEqual(0);
    expect(pos.y).toBeGreaterThanOrEqual(0);
  });

  it("크기가 커지면 허용 범위도 함께 줄어든다", () => {
    const small = clampToViewport({ x: 9999, y: 0 }, 60, 1000, 1000);
    const large = clampToViewport({ x: 9999, y: 0 }, 300, 1000, 1000);
    expect(large.x).toBeLessThan(small.x);
  });
});

describe("clampSize", () => {
  it("60~300 범위로 가둔다 (store의 저장 검증 범위와 동일)", () => {
    expect(clampSize(10)).toBe(60);
    expect(clampSize(1000)).toBe(300);
    expect(clampSize(150)).toBe(150);
  });

  it("소수점은 정수로 만든다 — 픽셀 좌표가 흔들리지 않게", () => {
    expect(clampSize(120.6)).toBe(121);
  });
});

describe("avatarSrcFor", () => {
  it("감정 이름으로 png 경로를 만든다", () => {
    expect(avatarSrcFor("thinking")).toContain("avatars/thinking.png");
  });

  it("uploading은 webm도 만들 수 있다 (유일한 영상 상태)", () => {
    expect(avatarSrcFor("uploading", "webm")).toContain("avatars/uploading.webm");
  });
});
