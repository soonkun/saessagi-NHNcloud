// TTS 통합 유틸 — 설정된 엔진(melo/system)에 따라 자동 라우팅
import { useStore } from "../store";
import { speakLocal } from "./speech";
import { API_BASE } from "./api";

export async function speakMeloTTS(text: string, attempt = 0): Promise<void> {
  try {
    const res = await fetch(API_BASE + "/api/tts/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (res.status === 503 && attempt < 5) {
      setTimeout(() => { void speakMeloTTS(text, attempt + 1); }, 2000);
      return;
    }
    if (!res.ok) return;
    const { audio } = (await res.json()) as { audio: string; format: string };
    if (!audio) return;

    const bin = atob(audio);
    const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);

    const ctx = new AudioContext();
    const decoded = await ctx.decodeAudioData(buf.buffer);
    const src = ctx.createBufferSource();
    src.buffer = decoded;
    src.playbackRate.value = useStore.getState().ttsRate;
    src.connect(ctx.destination);
    src.start();
  } catch (e) {
    console.warn("[TTS] MeloTTS 재생 실패:", e);
  }
}

export async function speak(text: string): Promise<void> {
  const engine = useStore.getState().ttsEngine;
  if (engine === "system") {
    speakLocal(text);
  } else {
    await speakMeloTTS(text);
  }
}
