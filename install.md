# 새싹이 설치 가이드 — Linux / WSL2

처음 클론한 뒤 실행까지의 전체 과정을 순서대로 정리한 문서다.
(2026-07-14, Windows 11 + WSL2(Ubuntu) + WSLg + RTX 4090 환경에서 검증)

---

## 0. 사전 요구사항

| 항목 | 요구 사항 | 확인 명령 |
|------|-----------|-----------|
| OS | Windows 11 + WSL2 (WSLg 포함) 또는 리눅스 데스크톱 | `echo $WAYLAND_DISPLAY` → `wayland-0` 나오면 GUI 가능 |
| Python | 3.11+ | `python3 --version` |
| Node.js | 18+ (npm 포함) | `node --version` |
| Git | 최신 | `git --version` |
| Ollama | 설치 + 서비스 실행 가능 | `ollama --version` |
| GPU (권장) | CUDA 지원 NVIDIA GPU — LLM·TTS 가속 | `nvidia-smi` |
| 디스크 | 모델 포함 약 15GB 여유 | |

Ollama가 없다면:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

---

## 1. 클론

```bash
git clone https://github.com/soonkun/saessagi-Linux.git
cd saessagi-Linux
```

---

## 2. 부트스트랩 실행

```bash
python3 scripts/bootstrap.py
```

이 스크립트가 자동으로 처리하는 것:

1. **uv** 설치 (없을 때)
2. **vendor/open_llm_vtuber 존재 확인** — 대화 엔진은 리포지토리에 벤더링되어 있어 (CR-17) 별도 클론이 없다
3. **Ollama LLM 모델** `gemma4:e4b` 다운로드 (~9GB, 이미 gemma4 계열이 있으면 건너뜀)
4. **Python 가상환경(.venv) + 패키지 설치** (`uv sync`) + MeloTTS 패키지 설치
5. **BGE-M3 임베딩 모델** 다운로드 (~1.5GB → `assets/models/bge-m3`, RAG 문서 검색용)

각 단계는 멱등이라 중간에 끊겨도 다시 실행하면 된 부분은 건너뛴다.

---

## 3. TTS·음성인식 모델 다운로드 (bootstrap 미포함 — 필수)

TTS는 완전 오프라인으로 동작하도록 설계되어(`HF_HUB_OFFLINE=1` 강제) **로컬 모델이 없으면
백엔드의 LLM·대화 기능까지 통째로 초기화에 실패한다** (ERROR_HISTORY E-54).
아래를 반드시 실행할 것:

```bash
# 한국어 TTS 모델 (~210MB) + 한국어 BERT (~400MB)
.venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('myshell-ai/MeloTTS-Korean', local_dir='assets/models/melotts-ko', local_dir_use_symlinks=False)
snapshot_download('kykim/bert-kor-base')
"

# melo가 import 시점에 요구하는 각 언어 토크나이저 (합계 수십 MB)
.venv/bin/python -c "
from transformers import AutoTokenizer
for mid in ['tohoku-nlp/bert-base-japanese-v3', 'bert-base-uncased', 'bert-base-multilingual-uncased',
            'dbmdz/bert-base-french-europeana-cased', 'dccuchile/bert-base-spanish-wwm-uncased']:
    AutoTokenizer.from_pretrained(mid)
    print('OK:', mid)
"
```

(선택) 음성 입력(STT)까지 쓰려면 faster-whisper 모델도 미리 받아둔다 (~1.6GB):

```bash
.venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('mobiuslabsgmbh/faster-whisper-large-v3-turbo', cache_dir='assets/models')
"
```

---

## 4. 설정 확인 (conf.yaml)

첫 실행 시 `conf.example.yaml`이 `conf.yaml`로 자동 복사된다. 기본값은 **Ollama**다.
확인할 것 한 가지 — **conf.yaml의 모델명과 실제 설치된 Ollama 태그가 일치해야 한다**:

```bash
ollama list        # 설치된 태그 확인 (예: gemma4:e4b 또는 gemma4:latest)
grep "model: gemma" conf.yaml
```

불일치하면 conf.yaml의 `model:` 두 곳(122행 근처 `ollama_llm`, 152행 근처 `app.ollama`)을
`ollama list`에 나온 태그로 맞춘다.

(선택) 외부 AI(ChatGPT)를 쓰려면: `llm_provider`를 `openai_llm`/`openai`로 바꾸고
`llm_api_key`/`api_key`에 실제 키를 넣는다. 기본 상태에서는 필요 없다.

---

## 5. 실행

```bash
./새싹이.sh
```

런처가 순서대로 처리한다:

1. conf.yaml 없으면 템플릿에서 생성
2. 프론트엔드 소스가 빌드보다 새로우면 자동 재빌드 (`ELECTRON_BUILD=1` 포함)
3. Ollama 미실행 시 백그라운드로 기동
4. 포트(12393)를 점유한 이전 백엔드 정리
5. 백엔드 기동 → 준비 대기 (로그: `data/logs/backend.log`)
6. Electron 앱 실행 → **캐릭터가 화면 우하단에 나타남**

**터미널이 `[PetMode] enable` 로그에서 멈춘 것처럼 보이는 게 정상이다** — 앱이 실행 중이라는
뜻이다. 종료는 그 터미널에서 `Ctrl+C` (백엔드도 함께 정리된다).

첫 메시지 응답은 Ollama 모델 로딩 때문에 30초~1분 걸릴 수 있다. 이후로는 빠르다.

---

## 6. 한글 입력 설정 (WSL 필수)

WSLg 창은 Windows 한/영 IME를 받지 못하므로 리눅스 입력기(fcitx5)가 필요하다:

```bash
sudo apt install -y fcitx5 fcitx5-hangul
```

**주의: WSLg의 Weston은 Wayland 입력기 등록을 거부하므로 fcitx5를 X11 전용으로
실행해야 한다** (기본 실행 시 `permission to bind input_method denied`로 즉시 종료됨).
systemd 유저 서비스로 상주시키는 것을 권장:

```bash
mkdir -p ~/.config/systemd/user ~/.config/fcitx5

cat > ~/.config/systemd/user/fcitx5.service <<'EOF'
[Unit]
Description=Fcitx5 Input Method (WSLg 한글 입력)

[Service]
Environment=DISPLAY=:0
ExecStart=/usr/bin/fcitx5 --disable=wayland,waylandim
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF

cat > ~/.config/fcitx5/profile <<'EOF'
[Groups/0]
Name=Default
Default Layout=us
DefaultIM=hangul

[Groups/0/Items/0]
Name=keyboard-us

[Groups/0/Items/1]
Name=hangul

[GroupOrder]
0=Default
EOF

cat > ~/.config/fcitx5/config <<'EOF'
[Hotkey/TriggerKeys]
0=Control+space
1=Hangul
EOF

systemctl --user daemon-reload
systemctl --user enable --now fcitx5.service
```

`새싹이.sh`가 IME 환경 변수(GTK_IM_MODULE 등)를 자동 설정하므로, 이후 앱을
(재)시작하면 입력창에서 **Ctrl+Space**로 한/영 전환이 된다.

---

## 6b. (선택) GraphRAG — Neo4j 지식그래프 (CR-18)

문서·노트에서 개체(조직·사업·인물 등)와 관계를 추출해 그래프로 축적하고, 검색을
벡터+그래프 하이브리드로 보강하는 기능. **선택 사항** — 설치하지 않으면 앱은 기존
벡터 RAG로 동작한다 (`graphrag.enabled: false` 기본).

**설치 A — sudo 없이 사용자 공간 (WSL 개발 PC에서 검증된 방식):**

```bash
mkdir -p ~/opt && cd ~/opt
# JRE 21 (Temurin) + Neo4j Community 5.26
curl -sLo jre21.tar.gz "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jre/hotspot/normal/eclipse"
curl -sLo neo4j.tar.gz "https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz"
tar xzf jre21.tar.gz && mv jdk-*-jre jre21
tar xzf neo4j.tar.gz && mv neo4j-community-* neo4j
JAVA_HOME=~/opt/jre21 ~/opt/neo4j/bin/neo4j-admin dbms set-initial-password saessagi-graph

# systemd 유저 서비스로 상주 (부팅 자동 시작 + 자동 재시작)
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/neo4j.service <<'EOF'
[Unit]
Description=Neo4j Community (사용자 공간 설치 — M_19 GraphRAG)
[Service]
Environment=JAVA_HOME=%h/opt/jre21
ExecStart=%h/opt/neo4j/bin/neo4j console
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload && systemctl --user enable --now neo4j.service
```

**설치 B — apt (sudo 가능한 환경):**

```bash
wget -qO - https://debian.neo4j.com/neotechnology.gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
echo 'deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable latest' | sudo tee /etc/apt/sources.list.d/neo4j.list
sudo apt update && sudo apt install -y neo4j
sudo neo4j-admin dbms set-initial-password saessagi-graph
sudo systemctl enable --now neo4j
```

conf.yaml 설정:

```yaml
  graphrag:
    enabled: true
    neo4j_password: "saessagi-graph"
```

앱 재시작 후: **그래프 탭 → 재인덱싱**으로 기존 문서를 분석하면 그래프가 채워진다
(청크당 LLM 1콜이라 문서가 많으면 수 분 소요, 백그라운드 진행률 표시). 이후 채팅
답변의 **"근거 그래프"** 버튼으로 답변이 밟은 개체·관계·출처를 시각적으로 확인할 수 있다.

---

## 7. WSLg 입출력 제약 (알아둘 것)

Windows ↔ WSLg 창 사이에는 다음이 **전달되지 않는다** (앱 버그 아님, 플랫폼 제약):

| 동작 | 지원 | 대안 |
|------|------|------|
| 텍스트 복사/붙여넣기 | ✅ | — |
| 이미지(스크린샷) 붙여넣기 | ❌ | 캡처를 파일로 저장 → "클릭해서 파일 선택"에서 `/mnt/c/Users/<이름>/Pictures/Screenshots/` |
| 파일 복사→붙여넣기 | ❌ | "클릭해서 파일 선택" |
| 탐색기에서 파일 드래그 | ❌ | "클릭해서 파일 선택" (Windows 파일은 `/mnt/c/...`) |

---

## 8. 문제 해결

| 증상 | 원인/해법 |
|------|-----------|
| 캐릭터(창)가 아예 안 보임 | WSLg 표시 계층 고착 (오래 켜두면 발생). **Windows PowerShell에서 `wsl --shutdown`** 후 WSL 터미널을 새로 열고 재실행. 이때 진행 중인 WSL 작업이 모두 종료되니 주의 |
| 대화가 전부 무반응 + 로그에 `'NoneType' object has no attribute 'chat'` | 3단계(TTS 모델) 누락. TTS 초기화 실패가 LLM까지 전멸시킨다 — 3단계 실행 후 재시작 |
| LLM 응답 없음 | `ollama list` 태그와 conf.yaml `model:` 불일치 (4단계 참조), 또는 `curl http://127.0.0.1:11434/api/version`으로 Ollama 생존 확인 |
| 목소리만 안 나옴 (`/api/tts/speak` 503) | 백엔드 시작 직후에는 TTS 초기화에 ~8초 더 걸려 일시 503이 정상. 지속되면 3단계 확인 |
| 흰 화면 | `web/dist`가 `ELECTRON_BUILD=1` 없이 빌드된 것. `cd web && ELECTRON_BUILD=1 npm run build` 후 재실행 (E-22) |

**금지**: 브라우저로 `http://127.0.0.1:12393`을 열지 말 것 — Electron 전용 UI다.

### 리눅스 특이사항 (이 저장소에 이미 반영된 수정들)

- Electron 하드웨어 가속을 리눅스에서 자동 비활성화 (투명창 렌더링, E-52)
- 펫 모드 클릭스루 해제용 커서 폴러 — X 서버 `QueryPointer` 직접 조회 (E-53).
  WSLg에서 Electron의 `getCursorScreenPoint()`는 신뢰 불가
- 백엔드/프론트 로그: `data/logs/backend.log` / 런처 실행 터미널

---

## 부록: 수동 실행 (런처 없이)

```bash
# 터미널 1 — 백엔드
export SAESSAGI_ROOT="$(pwd)" SAESSAGI_CONFIG_PATH="$(pwd)/conf.yaml"
export PYTHONPATH="$(pwd):$(pwd)/src:$(pwd)/vendor"
uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393

# 터미널 2 — 프론트엔드
cd frontend && npm run start
```

백엔드만 검증하려면: `uv run python scripts/ws_test.py "안녕"` (LLM 응답·오디오 payload 수신 확인)
