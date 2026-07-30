// STT: push-to-talk + 2s silence auto-stop → WAV → POST /asr

import { API_BASE } from "./api";

// API_BASE는 Electron에서 절대 주소, 브라우저에서 빈 문자열(=현재 origin 상대경로)이다.
// 브라우저에서 127.0.0.1로 박아두면 다른 PC에서 열었을 때 그 PC를 가리킨다 (CR-38).
const ASR_URL = `${API_BASE}/asr`;
const SILENCE_MS = 2000;
const SILENCE_THRESHOLD = 10; // 0-255 average frequency energy
const SAMPLE_RATE = 16000;
/** 이 실효값 미만이면 말이 없었던 것으로 본다(무음 환각 방지). */
const SILENCE_RMS = 0.004;

/**
 * Whisper가 무음·잡음에서 지어내는 상투 문구.
 * 학습 데이터(자막)에 워낙 흔해서, 들린 게 없으면 이런 문장이 튀어나온다.
 * 사용자가 실제로 이 말을 할 가능성은 거의 없고, 잘못 보내면 대화가 오염된다.
 */
const HALLUCINATION_RE =
  /^[\s.,!?]*(다음 영상에서 만나요|시청해 주셔서 감사합니다|구독과 좋아요|감사합니다)[\s.,!?~]*$/;

export function isHallucination(text: string): boolean {
  return HALLUCINATION_RE.test(text.trim());
}

export type VoiceCallbacks = {
  onStart: () => void;
  onStop: () => void;
  onText: (text: string) => void;
  onError: (msg: string) => void;
};

let _active = false;
let _stream: MediaStream | null = null;
let _recorder: MediaRecorder | null = null;
let _silenceTimer: ReturnType<typeof setTimeout> | null = null;
let _rafId: number | null = null;
let _cb: VoiceCallbacks | null = null;

export function isVoiceActive(): boolean {
  return _active;
}

export async function startVoice(cb: VoiceCallbacks): Promise<void> {
  if (_active) {
    stopVoice();
    return;
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: SAMPLE_RATE,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
  } catch {
    cb.onError("마이크 권한이 없습니다.");
    return;
  }

  _active = true;
  _stream = stream;
  _cb = cb;
  cb.onStart();

  // Silence detection via AnalyserNode
  const monitorCtx = new AudioContext();
  const srcNode = monitorCtx.createMediaStreamSource(stream);
  const analyser = monitorCtx.createAnalyser();
  analyser.fftSize = 256;
  srcNode.connect(analyser);
  const freqBuf = new Uint8Array(analyser.frequencyBinCount);

  function tick(): void {
    if (!_active) return;
    analyser.getByteFrequencyData(freqBuf);
    const avg = freqBuf.reduce((a, b) => a + b, 0) / freqBuf.length;
    if (avg < SILENCE_THRESHOLD) {
      if (!_silenceTimer) {
        _silenceTimer = setTimeout(() => { stopVoice(); }, SILENCE_MS);
      }
    } else {
      if (_silenceTimer) { clearTimeout(_silenceTimer); _silenceTimer = null; }
    }
    _rafId = requestAnimationFrame(tick);
  }
  _rafId = requestAnimationFrame(tick);

  // MediaRecorder
  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : "audio/webm";
  const chunks: Blob[] = [];
  _recorder = new MediaRecorder(stream, { mimeType });

  _recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };

  _recorder.onstop = async () => {
    if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
    if (_silenceTimer) { clearTimeout(_silenceTimer); _silenceTimer = null; }
    void monitorCtx.close();

    try {
      const blob = new Blob(chunks, { type: mimeType });
      const { wav, rms } = await toWav(blob);

      // 사실상 무음이면 보내지 않는다.
      // Whisper는 무음을 받으면 학습 데이터에 흔한 문구("다음 영상에서 만나요." 등)를
      // 지어낸다. 그 결과가 그대로 대화로 전송되므로, 말하지 않고 마이크를 껐을 때
      // 엉뚱한 메시지가 나가 버린다. 실제로 무음 WAV에서 재현된다.
      if (rms < SILENCE_RMS) {
        _cb?.onError("소리가 들리지 않았어요. 다시 말씀해 주세요.");
        _cb?.onStop();
        _cb = null;
        return;
      }

      const form = new FormData();
      form.append("file", wav, "voice.wav");
      const res = await fetch(ASR_URL, { method: "POST", body: form });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await (res.json() as Promise<{ text?: string }>);
      const text = (json.text ?? "").trim();
      if (text && !isHallucination(text)) _cb?.onText(text);
      else if (text) _cb?.onError("소리가 잘 들리지 않았어요. 다시 말씀해 주세요.");
    } catch (e) {
      _cb?.onError(`음성 인식 오류: ${String(e)}`);
    }

    _cb?.onStop();
    _cb = null;
  };

  _recorder.start();
}

export function stopVoice(): void {
  if (!_active) return;
  _active = false;
  if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
  if (_silenceTimer) { clearTimeout(_silenceTimer); _silenceTimer = null; }
  if (_recorder?.state === "recording") _recorder.stop();
  _stream?.getTracks().forEach((t) => t.stop());
  _stream = null;
}

async function toWav(blob: Blob): Promise<{ wav: Blob; rms: number }> {
  const raw = await blob.arrayBuffer();
  const decodeCtx = new AudioContext();
  const decoded = await decodeCtx.decodeAudioData(raw);
  await decodeCtx.close();

  const len = Math.ceil(decoded.duration * SAMPLE_RATE);
  const offCtx = new OfflineAudioContext(1, len, SAMPLE_RATE);
  const src = offCtx.createBufferSource();
  src.buffer = decoded;
  src.connect(offCtx.destination);
  src.start();
  const rendered = await offCtx.startRendering();

  const pcm = rendered.getChannelData(0);
  const int16 = new Int16Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) {
    int16[i] = Math.max(-32768, Math.min(32767, Math.round(pcm[i] * 32767)));
  }

  const wavBuf = new ArrayBuffer(44 + int16.byteLength);
  const dv = new DataView(wavBuf);
  function ws(o: number, s: string): void {
    for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i));
  }
  ws(0, "RIFF"); dv.setUint32(4, 36 + int16.byteLength, true);
  ws(8, "WAVE"); ws(12, "fmt ");
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); // PCM
  dv.setUint16(22, 1, true); dv.setUint32(24, SAMPLE_RATE, true);
  dv.setUint32(28, SAMPLE_RATE * 2, true); dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true); ws(36, "data");
  dv.setUint32(40, int16.byteLength, true);
  new Int16Array(wavBuf, 44).set(int16);

  // 실효값(RMS) — 사실상 무음인지 판별하는 데 쓴다.
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
  const rms = pcm.length > 0 ? Math.sqrt(sum / pcm.length) : 0;

  return { wav: new Blob([wavBuf], { type: "audio/wav" }), rms };
}
