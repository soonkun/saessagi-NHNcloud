/**
 * CR-38 회귀 방지 — 브라우저(비-Electron) 실행 시의 기본값.
 *
 * 여기서 검증하는 두 가지가 깨지면 "다른 PC에서 열면 아무것도 안 되는" 증상이 난다:
 *  1) 창 모드로 떠야 한다. 펫 모드로 뜨면 빈 페이지에 캐릭터만 남고 조작이 안 된다.
 *  2) WebSocket 주소를 현재 접속 주소에서 유도해야 한다. 127.0.0.1로 박히면
 *     접속한 PC 자신을 가리켜 연결이 실패한다.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

async function freshStore() {
  vi.resetModules();
  return await import("../store");
}

describe("CR-38 브라우저 모드 기본값", () => {
  beforeEach(() => {
    localStorage.clear();
    // 브라우저 환경 — Electron 브리지가 없다
    delete (window as { electronAPI?: unknown }).electronAPI;
  });

  it("Electron이 없으면 창(데스크탑) 모드로 뜬다", async () => {
    const { useStore } = await freshStore();
    expect(useStore.getState().windowMode).toBe("window");
  });

  it("저장값이 pet이어도 브라우저에서는 창 모드를 강제한다", async () => {
    localStorage.setItem("saessagi_window_mode", "pet");
    const { useStore } = await freshStore();
    expect(useStore.getState().windowMode).toBe("window");
  });

  it("WebSocket 주소를 현재 origin에서 유도한다 (127.0.0.1 하드코딩 금지)", async () => {
    const { useStore } = await freshStore();
    const url = useStore.getState().wsUrl;

    expect(url).toBe(`ws://${window.location.host}/client-ws`);
    expect(url).not.toContain("127.0.0.1:12393");
  });

  it("사용자가 저장한 WebSocket 주소는 존중한다", async () => {
    localStorage.setItem("saessagi_ws_url", "ws://10.0.0.5:9999/client-ws");
    const { useStore } = await freshStore();
    expect(useStore.getState().wsUrl).toBe("ws://10.0.0.5:9999/client-ws");
  });

  it("isElectronRuntime()은 브라우저에서 false", async () => {
    const { isElectronRuntime } = await freshStore();
    expect(isElectronRuntime()).toBe(false);
  });

  it("글씨 크기는 데스크탑 값 하나만 저장한다", async () => {
    const { useStore } = await freshStore();
    useStore.getState().setUiScale(1.15);

    expect(localStorage.getItem("saessagi_ui_scale_desktop")).toBe("1.15");
    expect(localStorage.getItem("saessagi_ui_scale_pet")).toBeNull();
    expect(useStore.getState().uiScaleDesktop).toBe(1.15);
  });
});
