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
  /** 나라 이름(한국어). 목록 정렬 기준이라 이모지와 별도로 갖고 있어야 한다. */
  country: string;
}

/**
 * 이름 접두사 → 출처. **긴 접두사를 먼저** 둔다 — "gpt-oss"가 "gpt"보다 앞에 있어야
 * OpenAI의 오픈웨이트 모델이 API 모델과 뒤섞이지 않는다.
 */
const ORIGINS: { prefix: string; flag: string; country: string; vendor: string }[] = [
  // 미국
  { prefix: "gpt-oss", flag: "🇺🇸", country: "미국", vendor: "OpenAI" },
  { prefix: "gpt-", flag: "🇺🇸", country: "미국", vendor: "OpenAI" },
  { prefix: "o1", flag: "🇺🇸", country: "미국", vendor: "OpenAI" },
  { prefix: "o3", flag: "🇺🇸", country: "미국", vendor: "OpenAI" },
  { prefix: "gemma", flag: "🇺🇸", country: "미국", vendor: "Google" },
  { prefix: "codegemma", flag: "🇺🇸", country: "미국", vendor: "Google" },
  { prefix: "llama", flag: "🇺🇸", country: "미국", vendor: "Meta" },
  { prefix: "codellama", flag: "🇺🇸", country: "미국", vendor: "Meta" },
  { prefix: "granite", flag: "🇺🇸", country: "미국", vendor: "IBM" },
  { prefix: "nemotron", flag: "🇺🇸", country: "미국", vendor: "NVIDIA" },
  { prefix: "phi", flag: "🇺🇸", country: "미국", vendor: "Microsoft" },
  { prefix: "olmo", flag: "🇺🇸", country: "미국", vendor: "AI2" },
  { prefix: "claude", flag: "🇺🇸", country: "미국", vendor: "Anthropic" },
  // 유럽
  { prefix: "mistral", flag: "🇫🇷", country: "프랑스", vendor: "Mistral AI" },
  { prefix: "magistral", flag: "🇫🇷", country: "프랑스", vendor: "Mistral AI" },
  { prefix: "devstral", flag: "🇫🇷", country: "프랑스", vendor: "Mistral AI" },
  { prefix: "codestral", flag: "🇫🇷", country: "프랑스", vendor: "Mistral AI" },
  { prefix: "ministral", flag: "🇫🇷", country: "프랑스", vendor: "Mistral AI" },
  { prefix: "pixtral", flag: "🇫🇷", country: "프랑스", vendor: "Mistral AI" },
  { prefix: "mixtral", flag: "🇫🇷", country: "프랑스", vendor: "Mistral AI" },
  // 캐나다
  { prefix: "command-r", flag: "🇨🇦", country: "캐나다", vendor: "Cohere" },
  { prefix: "command", flag: "🇨🇦", country: "캐나다", vendor: "Cohere" },
  { prefix: "aya", flag: "🇨🇦", country: "캐나다", vendor: "Cohere" },
  // 한국
  { prefix: "exaone", flag: "🇰🇷", country: "대한민국", vendor: "LG AI연구원" },
  { prefix: "k-exaone", flag: "🇰🇷", country: "대한민국", vendor: "LG AI연구원" },
  { prefix: "solar", flag: "🇰🇷", country: "대한민국", vendor: "Upstage" },
  { prefix: "a.x", flag: "🇰🇷", country: "대한민국", vendor: "SK텔레콤" },
  // 중국
  { prefix: "qwen", flag: "🇨🇳", country: "중국", vendor: "Alibaba" },
  { prefix: "deepseek", flag: "🇨🇳", country: "중국", vendor: "DeepSeek" },
  { prefix: "glm", flag: "🇨🇳", country: "중국", vendor: "Zhipu AI" },
  { prefix: "chatglm", flag: "🇨🇳", country: "중국", vendor: "Zhipu AI" },
  { prefix: "kimi", flag: "🇨🇳", country: "중국", vendor: "Moonshot AI" },
  { prefix: "yi", flag: "🇨🇳", country: "중국", vendor: "01.AI" },
  { prefix: "bge", flag: "🇨🇳", country: "중국", vendor: "BAAI" },
  { prefix: "minicpm", flag: "🇨🇳", country: "중국", vendor: "OpenBMB" },
  // 그 외
  { prefix: "falcon", flag: "🇦🇪", country: "아랍에미리트", vendor: "TII" },
  { prefix: "smollm", flag: "🇫🇷", country: "프랑스", vendor: "Hugging Face" },
  { prefix: "zephyr", flag: "🇫🇷", country: "프랑스", vendor: "Hugging Face" },
];

/** 모델 이름에서 만든 나라·회사를 찾는다. 모르는 모델이면 빈 값. */
export function modelOrigin(name: string): ModelOrigin {
  const n = (name ?? "").toLowerCase().trim();
  if (!n) return { flag: "", vendor: "", country: "" };
  for (const o of ORIGINS) {
    if (n.startsWith(o.prefix))
      return { flag: o.flag, vendor: o.vendor, country: o.country };
  }
  return { flag: "", vendor: "", country: "" };
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

/**
 * 모델 이름에서 파라미터 규모(단위 B)를 뽑는다. 못 찾으면 `null`.
 *
 * 태그 표기가 제각각이라 문자열 정렬로는 크기 순서가 나오지 않는다 —
 * `"8b" > "30b"`, `"120b" < "26b"`처럼 뒤집힌다. 숫자로 바꿔서 비교해야 한다.
 *
 * 다루는 표기:
 *   `gemma4:26b` → 26 · `granite4.1:8b` → 8 · `gpt-oss:120b` → 120
 *   `qwen3:1.5b` → 1.5 · `gemma4:e4b` → 4 (나노 계열의 유효 파라미터)
 *   `llama4:16x17b` → 17 (MoE는 전문가 하나 크기 기준 — 표기에서 총량을 알 수 없다)
 *   `granite4.1:latest`처럼 크기가 없으면 null
 */
export function modelParamSize(name: string): number | null {
  const tag = (name ?? "").toLowerCase().split(":").slice(1).join(":") || name.toLowerCase();
  // 마지막에 오는 "<숫자>b"를 찾는다. 16x17b는 17b가 잡힌다.
  const m = tag.match(/(\d+(?:\.\d+)?)\s*b(?![a-z0-9])/);
  return m ? parseFloat(m[1]) : null;
}

/**
 * 목록 정렬 순서: **국가명 → 회사명 → 파라미터 크기(작은 것부터) → 이름** (CR-50 후속).
 *
 * 출처를 모르는 모델은 맨 뒤로 보낸다 — 아는 것들 사이에 끼면 분류가 흐트러진다.
 * 크기를 모르는 태그(`:latest` 등)는 같은 회사 안에서 뒤로 보낸다.
 */
export function compareModels(a: string, b: string): number {
  const oa = modelOrigin(a);
  const ob = modelOrigin(b);

  const knownA = !!oa.vendor;
  const knownB = !!ob.vendor;
  if (knownA !== knownB) return knownA ? -1 : 1;

  if (knownA) {
    const byCountry = oa.country.localeCompare(ob.country, "ko");
    if (byCountry !== 0) return byCountry;
    const byVendor = oa.vendor.localeCompare(ob.vendor, "ko");
    if (byVendor !== 0) return byVendor;
  }

  const sa = modelParamSize(a);
  const sb = modelParamSize(b);
  if (sa !== sb) {
    if (sa === null) return 1; // 크기 미상은 뒤로
    if (sb === null) return -1;
    return sa - sb;
  }
  return a.localeCompare(b, "ko");
}

/** 위 순서로 정렬한 새 배열. 원본은 건드리지 않는다. */
export function sortModels(names: string[]): string[] {
  return [...names].sort(compareModels);
}
