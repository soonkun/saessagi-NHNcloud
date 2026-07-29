#!/usr/bin/env bash
# deploy/nhncloud-setup.sh — 헤드리스 GPU 서버 1회 설치 스크립트 (CR-38 웹 UI 버전)
#
# **새 인스턴스에 처음부터 구축할 때** 클론 직후 한 번 실행한다.
# 이미 구축된 서버에서는 실행할 필요가 없다 — 그냥 ./새싹이.sh 를 쓰면 된다.
#
#   bash deploy/nhncloud-setup.sh
#
# 약 24GB를 내려받으므로 회선에 따라 20~40분 걸린다. 각 단계는 멱등이라 중간에
# 끊기면 다시 실행하면 된 부분은 건너뛴다.
#
# install.md의 1~4단계를 자동화하되, 2026-07-29 NHN 클라우드 실제 구축에서 걸렸던
# 함정 세 가지를 반영했다:
#   - 배포판 nodejs 패키지에 npm이 빠져 있음 (Ubuntu 24.04) → 타르볼로 통일
#   - ollama.com 설치 스크립트는 systemd를 전제 → 컨테이너 대비 타르볼 사용
#   - TTS 모델을 빠뜨리면 TTS 초기화 실패가 LLM 대화까지 죽인다 (E-54) → 필수 단계로 포함
#
# 툴체인은 시스템을 건드리지 않고 프로젝트 안(.local/)에 설치한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OPT="$ROOT/.local"          # 자체 설치 툴체인 (node, ollama)
NODE_VER="20.18.1"

log() { echo -e "\n\033[1;36m[setup] $*\033[0m"; }
ok()  { echo -e "\033[0;32m  -> $*\033[0m"; }
warn(){ echo -e "\033[1;33m  !! $*\033[0m"; }

mkdir -p "$OPT"
export PATH="$OPT/node/bin:$OPT/ollama/bin:$PATH"

# ── 0. 사전 확인 ──────────────────────────────────────────────────────────────
log "[0/7] 환경 확인"
python3 --version || { echo "python3 필요"; exit 1; }
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1
else
    warn "GPU를 못 찾았습니다. CPU로도 동작하지만 응답이 매우 느립니다."
    warn "NHN 클라우드 GPU 인스턴스라면 NVIDIA 드라이버 설치 여부를 확인하세요."
fi

# ── 1. Node + npm ─────────────────────────────────────────────────────────────
# 배포판 nodejs 패키지에 npm이 빠져 있는 경우가 있어(Ubuntu 24.04) 타르볼로 통일한다.
log "[1/7] Node.js + npm"
if [ -x "$OPT/node/bin/npm" ]; then
    ok "이미 설치됨 ($("$OPT/node/bin/node" --version))"
else
    curl -fsSLo /tmp/node.tar.xz \
        "https://nodejs.org/dist/v${NODE_VER}/node-v${NODE_VER}-linux-x64.tar.xz"
    tar xf /tmp/node.tar.xz -C /tmp && rm /tmp/node.tar.xz
    rm -rf "$OPT/node" && mv "/tmp/node-v${NODE_VER}-linux-x64" "$OPT/node"
    ok "Node $("$OPT/node/bin/node" --version) / npm $("$OPT/node/bin/npm" --version)"
fi

# ── 2. Ollama ─────────────────────────────────────────────────────────────────
log "[2/7] Ollama"
if [ -x "$OPT/ollama/bin/ollama" ]; then
    ok "이미 설치됨"
else
    mkdir -p "$OPT/ollama"
    VER=$(curl -s https://api.github.com/repos/ollama/ollama/releases/latest \
          | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
    curl -fsSL -o /tmp/ollama.tar.zst \
        "https://github.com/ollama/ollama/releases/download/${VER}/ollama-linux-amd64.tar.zst"
    tar --zstd -xf /tmp/ollama.tar.zst -C "$OPT/ollama" && rm /tmp/ollama.tar.zst
    ok "Ollama ${VER} 설치"
fi

if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    setsid nohup "$OPT/ollama/bin/ollama" serve >/tmp/ollama-setup.log 2>&1 </dev/null &
    for _ in $(seq 1 30); do
        sleep 1
        curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
    done
fi
curl -sf http://127.0.0.1:11434/api/version >/dev/null && ok "Ollama 응답 확인"

# ── 3. uv + Python 환경 ──────────────────────────────────────────────────────
log "[3/7] Python 가상환경 + 의존성"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
[ -x "$ROOT/.venv/bin/python" ] || uv venv
uv sync
# melotts는 pypinyin 충돌로 pyproject.toml 밖에 있다 — 별도 설치가 필수.
uv pip install --quiet "tokenizers>=0.14.0" "transformers>=4.35.0" "pypinyin==0.50.0"
uv pip install --quiet --no-deps "melotts @ git+https://github.com/myshell-ai/MeloTTS.git"
uv pip install --quiet huggingface_hub
ok "패키지 설치 완료"

# ── 4. LLM 모델 ──────────────────────────────────────────────────────────────
log "[4/7] Ollama LLM 모델 (~9GB)"
if "$OPT/ollama/bin/ollama" list 2>/dev/null | grep -q "gemma4"; then
    ok "이미 있음"
else
    "$OPT/ollama/bin/ollama" pull gemma4:e4b
fi

# ── 5. TTS·검색·ASR 모델 ─────────────────────────────────────────────────────
# 여기를 빠뜨리면 TTS 초기화 실패가 LLM 대화까지 죽인다 (E-54). 선택 사항이 아니다.
log "[5/7] TTS·임베딩·리랭커·ASR 모델 (~11GB)"
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer
import os

jobs = [
    ("BAAI/bge-m3",              dict(local_dir="assets/models/bge-m3")),
    ("myshell-ai/MeloTTS-Korean", dict(local_dir="assets/models/melotts-ko")),
    ("kykim/bert-kor-base",       dict()),
    ("BAAI/bge-reranker-v2-m3",   dict(local_dir="assets/models/bge-reranker-v2-m3")),
    ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", dict(cache_dir="assets/models")),
]
for repo, kw in jobs:
    target = kw.get("local_dir")
    if target and os.path.exists(os.path.join(target, "config.json")):
        print("skip:", repo); continue
    print("download:", repo)
    snapshot_download(repo, **kw)

# melo가 import 시점에 요구하는 각 언어 토크나이저
for mid in ["tohoku-nlp/bert-base-japanese-v3", "bert-base-uncased",
            "bert-base-multilingual-uncased",
            "dbmdz/bert-base-french-europeana-cased",
            "dccuchile/bert-base-spanish-wwm-uncased"]:
    AutoTokenizer.from_pretrained(mid)
print("모델 준비 완료")
PY

# whisper 스냅샷 경로가 conf.example.yaml의 model_path와 맞는지 확인
SNAP=$(ls -d assets/models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/*/ 2>/dev/null | head -1)
[ -n "$SNAP" ] && ok "whisper 스냅샷: ${SNAP}"

# ── 6. 프론트엔드 빌드 ───────────────────────────────────────────────────────
log "[6/7] 웹 UI 빌드"
( cd "$ROOT/web" && npm install --no-audit --no-fund && npm run build )
grep -q 'assets/' "$ROOT/web/dist/index.html" && ok "web/dist 생성 확인"

# ── 7. 설정 ──────────────────────────────────────────────────────────────────
log "[7/7] conf.yaml"
if [ ! -f "$ROOT/conf.yaml" ]; then
    cp "$ROOT/conf.example.yaml" "$ROOT/conf.yaml"
    ok "conf.example.yaml 에서 생성"
else
    ok "이미 존재 — 건드리지 않음"
fi

cat <<'EOS'

════════════════════════════════════════════════════════════════
 설치 완료
════════════════════════════════════════════════════════════════

실행:
    ./새싹이.sh

기본값은 127.0.0.1 로컬 전용이다. 외부 PC에서 접속하는 방법은 두 가지:

 [A] SSH 터널 (권장 — 포트를 새로 열지 않는다)
     외부 PC에서:
         ssh -N -p <SSH포트> -L 12393:127.0.0.1:12393 <계정>@<서버주소>
     이 창을 띄워둔 채 브라우저에서 http://localhost:12393

     -N 이 중요하다. ForceCommand가 걸린 SFTP 전용 SSH라도 셸을 요청하지 않으면
     터널만 뚫린다 (서버쪽 AllowTcpForwarding 이 yes 여야 한다 — `sshd -T` 로 확인).

     이 방식의 이점:
       · 노출 면적이 늘지 않는다
       · 앱은 평문 HTTP지만 SSH 암호화 안으로 흐른다
       · localhost는 브라우저의 secure context라 **마이크(음성 입력)가 동작한다**

 [B] 서버를 직접 열기
     conf.yaml 의 app.web 을 수정:
         host: 0.0.0.0
         auth_enabled: true
         auth_password: "..."      (또는 환경변수 SAESSAGI_WEB_PASSWORD)
     인증을 켜지 않으면 백엔드가 기동을 거부한다.
     클라우드 보안 그룹에서 12393을 열되 소스 IP를 제한할 것.
     평문 HTTP면 비밀번호가 그대로 흐르고 마이크는 차단된다 — TLS 권장.

 주의: 여러 사용자가 함께 쓰는 서버라면 host를 127.0.0.1로 두더라도 auth_enabled를
 켜둘 것. 루프백 바인딩은 같은 서버의 다른 계정을 막아주지 않는다.

EOS
