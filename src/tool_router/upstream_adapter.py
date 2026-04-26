# src/tool_router/upstream_adapter.py
"""ToolRouterAdapter, CompositeToolExecutor — upstream ToolExecutor 경계 어댑터."""

import asyncio
import datetime
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from .errors import AgentProtocolError
from .router import ToolRouter
from .types import ToolResult

logger = logging.getLogger(__name__)


def _json_dumps(obj: Any) -> str:
    """ensure_ascii=False, 공백 최소 JSON 직렬화."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _format_search_docs_for_llm(result: ToolResult) -> str:
    """search_docs 결과를 LLM이 읽기 쉬운 텍스트 형식으로 변환.

    JSON 중첩 구조 대신 plain text로 변환해 Gemma4 같은 소형 모델도
    hits[].text 내용을 바로 읽을 수 있게 한다.
    """
    if not result.ok:
        return f"search_docs 오류: {result.error}"
    payload = result.payload or {}
    if not payload.get("found") or not payload.get("hits"):
        reason = payload.get("no_match_reason") or "등록된 문서에서 관련 내용을 찾지 못했습니다."
        return f"검색 결과 없음: {reason}"

    hits = payload["hits"]
    lines = [f"문서 검색 결과 ({len(hits)}건):"]
    for i, h in enumerate(hits, 1):
        text = (h.get("text") or "").strip()
        doc = h.get("doc_name") or ""
        lines.append(f"\n[{i}] 출처: {doc}\n{text}")
    return "\n".join(lines)


def _result_to_json(result: ToolResult) -> str:
    """ToolResult를 JSON 문자열로 직렬화."""
    if result.ok:
        obj: dict[str, Any] = {"ok": True, "payload": result.payload}
    else:
        obj = {
            "ok": False,
            "error": result.error,
            "error_code": result.error_code,
        }
    return _json_dumps(obj)


class ToolRouterAdapter:
    """upstream 호출부가 기대하는 인터페이스 두 가지를 동시에 제공한다.

    1) execute_tool(name, arguments) -> str — 본 프로젝트 내부 약속.
    2) run_single_tool(tool_name, tool_id, tool_input) -> tuple — upstream ToolExecutor 시그니처.
    """

    def __init__(self, router: ToolRouter) -> None:
        self._router = router

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """사용자 요구 시그니처.

        반환 JSON 구조:
          - 성공: {"ok": true, "payload": {...}}
          - 실패: {"ok": false, "error": "...", "error_code": "..."}

        예외는 raise하지 않는다.
        """
        try:
            result = await self._router.dispatch(name, arguments)
            json_str = _result_to_json(result)
            logger.debug(
                "execute_tool(%s) 완료: ok=%s, 응답 길이=%d", name, result.ok, len(json_str)
            )
            return json_str
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("execute_tool 내부 예상 밖 예외: %s", exc)
            err_obj: dict[str, Any] = {
                "ok": False,
                "error": f"adapter_exception: {exc}",
                "error_code": "handler_exception",
            }
            return _json_dumps(err_obj)

    async def run_single_tool(
        self, tool_name: str, tool_id: str, tool_input: Any
    ) -> tuple[bool, str, dict[str, Any], list[dict[str, Any]]]:
        """upstream ToolExecutor.run_single_tool과 동일 시그니처.

        반환: (is_error, text_content, metadata, content_items)
        """
        if tool_input is None:
            tool_input = {}

        result = await self._router.dispatch(tool_name, tool_input)
        text_content = _result_to_json(result)
        is_error = not result.ok
        metadata: dict[str, Any] = {
            "source": "local",
            "tool_name": tool_name,
            "tool_id": tool_id,
        }

        content_items: list[dict[str, Any]]
        if (
            not is_error
            and tool_name == "take_screenshot"
            and result.payload.get("mode") == "single"
        ):
            # 이미지 data URL에서 base64 raw 추출
            image_data_url: str = result.payload.get("image", "")
            # "data:image/png;base64,<raw>" → raw 부분 추출
            if "," in image_data_url:
                raw_b64 = image_data_url.split(",", 1)[1]
            else:
                raw_b64 = image_data_url
            content_items = [{"type": "image", "data": raw_b64, "mimeType": "image/png"}]
        elif not is_error:
            content_items = [{"type": "text", "text": text_content}]
        else:
            content_items = [{"type": "error", "text": result.error or "unknown error"}]

        return is_error, text_content, metadata, content_items

    def as_upstream_tool_executor(self, fallback: Any = None) -> "CompositeToolExecutor":
        """upstream과 동일 인터페이스의 대체 ToolExecutor를 생성해 반환."""
        return CompositeToolExecutor(self._router, self, fallback)


class CompositeToolExecutor:
    """execute_tools(tool_calls, caller_mode) -> AsyncIterator[dict]를 upstream과
    동일 형식으로 구현. 로컬 툴만 ToolRouter로, 나머지는 fallback.execute_tools로 위임.

    caller_mode="OpenAI"만 지원. 다른 모드면 AgentProtocolError raise.
    """

    def __init__(
        self,
        router: ToolRouter,
        adapter: ToolRouterAdapter,
        fallback: Any,  # ToolExecutor | None
    ) -> None:
        self._router = router
        self._adapter = adapter
        self._fallback = fallback

    async def execute_tools(
        self,
        tool_calls: list[Any],
        caller_mode: Literal["Claude", "OpenAI", "Prompt"],
    ) -> AsyncIterator[dict[str, Any]]:
        """upstream ToolExecutor.execute_tools와 동일 시그니처 — async generator."""
        if caller_mode != "OpenAI":
            raise AgentProtocolError(
                f"CompositeToolExecutor는 OpenAI caller_mode만 지원합니다. 요청된 모드: {caller_mode}"
            )
        async for update in self._execute_tools_impl(tool_calls, caller_mode):
            yield update

    async def _execute_tools_impl(
        self,
        tool_calls: list[Any],
        caller_mode: Literal["Claude", "OpenAI", "Prompt"],
    ) -> AsyncIterator[dict[str, Any]]:
        """실제 실행 로직 — async generator."""
        tool_results_for_llm: list[dict[str, Any]] = []

        for call in tool_calls:
            # parse_tool_call: upstream ToolCallObject 또는 dict 처리
            (
                tool_name,
                tool_id,
                tool_input,
                is_error,
                result_content,
                parse_error,
            ) = self._parse_tool_call(call)

            if parse_error:
                error_id = tool_id or f"parse_error_{_now_iso()}"
                yield {
                    "type": "tool_call_status",
                    "tool_id": error_id,
                    "tool_name": tool_name or "Unknown Tool",
                    "status": "error",
                    "content": result_content,
                    "timestamp": _now_iso(),
                }
                formatted = self._format_tool_result_openai(error_id, str(result_content))
                tool_results_for_llm.append(formatted)
                continue

            if tool_name in ToolRouter.LOCAL_TOOL_NAMES:
                # 로컬 툴 처리
                yield {
                    "type": "tool_call_status",
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "status": "running",
                    "content": f"Input: {_json_dumps(tool_input)}",
                    "timestamp": _now_iso(),
                }

                result: ToolResult = await self._router.dispatch(tool_name, tool_input or {})
                text_content = _result_to_json(result)

                # LLM에 전달할 content — search_docs는 읽기 쉬운 텍스트 형식 사용
                llm_content = (
                    _format_search_docs_for_llm(result)
                    if tool_name == "search_docs"
                    else text_content
                )

                if (
                    result.ok
                    and tool_name == "take_screenshot"
                    and result.payload.get("mode") == "single"
                ):
                    status_content = f"{text_content}\n[Tool returned 1 image(s)]".strip()
                    yield {
                        "type": "tool_call_status",
                        "tool_id": tool_id,
                        "tool_name": tool_name,
                        "status": "completed",
                        "content": status_content,
                        "timestamp": _now_iso(),
                    }
                else:
                    yield {
                        "type": "tool_call_status",
                        "tool_id": tool_id,
                        "tool_name": tool_name,
                        "status": "error" if not result.ok else "completed",
                        "content": (f"Error: {result.error}" if not result.ok else llm_content),
                        "timestamp": _now_iso(),
                    }

                formatted = self._format_tool_result_openai(tool_id, llm_content)
                tool_results_for_llm.append(formatted)

            else:
                # MCP 툴: fallback 위임
                if self._fallback is None:
                    unknown_result = ToolResult(
                        ok=False,
                        error=f"unknown_tool: {tool_name}",
                        error_code="unknown_tool",
                    )
                    text_content = _result_to_json(unknown_result)
                    yield {
                        "type": "tool_call_status",
                        "tool_id": tool_id,
                        "tool_name": tool_name,
                        "status": "error",
                        "content": f"Error: {unknown_result.error}",
                        "timestamp": _now_iso(),
                    }
                    tool_results_for_llm.append(
                        self._format_tool_result_openai(tool_id, text_content)
                    )
                else:
                    async for update in self._fallback.execute_tools([call], "OpenAI"):
                        if update.get("type") == "final_tool_results":
                            tool_results_for_llm.extend(update.get("results", []))
                        else:
                            yield update

        yield {"type": "final_tool_results", "results": tool_results_for_llm}

    def _parse_tool_call(self, call: Any) -> tuple[str, str, Any, bool, str, bool]:
        """tool_call을 파싱해 (tool_name, tool_id, tool_input, is_error, result_content, parse_error) 반환."""
        tool_name = ""
        tool_id = ""
        tool_input: Any = None
        is_error = False
        result_content = ""
        parse_error = False

        # upstream ToolCallObject 처리 시도
        if hasattr(call, "function") and hasattr(call, "id"):
            tool_name = call.function.name
            tool_id = call.id
            try:
                tool_input = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.error("OpenAI tool arguments 파싱 실패: %s", tool_name)
                result_content = f"Error: Invalid arguments format for tool '{tool_name}'."
                is_error = True
                parse_error = True
        elif isinstance(call, dict):
            tool_id = call.get("id", "")
            tool_name = call.get("name", "")
            tool_input = call.get("input", call.get("args"))

            if tool_input is None:
                tool_input = {}

            if not tool_id or not tool_name:
                logger.error("잘못된 Dict tool call 구조: %s", call)
                result_content = "Error: Invalid tool call structure from LLM."
                is_error = True
                parse_error = True
        else:
            logger.error("지원하지 않는 tool call 타입: %s", type(call))
            result_content = "Error: Unsupported tool call type."
            is_error = True
            parse_error = True

        return tool_name, tool_id, tool_input, is_error, result_content, parse_error

    def _format_tool_result_openai(self, tool_id: str, content: str) -> dict[str, Any]:
        """OpenAI 형식 tool result 포맷."""
        return {
            "role": "tool",
            "tool_call_id": tool_id,
            "content": content,
        }
