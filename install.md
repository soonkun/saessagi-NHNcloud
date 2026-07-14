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
2. **upstream/Open-LLM-VTuber 클론** — 고정 커밋 `19b58b1` 체크아웃 + `patches/` 필수 패치 적용 + frontend 서브모듈 초기화
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

## 6. 문제 해결

| 증상 | 원인/해법 |
|------|-----------|
| 캐릭터(창)가 아예 안 보임 | WSLg 표시 계층 고착 (오래 켜두면 발생). **Windows PowerShell에서 `wsl --shutdown`** 후 WSL 터미널을 새로 열고 재실행. 이때 진행 중인 WSL 작업이 모두 종료되니 주의 |
| 대화가 전부 무반응 + 로그에 `'NoneType' object has no attribute 'chat'` | 3단계(TTS 모델) 누락. TTS 초기화 실패가 LLM까지 전멸시킨다 — 3단계 실행 후 재시작 |
| LLM 응답 없음 | `ollama list` 태그와 conf.yaml `model:` 불일치 (4단계 참조), 또는 `curl http://127.0.0.1:11434/api/version`으로 Ollama 생존 확인 |
| 목소리만 안 나옴 (`/api/tts/speak` 503) | 백엔드 시작 직후에는 TTS 초기화에 ~8초 더 걸려 일시 503이 정상. 지속되면 3단계 확인 |
| 흰 화면 | `web/dist`가 `ELECTRON_BUILD=1` 없이 빌드된 것. `cd web && ELECTRON_BUILD=1 npm run build` 후 재실행 (E-22) |
| upstream 없음 오류 | `python3 scripts/bootstrap.py` 먼저 실행 |

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
export PYTHONPATH="$(pwd):$(pwd)/src:$(pwd)/upstream/Open-LLM-VTuber/src:$(pwd)/upstream/Open-LLM-VTuber"
cd upstream/Open-LLM-VTuber
uv run --project "$OLDPWD" uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393

# 터미널 2 — 프론트엔드
cd frontend && npm run start
```

백엔드만 검증하려면: `uv run python scripts/ws_test.py "안녕"` (LLM 응답·오디오 payload 수신 확인)
