// 보고서·노트 본문 정리와 인쇄(PDF 저장) 공용 코드 (CR-59).
//
// 딥 리서치 보고서와 그것을 옮겨 담은 업무 노트는 같은 글이다. 정리 규칙과 인쇄 규격이
// 두 화면에서 갈리면 "노트로 옮겼더니 모양이 달라졌다"가 된다 — 한 곳에 둔다.

// ── 1. 본문 정리 ─────────────────────────────────────────────────────────────

/**
 * LLM이 습관적으로 끼워 넣는 LaTeX 기호. 화면에는 `$\rightarrow$`가 그대로 찍힌다
 * (우리는 수식 렌더러를 쓰지 않는다). 사내 보고서에 수식이 필요한 경우는 없으므로
 * 흔한 것들은 유니코드 기호로 바꾸고, 나머지는 `$` 껍데기만 벗긴다.
 */
const LATEX_SYMBOLS: Record<string, string> = {
  rightarrow: "→",
  to: "→",
  longrightarrow: "→",
  Rightarrow: "⇒",
  leftarrow: "←",
  Leftarrow: "⇐",
  leftrightarrow: "↔",
  uparrow: "↑",
  downarrow: "↓",
  times: "×",
  div: "÷",
  pm: "±",
  cdot: "·",
  bullet: "·",
  le: "≤",
  leq: "≤",
  ge: "≥",
  geq: "≥",
  ne: "≠",
  neq: "≠",
  approx: "≈",
  sim: "~",
  infty: "∞",
  alpha: "α",
  beta: "β",
  gamma: "γ",
  delta: "δ",
  Delta: "Δ",
  sigma: "σ",
  mu: "μ",
  degree: "°",
  circ: "°",
  percent: "%",
  ldots: "…",
  dots: "…",
};

/** `\rightarrow` → `→` (달러 기호 안이든 밖이든). */
function replaceLatexCommands(text: string): string {
  return text.replace(/\\([A-Za-z]+)/g, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(LATEX_SYMBOLS, name) ? LATEX_SYMBOLS[name] : whole
  );
}

/**
 * 인라인 수식 껍데기를 벗긴다.
 *
 * **안에 LaTeX 명령(`\rightarrow` 같은 백슬래시 표기)이 있을 때만** 벗긴다.
 * 이 규칙이 없으면 본문의 금액 표기가 망가진다 — "예산 $100 상당의 장비와 $200
 * 상당의 소모품"에서 `$100 상당의 장비와 $`가 수식으로 잡혀 통째로 사라진다
 * (테스트로 실제 확인). 우리가 고치려는 것은 LLM이 끼워 넣는 수식 표기뿐이다.
 *
 * 여는 `$`와 닫는 `$` 사이에 줄바꿈이 있으면 수식이 아니라고 본다.
 */
function stripInlineMath(text: string): string {
  return text.replace(/\$([^$\n]{1,60})\$/g, (whole, inner: string) => {
    if (!/\\[A-Za-z]/.test(inner)) return whole; // LaTeX 명령이 없으면 수식이 아니다
    const cleaned = replaceLatexCommands(inner).trim();
    // 우리가 모르는 명령이 남았다면 뜻이 바뀔 수 있으니 손대지 않는다.
    if (/\\[A-Za-z]/.test(cleaned)) return whole;
    return cleaned;
  });
}

/**
 * `**'따옴표로 시작하는 강조'**`가 굵게 안 되고 별표가 그대로 보이는 문제를 고친다.
 *
 * 마크다운 규칙상 닫는 `**`는 앞이 문장부호(`'`)이고 뒤가 한글이면 "닫는 표시"로
 * 인정되지 않는다. 영어에서는 뒤에 공백이 오므로 잘 드러나지 않지만, 조사가 바로
 * 붙는 한국어에서는 거의 항상 터진다(`**'기후변화'**이라는` → 별표가 그대로 노출).
 *
 * 문장부호를 강조 밖으로 빼면 규칙을 만족한다: `'**기후변화**'이라는`.
 */
function fixCjkEmphasis(text: string): string {
  return text.replace(
    /\*\*(\s*)([([{'"“‘]*)([^*\n]+?)([)\]}'"”’]*)(\s*)\*\*/g,
    (whole, lead: string, open: string, body: string, close: string, tail: string) => {
      // 글자·숫자가 없는 강조(`**''**` 같은 것)는 옮길 알맹이가 없다.
      if (!/[\p{L}\p{N}]/u.test(body)) return whole;
      if (!open && !close) return whole; // 멀쩡한 강조는 건드리지 않는다
      return `${lead}${open}**${body}**${close}${tail}`;
    }
  );
}

/**
 * 보고서 본문을 화면·PDF에 내보내기 좋게 정리한다.
 *
 * 원문을 고치는 것이 아니라 **표시 직전에** 다듬는 용도다. 저장하는 쪽(노트로 옮기기,
 * MD 내려받기)에서도 같은 함수를 통과시켜, 화면과 파일이 어긋나지 않게 한다.
 */
export function cleanReportMarkdown(text: string): string {
  if (!text) return "";
  let out = stripInlineMath(text);
  out = replaceLatexCommands(out);
  out = fixCjkEmphasis(out);
  // 이스케이프된 문장부호(`\%`, `\_`)는 그대로 두면 백슬래시가 보인다.
  out = out.replace(/\\([%_&#])/g, "$1");
  return out;
}

// ── 2. 인쇄(PDF 저장) ────────────────────────────────────────────────────────

/** 인쇄 문서에 넣을 텍스트 이스케이프 — 제목·요청 문구에 <, & 가 있어도 안 깨지게. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * 인쇄 문서 스타일. 앱 CSS와 완전히 분리된 독립 문서라 여기에 다 적는다.
 * 화면 테마(어두운 배경 등)를 끌고 오면 종이에서 읽을 수 없으므로 흑백으로 고정한다.
 */
const PRINT_CSS = `
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body {
  margin: 0;
  color: #000;
  background: #fff;
  font-family: "Noto Sans KR", "Malgun Gothic", "AppleGothic", sans-serif;
  font-size: 10.5pt;
  line-height: 1.65;
}
.doc-title { font-size: 16pt; margin: 0 0 2mm; }
.doc-meta {
  font-size: 9pt;
  color: #333;
  border-bottom: 1px solid #000;
  padding-bottom: 2mm;
  margin-bottom: 5mm;
}
h1, h2, h3, h4 { page-break-after: avoid; margin: 4mm 0 2mm; line-height: 1.35; }
h1 { font-size: 14pt; } h2 { font-size: 12.5pt; } h3 { font-size: 11pt; }
p, li { orphans: 2; widows: 2; }
ul, ol { padding-left: 6mm; margin: 2mm 0; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0; font-size: 9.5pt; }
th, td { border: 1px solid #666; padding: 1.5mm 2mm; vertical-align: top; word-break: break-word; }
th { background: #eee; font-weight: 700; }
tr { page-break-inside: avoid; }
/* 화면에서는 표를 가로 스크롤 상자에 담지만 종이에서는 잘리면 안 된다 */
.md-table-wrap, .md-body div { overflow: visible !important; }
code, pre { font-family: "D2Coding", Consolas, monospace; font-size: 9pt; }
pre { background: #f4f4f4; padding: 2mm; page-break-inside: avoid; white-space: pre-wrap; }
blockquote { margin: 2mm 0; padding-left: 3mm; border-left: 2px solid #999; color: #333; }
img { max-width: 100%; }
a { color: #000; text-decoration: none; }
`;

/**
 * 주어진 HTML만 담은 독립 문서를 만들어 인쇄 창을 띄운다 (→ "PDF로 저장").
 *
 * **현재 페이지를 그대로 인쇄하면 안 된다.** 앱은 화면을 꽉 채우는 고정 레이아웃
 * (`position: fixed` body, `overflow: hidden` 컨테이너)이라, 인쇄 CSS로 다른 요소를
 * 숨겨도 본문이 그 컨테이너에 잘려 **빈 페이지가 나온다**(실제로 그랬다).
 * 숨은 iframe에 본문만 담은 문서를 새로 만들어 그것을 인쇄한다.
 *
 * @param title 문서 제목 — 인쇄 대화상자의 기본 파일 이름이 된다.
 * @param meta  제목 아래 한 줄 (날짜·모드 등). 이미 이스케이프된 HTML.
 * @param bodyHtml 본문 HTML (렌더된 마크다운을 그대로 복제해 넣는다).
 */
export function printHtmlDocument(title: string, meta: string, bodyHtml: string): void {
  const iframe = document.createElement("iframe");
  iframe.setAttribute("aria-hidden", "true");
  iframe.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  document.body.appendChild(iframe);

  const doc = iframe.contentDocument;
  const win = iframe.contentWindow;
  if (!doc || !win) {
    iframe.remove();
    return;
  }

  doc.open();
  doc.write(
    `<!doctype html><html lang="ko"><head><meta charset="utf-8">` +
      `<title>${escapeHtml(title)}</title><style>${PRINT_CSS}</style></head><body>` +
      `<h1 class="doc-title">${escapeHtml(title)}</h1>` +
      (meta ? `<div class="doc-meta">${meta}</div>` : "") +
      bodyHtml +
      `</body></html>`
  );
  doc.close();

  // 인쇄 대화상자가 닫힌 뒤에 지운다 — 먼저 지우면 인쇄가 취소된다.
  win.addEventListener("afterprint", () => {
    window.setTimeout(() => iframe.remove(), 500);
  });
  // 폰트·레이아웃이 자리를 잡은 뒤 띄운다.
  window.setTimeout(() => {
    win.focus();
    win.print();
  }, 120);
}

/** 파일 이름에 쓸 수 없는 문자를 정리한다. */
export function safeFileStem(text: string, max = 40): string {
  return (text || "문서")
    .replace(/[\\/:*?"<>|\n\r\t]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

// ── 딥 리서치 결과 제목 (CR-62) ──────────────────────────────────────────────

/** 모드 → 제목에 쓸 짧은 이름. 화면 라벨("과제 중복성 검토")은 목록에서 너무 길다. */
// CR-62: 모드 3개가 방(프로젝트)으로 바뀌었다. 시드 방 이름은 짧게 줄여 쓰고,
// 사용자가 만든 방은 이름을 그대로 쓴다 — 임의로 줄이면 오히려 못 알아본다.
export const RESEARCH_MODE_SHORT: Record<string, string> = {
  duplication: "중복성검토",
  discovery: "신규과제발굴",
  proposal: "계획서초안",
};

/**
 * 보고서 본문에서 과제명을 뽑는다.
 *
 * 딥 리서치 결과를 노트로 옮기면 제목이 전부
 * `[딥리서치·과제 중복성 검토] 첨부 기반`으로 똑같아져 목록에서 구분이 안 됐다
 * (파일만 첨부하고 프롬프트를 비우면 늘 "첨부 기반"이 된다 — 사용자 지적).
 * 보고서 첫머리에 과제명이 적혀 있으므로 그것을 쓴다.
 */
export function extractProjectTitle(report: string): string {
  if (!report) return "";
  // "**과제명**: ○○○" / "과제명 : ○○○" / "- 과제명: ○○○" 등 표기 흔들림을 흡수한다.
  const patterns = [
    /(?:^|\n)[\s*\-•○·]*\*{0,2}(?:신규\s*)?과제\s*명\*{0,2}\s*[:：]\s*(.+)/,
    /(?:^|\n)[\s*\-•○·]*\*{0,2}연구\s*과제\s*명\*{0,2}\s*[:：]\s*(.+)/,
    /(?:^|\n)[\s*\-•○·]*\*{0,2}사업\s*명\*{0,2}\s*[:：]\s*(.+)/,
  ];
  for (const re of patterns) {
    const m = re.exec(report);
    if (m) {
      const title = m[1]
        .replace(/\*\*/g, "")
        .replace(/\(가칭\)/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .replace(/[.,·]+$/, "");
      if (title.length >= 4) return title;
    }
  }
  return "";
}

/**
 * 노트·PDF에 쓸 제목. `(중복성검토)과제명` 형태.
 *
 * 과제명을 못 찾으면 순서대로 물러선다: 첨부 파일명 → 사용자가 쓴 요청 → 날짜.
 * 어느 경우에도 **서로 다른 리서치가 같은 제목을 갖지 않게** 하는 것이 목적이다.
 */
export function researchTitle(
  mode: string,
  report: string,
  prompt: string,
  fileName = "",
  now: Date = new Date(),
): string {
  const short = RESEARCH_MODE_SHORT[mode] ?? mode;  // 방 이름은 그대로
  const fromReport = extractProjectTitle(report);
  const fromFile = fileName.replace(/\.[^.]+$/, "").trim();
  const fromPrompt = prompt.trim().replace(/\s+/g, " ");
  const subject =
    fromReport ||
    fromFile ||
    fromPrompt ||
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
      now.getDate(),
    ).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(
      now.getMinutes(),
    ).padStart(2, "0")}`;
  return `(${short})${subject.slice(0, 60)}`;
}
