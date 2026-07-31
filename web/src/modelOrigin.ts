// web/src/modelOrigin.ts
/**
 * 모델 이름 → 만든 나라·회사 (CR-50, 사용자 요청).
 *
 * 설정에서 모델을 고를 때 "gemma4:31b" 같은 이름만으로는 어느 나라 어느 회사가 만든
 * 모델인지 알 수 없다. 사내 자료를 다루는 시스템이라 이 정보는 고르는 기준이 된다.
 *
 * 로고 이미지는 쓰지 않는다 — 이 앱은 오프라인 전제이고 외부에서 이미지를 받아올 수
 * 없으며, 회사 로고를 임의로 그려 넣는 것은 상표 문제도 있다. 국기 이모지와 회사 이름을
 * 함께 적는 것으로 목적(어느 나라·어느 회사인지)을 충족한다.
 *
 * `<option>`은 마크업을 넣을 수 없어 텍스트로만 표시된다 — 이모지는 텍스트라 문제없다.
 */

export interface ModelOrigin {
  /** 국기 이모지. 모르는 모델은 빈 문자열. */
  flag: string;
  /** 만든 곳. 모르면 빈 문자열. */
  vendor: string;
}

/**
 * 이름 접두사 → 출처. **긴 접두사를 먼저** 둔다 — "gpt-oss"가 "gpt"보다 앞에 있어야
 * OpenAI의 오픈웨이트 모델이 API 모델과 뒤섞이지 않는다.
 */
const ORIGINS: { prefix: string; flag: string; vendor: string }[] = [
  // 미국
  { prefix: "gpt-oss", flag: "🇺🇸", vendor: "OpenAI" },
  { prefix: "gpt-", flag: "🇺🇸", vendor: "OpenAI" },
  { prefix: "o1", flag: "🇺🇸", vendor: "OpenAI" },
  { prefix: "o3", flag: "🇺🇸", vendor: "OpenAI" },
  { prefix: "gemma", flag: "🇺🇸", vendor: "Google" },
  { prefix: "codegemma", flag: "🇺🇸", vendor: "Google" },
  { prefix: "llama", flag: "🇺🇸", vendor: "Meta" },
  { prefix: "codellama", flag: "🇺🇸", vendor: "Meta" },
  { prefix: "granite", flag: "🇺🇸", vendor: "IBM" },
  { prefix: "nemotron", flag: "🇺🇸", vendor: "NVIDIA" },
  { prefix: "phi", flag: "🇺🇸", vendor: "Microsoft" },
  { prefix: "olmo", flag: "🇺🇸", vendor: "AI2" },
  { prefix: "claude", flag: "🇺🇸", vendor: "Anthropic" },
  // 유럽
  { prefix: "mistral", flag: "🇫🇷", vendor: "Mistral AI" },
  { prefix: "magistral", flag: "🇫🇷", vendor: "Mistral AI" },
  { prefix: "devstral", flag: "🇫🇷", vendor: "Mistral AI" },
  { prefix: "codestral", flag: "🇫🇷", vendor: "Mistral AI" },
  { prefix: "ministral", flag: "🇫🇷", vendor: "Mistral AI" },
  { prefix: "pixtral", flag: "🇫🇷", vendor: "Mistral AI" },
  { prefix: "mixtral", flag: "🇫🇷", vendor: "Mistral AI" },
  // 캐나다
  { prefix: "command-r", flag: "🇨🇦", vendor: "Cohere" },
  { prefix: "command", flag: "🇨🇦", vendor: "Cohere" },
  { prefix: "aya", flag: "🇨🇦", vendor: "Cohere" },
  // 한국
  { prefix: "exaone", flag: "🇰🇷", vendor: "LG AI연구원" },
  { prefix: "k-exaone", flag: "🇰🇷", vendor: "LG AI연구원" },
  { prefix: "solar", flag: "🇰🇷", vendor: "Upstage" },
  { prefix: "a.x", flag: "🇰🇷", vendor: "SK텔레콤" },
  // 중국
  { prefix: "qwen", flag: "🇨🇳", vendor: "Alibaba" },
  { prefix: "deepseek", flag: "🇨🇳", vendor: "DeepSeek" },
  { prefix: "glm", flag: "🇨🇳", vendor: "Zhipu AI" },
  { prefix: "chatglm", flag: "🇨🇳", vendor: "Zhipu AI" },
  { prefix: "kimi", flag: "🇨🇳", vendor: "Moonshot AI" },
  { prefix: "yi", flag: "🇨🇳", vendor: "01.AI" },
  { prefix: "bge", flag: "🇨🇳", vendor: "BAAI" },
  { prefix: "minicpm", flag: "🇨🇳", vendor: "OpenBMB" },
  // 그 외
  { prefix: "falcon", flag: "🇦🇪", vendor: "TII" },
  { prefix: "smollm", flag: "🇫🇷", vendor: "Hugging Face" },
  { prefix: "zephyr", flag: "🇫🇷", vendor: "Hugging Face" },
];

/** 모델 이름에서 만든 나라·회사를 찾는다. 모르는 모델이면 빈 값. */
export function modelOrigin(name: string): ModelOrigin {
  const n = (name ?? "").toLowerCase().trim();
  if (!n) return { flag: "", vendor: "" };
  for (const o of ORIGINS) {
    if (n.startsWith(o.prefix)) return { flag: o.flag, vendor: o.vendor };
  }
  return { flag: "", vendor: "" };
}

/**
 * 드롭다운 한 줄. `<option>`은 마크업을 못 넣으므로 텍스트로 합친다.
 * 모르는 모델은 이름만 보여준다 — "❓ 알 수 없음" 같은 표시는 목록만 어지럽힌다.
 */
export function modelOptionLabel(name: string, suffix = ""): string {
  const { flag, vendor } = modelOrigin(name);
  const head = flag && vendor ? `${flag} ${vendor} · ` : "";
  return `${head}${name}${suffix}`;
}
