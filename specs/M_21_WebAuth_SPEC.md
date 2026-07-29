# M_21 WebAuth — 웹 UI 비밀번호 인증

CR-38의 하위 모듈. 사내망에 노출되는 웹 UI 앞에 비밀번호 인증을 둔다.

## 문제

앱에는 사용자·로그인 개념이 없다. 백엔드를 `0.0.0.0`에 바인드하면 사내 문서 RAG·파일
업로드·LLM·회의록이 **인증 없이** 네트워크에 노출된다. 공용 GPU 노드라 더 위험하다.

## 범위

- **보호 대상**: 모든 HTTP 라우트, `/` 정적 마운트(web/dist), **`/client-ws` WebSocket**.
  WebSocket을 빼먹으면 대화·TTS 전체가 무인증으로 열린다 — 이게 이 스펙의 핵심이다.
- **비범위**: 다중 사용자, 권한 분리, 계정 관리. 비밀번호 1개를 아는 사람 = 사용 가능.
  기관 SSO 도입 시 이 모듈을 교체한다.

## 설정 (`conf.yaml` → `app.web`)

| 키 | 기본값 | 의미 |
|----|--------|------|
| `host` | `127.0.0.1` | 바인드 주소. 사내망 노출은 `0.0.0.0`을 **명시**해야만 일어난다 |
| `port` | `12393` | 바인드 포트 |
| `auth_enabled` | `false` | 인증 사용 여부. 기본 off — 로컬 전용 사용 시 기존과 동일 |
| `auth_password` | `""` | 비밀번호. 환경변수 `SAESSAGI_WEB_PASSWORD`가 있으면 그쪽이 우선 |
| `session_ttl_hours` | `12` | 세션 유효 시간 |

**안전장치**: `host`가 루프백이 아닌데 `auth_enabled: false`면 **기동을 거부**한다.
"열어두고 인증 켜는 걸 잊는" 사고가 가장 위험하므로 설정 실수를 실행 실패로 바꾼다.
`auth_enabled: true`인데 비밀번호가 비어 있어도 거부한다.

## 토큰

상태를 서버에 두지 않는다(재시작해도 세션 유지, 메모리 증가 없음).

```
secret  = sha256(auth_password + salt)      # salt: data/.web_auth_salt, 없으면 생성
token   = "{exp}.{hmac_sha256(secret, str(exp))}"
```

- `exp`: 만료 unix time. 검증 시 만료·서명 둘 다 확인, 비교는 `hmac.compare_digest`.
- 비밀번호를 바꾸면 secret이 바뀌어 기존 토큰이 자동 무효화된다.
- salt 파일 권한은 0600.

## 전달

`saessagi_session` 쿠키. `HttpOnly`, `SameSite=Lax`, `Path=/`.
HTTPS로 접속한 경우에만 `Secure`를 붙인다(평문 HTTP에서 `Secure`를 붙이면 쿠키가
저장되지 않아 로그인이 무한 반복된다).

브라우저는 동일 origin WebSocket 핸드셰이크에도 쿠키를 실어 보내므로 `/client-ws`도
같은 쿠키로 검증된다. 별도 토큰 전달 경로가 필요 없다.

## 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/login` | 로그인 페이지(Python이 인라인 HTML 반환 — 프론트 빌드 의존 없음) |
| POST | `/api/auth/login` | `{"password": "..."}` → 성공 시 쿠키 설정. 실패 401 |
| POST | `/api/auth/logout` | 쿠키 삭제 |

인증 면제 경로: 위 세 개뿐.

## 미들웨어 동작

순수 ASGI 미들웨어(`scope["type"]`이 `http`/`websocket` 둘 다 처리).

- `auth_enabled: false` → 아무것도 하지 않고 통과.
- **websocket** + 무효 토큰 → 핸드셰이크 거부(`websocket.close`, code 1008).
- **http** + 무효 토큰:
  - `/api/*` → `401 {"detail": "unauthorized"}` (fetch가 JSON으로 처리)
  - 그 외(문서 요청) → `302 /login` (브라우저가 로그인 페이지로 이동)

무차별 대입 완화: 실패 응답을 ~0.5초 지연시킨다. 비밀번호 1개짜리 단순 인증이라
계정 잠금은 두지 않는다.

## 테스트 (`tests/app/test_web_auth.py`)

1. 토큰 발급→검증 왕복 성공
2. 만료 토큰 거부
3. 서명 변조 토큰 거부
4. 비밀번호 변경 시 기존 토큰 무효
5. `auth_enabled: false`면 미들웨어 통과
6. 인증 없이 `/api/*` → 401
7. 인증 없이 문서 요청 → 302 `/login`
8. **인증 없이 `/client-ws` → 연결 거부**
9. 로그인 후 쿠키로 `/api/*` 접근 성공
10. 비루프백 host + `auth_enabled: false` → 기동 거부
11. `auth_enabled: true` + 빈 비밀번호 → 기동 거부
