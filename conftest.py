# conftest.py
"""최상위 pytest conftest — src와 upstream을 sys.path에 추가.

tests/app/__init__.py가 pytest에 의해 'app' 패키지로 인식되는 문제를 해결하기 위해
sys.path를 먼저 설정하고 src/app을 올바른 'app' 패키지로 등록.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).parent

# src 디렉토리 (가장 앞에 추가하여 우선순위 확보)
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# vendor/ (open_llm_vtuber·prompts 패키지 — CR-17 벤더링, upstream 클론 불필요)
_VENDOR = _PROJECT_ROOT / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(1, str(_VENDOR))


# upstream의 선택적 의존성을 mock으로 등록 (테스트 환경에서 설치되지 않은 패키지들)
def _make_mock_package(name: str) -> MagicMock:
    """서브모듈 접근이 가능한 mock 패키지 생성."""
    from importlib.machinery import ModuleSpec

    mock = MagicMock()
    mock.__name__ = name
    mock.__package__ = name
    mock.__path__ = []  # 패키지로 인식되도록
    # __spec__=None이면 importlib.util.find_spec이 ValueError를 던진다.
    # transformers._is_package_available 등이 find_spec으로 설치 여부만 체크할 때
    # ValueError가 예외 전파되는 문제를 피하기 위해 최소 ModuleSpec을 부여한다.
    mock.__spec__ = ModuleSpec(name, loader=None)
    return mock


_MOCK_PACKAGES = [
    "letta_client",
    "mem0",
    "hume",
    "aiohttp",
    "pydub",
    "pydub.utils",
    "faster_whisper",
    "torch",
    "torchaudio",
    "torchaudio.transforms",
    "silero_vad",
    "onnxruntime",
    # M_04 TTS — 실제 라이브러리가 설치되지 않은 환경에서 mock 등록
    "melo",
    "melo.api",
    "TTS",
    "TTS.api",
    "soundfile",
    # M_05 LLMAgent — anthropic SDK는 사용 안 하지만 upstream import가 요구
    "anthropic",
    # open_llm_vtuber — vendor/에 항상 존재하므로 find_spec에 걸려 mock되지 않음.
    # (선택 의존성 미설치로 vendor import가 실패하는 환경 대비 안전망으로만 유지)
    "open_llm_vtuber",
    "open_llm_vtuber.asr",
    "open_llm_vtuber.asr.asr_interface",
    "open_llm_vtuber.tts",
    "open_llm_vtuber.tts.tts_interface",
    "open_llm_vtuber.agent",
    "open_llm_vtuber.agent.agents",
    "open_llm_vtuber.agent.agents.agent_interface",
    "open_llm_vtuber.agent.agents.basic_memory_agent",
    "open_llm_vtuber.agent.input_types",
    "open_llm_vtuber.agent.stateless_llm",
    "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm",
    "open_llm_vtuber.config_manager",
    "open_llm_vtuber.config_manager.utils",
    "open_llm_vtuber.mcpp",
    "open_llm_vtuber.mcpp.tool_executor",
    "open_llm_vtuber.mcpp.tool_manager",
    "open_llm_vtuber.routes",
    "open_llm_vtuber.server",
    "open_llm_vtuber.service_context",
    "open_llm_vtuber.websocket_handler",
]
for _pkg in _MOCK_PACKAGES:
    if _pkg in sys.modules:
        continue
    # 실제 venv에 설치된 패키지는 mock으로 덮지 않는다.
    # find_spec이 ValueError를 던지는 경우(sys.modules에 __spec__=None인 가짜가 있을 때)는
    # except로 흡수하고 mock 경로로 진행.
    try:
        if importlib.util.find_spec(_pkg) is not None:
            continue
    except (ImportError, ValueError):
        pass
    _mock = _make_mock_package(_pkg)
    sys.modules[_pkg] = _mock  # type: ignore[assignment]


# Fix: upstream base classes (ServiceContext, WebSocketHandler) must be real classes so
# that AppServiceContext / AppWebSocketHandler inherit properly. When these upstream
# modules are mocked as MagicMocks, Python's metaclass mechanism turns the subclasses
# into MagicMocks too — breaking MagicMock(spec=SubClass) on Python 3.12.
if "open_llm_vtuber.service_context" in sys.modules and isinstance(
    sys.modules["open_llm_vtuber.service_context"], MagicMock
):

    class _ServiceContextStub:
        def __init__(self) -> None:
            self.vad_engine = None
            self.tts_engine = None
            self.character_config = MagicMock()
            self.agent_engine = None
            self.system_prompt = ""
            self.tool_manager = None
            self.tool_executor = None
            self.client_contexts: dict = {}

        def init_vad(self, vad_config: object) -> None:
            pass

        def init_tts(self, tts_config: object) -> None:
            pass

        async def load_from_config(self, config: object) -> None:
            pass

        async def close(self) -> None:
            pass

        async def construct_system_prompt(self, persona_prompt: str) -> str:
            return ""

    sys.modules["open_llm_vtuber.service_context"].ServiceContext = _ServiceContextStub

if "open_llm_vtuber.websocket_handler" in sys.modules and isinstance(
    sys.modules["open_llm_vtuber.websocket_handler"], MagicMock
):

    class _WebSocketHandlerStub:
        def __init__(self, default_context_cache: object) -> None:
            self.default_context_cache = default_context_cache
            self.client_contexts: dict = {}
            self.client_connections: dict = {}
            self.chat_group_manager = MagicMock()
            self.current_conversation_tasks: dict = {}
            self.received_data_buffers: dict = {}

        def _init_message_handlers(self) -> dict:
            # Upstream handler stubs — tests check for their presence.
            return {
                "text-input": MagicMock(),
                "heartbeat": MagicMock(),
                "interrupt-signal": MagicMock(),
            }

        async def handle_new_connection(self, websocket: object, client_uid: str) -> None:
            pass

        async def handle_disconnect(self, client_uid: str) -> None:
            pass

        async def handle_websocket_communication(self, websocket: object, client_uid: str) -> None:
            pass

        async def _handle_conversation_trigger(
            self, websocket: object, client_uid: str, data: object
        ) -> None:
            pass

    sys.modules["open_llm_vtuber.websocket_handler"].WebSocketHandler = _WebSocketHandlerStub


def _register_src_module(name: str) -> None:
    """tests/<name>/__init__.py가 pytest에 의해 '<name>' 패키지로 잘못 인식되는 것을 방지.

    src/<name>을 올바른 '<name>' 모듈로 sys.modules에 미리 등록한다.
    upstream 없는 개발 환경(macOS 등)에서 로드 실패 시 경고만 출력하고 계속 진행.
    """
    import importlib.util as _iu

    if name in sys.modules:
        return

    _spec = _iu.spec_from_file_location(
        name,
        str(_SRC / name / "__init__.py"),
        submodule_search_locations=[str(_SRC / name)],
    )
    if not (_spec and _spec.loader):
        return

    _mod = _iu.module_from_spec(_spec)
    sys.modules[name] = _mod
    try:
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    except Exception as _exc:
        # upstream 미설치 환경에서 일부 src 모듈이 로드 실패할 수 있다.
        # 해당 모듈의 tests가 직접 실행될 때 import 오류가 다시 발생하므로
        # 여기서는 경고만 남기고 계속 진행한다.
        import warnings

        warnings.warn(
            f"conftest: src/{name} 모듈 사전 등록 실패 ({type(_exc).__name__}: {_exc}). "
            "해당 모듈 테스트는 import 오류가 날 수 있음.",
            stacklevel=2,
        )
        # 실패한 모듈을 sys.modules에 남겨두면 이후 import 시 혼란을 줄 수 있으므로 제거.
        sys.modules.pop(name, None)


for _src_mod_name in ("app", "vad", "asr", "tts"):
    _register_src_module(_src_mod_name)
