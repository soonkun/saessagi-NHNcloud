// STT: push-to-talk + 2s silence auto-stop → WAV → POST /asr

const ASR_URL = "http://127.0.0.1:12393/asr";
const SILENCE_MS = 2000;
const SILENCE_THRESHOLD = 10; // 0-255 average frequency energy
const SAMPLE_RATE = 16000;

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
      const wav = await toWav(blob);
      const form = new FormData();
      form.append("file", wav, "voice.wav");
      const res = await fetch(ASR_URL, { method: "POST", body: form });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await (res.json() as Promise<{ text?: string }>);
      const text = (json.text ?? "").trim();
      if (text) _cb?.onText(text);
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

async function toWav(blob: Blob): Promise<Blob> {
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

  return new Blob([wavBuf], { type: "audio/wav" });
}
