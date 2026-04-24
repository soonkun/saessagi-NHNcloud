// 로컬 메시지(시작 인사, 알림)용 Web Speech API TTS
// MeloTTS 파이프라인 없이 시스템 한국어 음성으로 즉시 재생

import { useStore } from "../store";

let voicesReady = false;

// 음성 목록 사전 로드 (첫 발화 전 준비)
if (typeof window !== "undefined" && window.speechSynthesis) {
  const preload = () => {
    if (window.speechSynthesis.getVoices().length > 0) {
      voicesReady = true;
    }
  };
  preload();
  window.speechSynthesis.addEventListener("voiceschanged", () => {
    voicesReady = true;
  });
}

// 저장된 이름에서 핵심 키워드 추출 (괄호·공백 전까지)
// "Shelley (한국어(대한민국))" → "Shelley"
function _baseName(name: string): string {
  return name.split(/[\s(]/)[0].trim();
}

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis?.getVoices() ?? [];
  const stored = useStore.getState().ttsVoiceName;

  if (stored) {
    // 1순위: 정확한 이름 매칭
    const exact = voices.find((v) => v.name === stored);
    if (exact) return exact;

    // 2순위: 핵심 이름이 포함된 음성 (예: "Shelley" ⊂ "Shelley (한국어(대한민국))")
    const base = _baseName(stored);
    if (base) {
      const partial = voices.find(
        (v) => v.name.includes(base) && v.lang.startsWith("ko")
      );
      if (partial) return partial;

      // 3순위: 언어 무관 부분 매칭
      const anyLang = voices.find((v) => v.name.includes(base));
      if (anyLang) return anyLang;
    }
  }

  // 폴백: Shelley → Yuna 순으로 한국어 여성 음성 탐색
  return (
    voices.find((v) => v.lang.startsWith("ko") && /shelley/i.test(v.name)) ??
    voices.find((v) => v.lang === "ko-KR" && /yuna|여성|female|woman/i.test(v.name)) ??
    voices.find((v) => v.lang === "ko-KR") ??
    voices.find((v) => v.lang.startsWith("ko")) ??
    null
  );
}

function doSpeak(text: string): void {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text);
  utt.lang = "ko-KR";
  utt.rate = useStore.getState().ttsRate;
  const voice = pickVoice();
  if (voice) utt.voice = voice;
  window.speechSynthesis.speak(utt);
}

export function speakLocal(text: string): void {
  if (!window.speechSynthesis) return;

  if (voicesReady) {
    doSpeak(text);
  } else {
    const handler = () => {
      window.speechSynthesis.removeEventListener("voiceschanged", handler);
      doSpeak(text);
    };
    window.speechSynthesis.addEventListener("voiceschanged", handler);
    // 500ms 타임아웃 폴백
    setTimeout(() => {
      window.speechSynthesis.removeEventListener("voiceschanged", handler);
      doSpeak(text);
    }, 500);
  }
}
