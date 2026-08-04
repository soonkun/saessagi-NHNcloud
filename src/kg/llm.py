# src/kg/llm.py
"""M_23 추출용 LLM 클라이언트 — OpenAI 호환 API (ollama·vLLM 공용).

기존 `GemmaChatAgent.complete_json`을 쓰지 않고 따로 둔 이유가 둘 있다.

1. **스키마를 실제로 강제하기 위해서.** 기존 구현은 `json_schema` 인자를 받아 놓고
   쓰지 않는다("Protocol 호환용"). 그래서 모델이 `primary_statement_type` 대신
   `primary_statement_implication` 같은 필드를 뱉어도 막히지 않았고, 검증 단계에서
   BAD_TYPE·BAD_STATUS로 버려졌다. 구조화 출력을 지원하는 서버(vLLM)에서는 스키마를
   걸어 애초에 어긋난 출력을 만들지 않게 한다.

2. **엔진을 바꿔 끼우기 위해서.** 추출은 단일 모델·대량 배치라 연속 배치를 하는 엔진이
   유리하다(실측: ollama는 디코드가 전체의 89%인데 슬롯을 늘려도 1.25배에 그침).
   base_url만 바꾸면 ollama ↔ vLLM을 오갈 수 있어야 한다.

두 서버 모두 `/v1/chat/completions`를 제공하므로 같은 코드로 다룬다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 구조화 출력 방식 (서버마다 지원이 다르다)
SCHEMA_MODE_JSON_SCHEMA = "json_schema"  # OpenAI/vLLM 표준 response_format
SCHEMA_MODE_GUIDED = "guided_json"  # vLLM 확장 (구버전 호환)
SCHEMA_MODE_JSON_OBJECT = "json_object"  # 느슨한 JSON 모드
SCHEMA_MODE_OLLAMA_NATIVE = "ollama_native"  # ollama /api/chat — 스키마 강제 + think=false
SCHEMA_MODE_NONE = "none"


class JsonCompletionClient:
    """`complete_json` 계약과 호환되는 호출자.

    `ExtractionRunner`가 기대하는 시그니처를 그대로 만족하므로, 런너 코드를 고치지 않고
    엔진만 갈아끼울 수 있다.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "EMPTY",
        schema_mode: str = SCHEMA_MODE_JSON_SCHEMA,
        extra_body: dict[str, Any] | None = None,
        max_connections: int = 32,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.schema_mode = schema_mode
        self._extra_body = dict(extra_body or {})
        # 동시 호출이 많으므로 연결 풀을 넉넉히 잡는다. 기본값(10)이면 여기서 줄을 선다.
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=max_connections, max_keepalive_connections=max_connections
            ),
            timeout=httpx.Timeout(600.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _response_format(self, schema: dict[str, Any]) -> dict[str, Any]:
        """서버가 이해하는 구조화 출력 지시를 만든다."""
        if self.schema_mode == SCHEMA_MODE_JSON_SCHEMA:
            return {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "extraction", "schema": schema, "strict": True},
                }
            }
        if self.schema_mode == SCHEMA_MODE_GUIDED:
            return {"extra_body": {"guided_json": schema}}
        if self.schema_mode == SCHEMA_MODE_JSON_OBJECT:
            return {"response_format": {"type": "json_object"}}
        return {}

    async def _call_ollama_native(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """ollama 네이티브 `/api/chat`.

        OpenAI 호환 엔드포인트로 부르면 **빈 응답만 온다**(finish_reason=length).
        gemma4는 thinking 모델이라 추론 토큰이 예산을 다 쓰기 때문이다. 네이티브 API의
        `think: false`가 있어야 하고, 스키마도 `format`으로 여기서만 걸 수 있다.
        """
        body = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": json_schema,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        async with asyncio.timeout(timeout_seconds):
            resp = await self._client.post(f"{self.base_url}/api/chat", json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM 서버 오류 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        content = (data.get("message") or {}).get("content") or ""
        if not content.strip():
            raise ValueError(f"빈 응답 (done_reason={data.get('done_reason')})")
        try:
            parsed: dict[str, Any] = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"JSON 파싱 실패: {exc}. 앞 200자: {content[:200]}") from exc
        return parsed

    async def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        if self.schema_mode == SCHEMA_MODE_OLLAMA_NATIVE:
            return await self._call_ollama_native(
                system_prompt, user_prompt, json_schema, max_tokens, temperature, timeout_seconds
            )
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        fmt = self._response_format(json_schema)
        # guided_json은 최상위가 아니라 확장 필드로 들어간다.
        extra = fmt.pop("extra_body", None)
        body.update(fmt)
        if extra:
            body.update(extra)
        body.update(self._extra_body)

        async with asyncio.timeout(timeout_seconds):
            resp = await self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM 서버 오류 {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        if not content.strip():
            # 빈 응답은 대개 토큰 한도에서 잘린 경우다 — 사유를 남겨야 튜닝이 가능하다.
            raise ValueError(f"빈 응답 (finish_reason={finish})")

        try:
            parsed: dict[str, Any] = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"JSON 파싱 실패 (finish_reason={finish}): {exc}. 앞 200자: {content[:200]}"
            ) from exc
        return parsed


async def probe_schema_mode(base_url: str, model: str, api_key: str = "EMPTY") -> str:
    """서버가 지원하는 구조화 출력 방식을 찾아낸다.

    ollama와 vLLM은 지원 범위가 다르고 버전에 따라서도 달라진다. 설정으로 강제하기보다
    한 번 찔러 보고 정하는 편이 운영에서 덜 깨진다.
    """
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    for mode in (
        SCHEMA_MODE_JSON_SCHEMA,
        SCHEMA_MODE_GUIDED,
        SCHEMA_MODE_OLLAMA_NATIVE,
        SCHEMA_MODE_JSON_OBJECT,
    ):
        client = JsonCompletionClient(base_url, model, api_key=api_key, schema_mode=mode)
        try:
            await client(
                "JSON만 출력한다.",
                'ok 필드를 true로 하는 JSON을 출력하라. 예: {"ok": true}',
                schema,
                max_tokens=64,
                temperature=0.0,
                timeout_seconds=120.0,
            )
            logger.info("구조화 출력 방식 확정: %s (%s)", mode, base_url)
            return mode
        except Exception as exc:
            logger.debug("구조화 출력 방식 %s 미지원(%s): %s", mode, base_url, exc)
        finally:
            await client.aclose()
    logger.warning("구조화 출력을 지원하지 않는 서버 — 프롬프트만으로 진행 (%s)", base_url)
    return SCHEMA_MODE_NONE
