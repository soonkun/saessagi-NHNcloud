// 관리 기능 2차 잠금 (CR-69).
//
// 로그인은 "이 앱을 쓸 수 있는가"를 가르고, 여기는 "문서를 지우거나 그래프를 다시 만들
// 수 있는가"를 가른다. 실수로 눌러 코퍼스를 날리는 것을 막는 것이 목적이다.
//
// **비밀번호는 프론트에 없다.** 번들에 넣으면 누구나 읽는다 — 서버(`/api/auth/admin-unlock`)가
// 검증하고, 여기서는 통과 여부만 기억한다. 기억은 `sessionStorage`라 탭을 닫으면 풀린다.

import { useState } from "react";
import { Lock } from "lucide-react";
import { API_BASE } from "../services/api";

const KEY = "saessagi.adminUnlocked";

export function isAdminUnlocked(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

function remember(): void {
  try {
    sessionStorage.setItem(KEY, "1");
  } catch {
    // 저장이 막혀 있어도 이번 화면에서는 열린 상태로 진행한다.
  }
}

async function verify(password: string): Promise<boolean> {
  try {
    const res = await fetch(API_BASE + "/api/auth/admin-unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) return false;
    const data = (await res.json()) as { ok?: boolean };
    return !!data.ok;
  } catch {
    return false;
  }
}

/**
 * 잠금 화면. 통과하면 `children`을 보여준다.
 *
 * @param label 무엇을 여는지 (예: "문서 관리")
 */
export function AdminGate({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  const [open, setOpen] = useState(isAdminUnlocked);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (open) return <>{children}</>;

  async function submit(): Promise<void> {
    if (busy || !pw) return;
    setBusy(true);
    setErr("");
    const ok = await verify(pw);
    setBusy(false);
    if (ok) {
      remember();
      setOpen(true);
      return;
    }
    setErr("비밀번호가 맞지 않습니다.");
    setPw("");
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        padding: 24,
        height: "100%",
        textAlign: "center",
      }}
    >
      <Lock size={26} style={{ color: "var(--color-text-muted)" }} />
      <div style={{ fontSize: "var(--fs-14)", fontWeight: 600 }}>{label}</div>
      <p
        style={{
          fontSize: "var(--fs-12)",
          color: "var(--color-text-muted)",
          lineHeight: 1.7,
          margin: 0,
          maxWidth: 320,
        }}
      >
        문서와 지식그래프를 바꿀 수 있는 화면입니다.
        <br />
        계속하려면 관리 비밀번호를 입력하세요.
      </p>
      <input
        type="password"
        value={pw}
        autoFocus
        onChange={(e) => setPw(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
        }}
        placeholder="관리 비밀번호"
        style={{
          width: 220,
          padding: "9px 12px",
          fontSize: "var(--fs-13)",
          fontFamily: "inherit",
          borderRadius: 8,
          border: `1px solid ${err ? "#c0392b" : "var(--color-border)"}`,
          background: "var(--color-bg)",
          color: "var(--color-text)",
          textAlign: "center",
        }}
      />
      {err && <div style={{ color: "#c0392b", fontSize: "var(--fs-11)" }}>{err}</div>}
      <button
        onClick={() => void submit()}
        disabled={busy || !pw}
        style={{
          padding: "8px 20px",
          fontSize: "var(--fs-12)",
          fontWeight: 600,
          fontFamily: "inherit",
          borderRadius: 8,
          border: "none",
          background: "var(--color-accent)",
          color: "#fff",
          cursor: busy || !pw ? "not-allowed" : "pointer",
          opacity: busy || !pw ? 0.6 : 1,
        }}
      >
        {busy ? "확인 중…" : "열기"}
      </button>
    </div>
  );
}
