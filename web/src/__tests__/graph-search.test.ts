// E-107 지식그래프 과제 검색 — 응답 형태 불일치로 화이트스크린이 났다.
import { describe, expect, it, vi, afterEach } from "vitest";
import { searchGraphDocs } from "../services/api";

function mockFetch(body: unknown): void {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true,
    json: async () => body,
  })) as unknown as typeof fetch);
}

afterEach(() => vi.unstubAllGlobals());

describe("searchGraphDocs", () => {
  it("M_23 응답을 그대로 돌려준다", async () => {
    // CR-61에서 바뀐 실제 형태 — title_match·matched_keywords는 없다
    mockFetch({
      docs: [
        {
          doc_id: "TRKO1.pdf_abc",
          title: "기후변화 대응 고온성 버섯자원 발굴",
          doc_name: "TRKO1.pdf",
          year: 2020,
          document_type: "FINAL_REPORT",
          score: 2.0,
        },
      ],
    });
    const got = await searchGraphDocs("기후변화");
    expect(got).toHaveLength(1);
    expect(got[0].score).toBe(2);
  });

  it("docs가 없어도 빈 배열 — 화면이 하얘지면 안 된다", async () => {
    mockFetch({});
    await expect(searchGraphDocs("기후")).resolves.toEqual([]);
  });

  it("docs가 배열이 아니어도 빈 배열", async () => {
    mockFetch({ docs: null });
    await expect(searchGraphDocs("기후")).resolves.toEqual([]);
  });
});
