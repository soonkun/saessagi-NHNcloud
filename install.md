# 새싹이 설치 가이드 — Linux

처음 클론한 뒤 실행까지의 전체 과정을 순서대로 정리한 문서다.
UI는 웹 페이지이므로 **서버에 디스플레이가 없어도 된다** (CR-38).
(2026-07-29, Ubuntu 24.04 + NVIDIA B200 헤드리스 서버에서 검증)

---

## 0. 사전 요구사항

| 항목 | 요구 사항 | 확인 명령 |
|------|-----------|-----------|
| OS | 리눅스 (디스플레이 불필요) | `uname -a` |
| Python | 3.12+ | `python3 --version` |
| Node.js | 18+ **npm 포함** | `node --version && npm --version` |
| Git | 최신 | `git --version` |
| Ollama | 설치 + 서비스 실행 가능 | `ollama --version` |
| GPU (권장) | CUDA 지원 NVIDIA GPU — LLM·TTS 가속 | `nvidia-smi` |
| 디스크 | 모델 포함 약 30GB 여유 | `df -h .` |
| 브라우저 | 접속하는 PC에 최신 크롬/엣지 | — |

> 배포판 `nodejs` 패키지에 npm이 빠져 있는 경우가 있다(Ubuntu 24.04 등).
> `npm --version`이 실패하면 Node를 공식 타르볼로 따로 설치하는 편이 빠르다.

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
# 한국어 TTS 모델 (~210MB) + 한국어 BERT (~400MB) + 검색 리랭커 (~1GB)
.venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('myshell-ai/MeloTTS-Korean', local_dir='assets/models/melotts-ko', local_dir_use_symlinks=False)
snapshot_download('kykim/bert-kor-base')
snapshot_download('BAAI/bge-reranker-v2-m3', local_dir='assets/models/bge-reranker-v2-m3')
"
# 주의: 리랭커가 없으면 검색이 '조용히' 벡터-only로 강등된다 (오류 없음, 품질만 저하)

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

(선택) 이미지·스크린샷 첨부를 읽으려면 비전 모델을 받아둔다 (~6GB).
gemma4 등 텍스트 전용 메인 모델은 이미지를 못 읽으므로, 이미지 턴만 이 모델이 대신 판독한다:

```bash
ollama pull qwen2.5vl:7b   # 한글 OCR 우수
```

받은 뒤 설정 화면 "보조 모델" 탭에서 비전 모델로 선택하거나, conf.yaml의
`app.ollama.vision_model: "qwen2.5vl:7b"`로 지정한다. 없으면 이미지 첨부 시
메인 모델이 그대로 처리를 시도한다 (텍스트 전용 모델이면 내용 파악 실패).

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
2. 프론트엔드 소스가 빌드보다 새로우면 자동 재빌드 (`--no-build`로 생략 가능)
3. Ollama 미실행 시 백그라운드로 기동
4. 이전 백엔드 정리 (`data/run/backend.pid` 기준)
5. 백엔드 기동 → 준비되면 **접속할 주소를 출력한다** (로그: `data/logs/backend.log`)

출력된 주소를 **브라우저로 열면** 된다. 종료는 `kill $(cat data/run/backend.pid)`.

첫 메시지 응답은 Ollama 모델 로딩 때문에 30초~1분 걸릴 수 있다. 이후로는 빠르다.

> **CR-38**: Electron 앱은 제거됐다. 예전엔 캐릭터가 바탕화면에 상주하는 펫 모드가
> 있었지만, 헤드리스 서버 배포로 전환하면서 웹 UI 하나로 단순화했다.

---

## 5b. 사내망에 공개하기 (선택)

기본값은 `127.0.0.1`이라 서버 안에서만 열린다. 다른 PC에서 접속하게 하려면 conf.yaml:

```yaml
app:
  web:
    host: 0.0.0.0
    auth_enabled: true
    auth_password: "원하는-비밀번호"
```

**인증을 켜지 않으면 백엔드가 기동을 거부한다.** 이 앱에는 로그인·권한 개념이 없어서
열어두면 사내 문서·LLM이 통째로 노출되기 때문이다. 비밀번호를 conf.yaml에 남기고 싶지
않으면 환경변수 `SAESSAGI_WEB_PASSWORD`를 쓰면 된다(이쪽이 우선한다).

접속하면 로그인 화면이 먼저 뜨고, 통과하면 세션 쿠키가 12시간 유지된다.

### 알아둘 제약 — 마이크는 HTTPS에서만 된다

브라우저는 secure context가 아니면 마이크 접근(`getUserMedia`)을 차단한다. 즉 평문
HTTP로 원격 접속하면 **음성 입력이 막힌다.** 텍스트 대화와 새싹이 음성 출력(TTS)은
정상 동작한다. 음성 입력이 필요하면 앞단에 TLS를 붙여야 한다
(리버스 프록시를 쓰면 `X-Forwarded-Proto: https`를 보고 쿠키에 Secure가 붙는다).

화면 분석(스크린샷)은 서버 화면을 캡처하므로 헤드리스 서버에서는 의미가 없다.

---

## 6. (선택) GraphRAG — Neo4j 지식그래프 (CR-18)

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

## 7. 문제 해결

| 증상 | 원인/해법 |
|------|-----------|
| 대화가 전부 무반응 + 로그에 `'NoneType' object has no attribute 'chat'` | 3단계(TTS 모델) 누락. TTS 초기화 실패가 LLM까지 전멸시킨다 — 3단계 실행 후 재시작 |
| bootstrap은 됐는데 실행하면 위 증상 재발 | 런처가 `uv run`으로 바뀌었는지 확인. `uv run`은 락파일에 없는 melotts를 지운다 — `.venv/bin/python` 직접 호출 또는 `--no-sync` 필요 (E-65) |
| LLM 응답 없음 | `ollama list` 태그와 conf.yaml `model:` 불일치 (4단계 참조), 또는 `curl http://127.0.0.1:11434/api/version`으로 Ollama 생존 확인 |
| 목소리만 안 나옴 (`/api/tts/speak` 503) | 백엔드 시작 직후에는 TTS 초기화에 ~8초 더 걸려 일시 503이 정상. 지속되면 3단계 확인 |
| 백엔드가 즉시 죽고 `auth_enabled` 오류 | `app.web.host`를 열어놓고 인증을 안 켠 것. 의도된 차단이다 — 5b 참조 |
| 로그인 화면만 반복 (쿠키가 안 붙음) | 평문 HTTP인데 쿠키에 Secure가 붙은 경우. 리버스 프록시가 `X-Forwarded-Proto`를 잘못 보내는지 확인 |
| 다른 PC에서 접속했는데 대화만 안 됨 | WebSocket이 `127.0.0.1`로 연결을 시도하는 상태. 설정 화면의 WebSocket 주소를 비우면 현재 접속 주소에서 자동 유도한다 |
| 마이크 버튼이 동작하지 않음 | 브라우저 제약 — HTTPS(또는 localhost)가 아니면 마이크가 차단된다. 5b 참조 |

### 리눅스 특이사항

- 백엔드 로그: `data/logs/backend.log`, Ollama 로그: `data/logs/ollama.log`
- PID 파일: `data/run/backend.pid` (런처가 이걸로 이전 인스턴스를 정리한다)

---

## 부록: 수동 실행 (런처 없이)

```bash
export SAESSAGI_ROOT="$(pwd)" SAESSAGI_CONFIG_PATH="$(pwd)/conf.yaml"
export PYTHONPATH="$(pwd):$(pwd)/src:$(pwd)/vendor"
.venv/bin/python -m app.main
```

`uv run`은 쓰지 말 것 — 락파일에 없는 melotts를 제거해 TTS·LLM이 죽는다 (E-65).
바인딩 주소·포트는 conf.yaml의 `app.web`을 따르며, `--host`/`--port`로 덮어쓸 수 있다.

백엔드만 검증하려면: `.venv/bin/python scripts/ws_test.py "안녕"`
(LLM 응답·오디오 payload 수신 확인)
