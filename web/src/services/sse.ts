// SSE(text/event-stream) fetch 응답 리더 — MeetingView·DeepResearchView 공용.
// "data: {json}\n\n" 프레임을 파싱해 onEvent 콜백으로 전달한다.

export async function readSseStream<T = Record<string, unknown>>(
  res: Response,
  onEvent: (evt: T) => void,
): Promise<void> {
  if (!res.body) throw new Error("응답 스트림이 없습니다.");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      let evt: T;
      try {
        evt = JSON.parse(line.slice(5).trim()) as T;
      } catch {
        continue; // JSON 파싱 실패만 무시
      }
      onEvent(evt); // 콜백 에러는 호출자로 전파
    }
  }
}
