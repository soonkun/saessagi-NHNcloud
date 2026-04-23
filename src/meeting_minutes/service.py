# src/meeting_minutes/service.py
"""M_13 MeetingMinutes — 파사드 서비스 클래스."""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import MeetingFileNotFoundError
from .generator import MeetingDraftGenerator
from .hwpx_writer import HwpxWriter
from .types import PageCount

logger = logging.getLogger(__name__)


class MeetingMinutesService:
    """생성·저장·URL 발급·만료 청소를 묶는 파사드.

    - generate(): tool 핸들러가 호출. LLM → HWPX → file_id 반환.
    - resolve(): 라우터(`/download/{file_id}`)가 호출. file_id → Path 변환.
    - cleanup_expired(): APScheduler 잡이 1시간마다 호출. 24h 경과 파일 삭제.
    """

    def __init__(
        self,
        agent: Any,  # GemmaChatAgent (Protocol _ChatAgentLike 만족)
        template_path: Path,  # data/Template/회의 결과보고 템플릿.hwpx
        temp_dir: Path,  # data/temp/
        download_base_url: str,  # 'http://127.0.0.1:12393'
        *,
        ttl_hours: int = 24,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=timezone.utc),
    ) -> None:
        self._generator = MeetingDraftGenerator(agent)
        self._writer = HwpxWriter(template_path)
        self._temp_dir = temp_dir
        self._download_base_url = download_base_url.rstrip("/")
        self._ttl_seconds = ttl_hours * 3600
        self._clock = clock

        # temp_dir 생성 (없으면)
        temp_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"MeetingMinutesService 초기화: temp_dir={temp_dir}, "
            f"ttl_hours={ttl_hours}, download_base_url={download_base_url}"
        )

    async def generate(
        self,
        transcript: str,
        pages: PageCount,
    ) -> dict[str, str]:
        """녹취록 → HWPX 파일 생성 → file_id 반환.

        Returns:
            {"file_id": uuid, "download_url": full_url, "expires_at": iso}

        Raises:
            MeetingMinutesError 계열.
        """
        draft = await self._generator.generate(transcript, pages)

        file_id = str(uuid.uuid4())
        out_path = self._temp_dir / f"{file_id}.hwpx"

        self._writer.write(draft, out_path)

        now = self._clock()
        expires_at = now.timestamp() + self._ttl_seconds
        expires_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)

        download_url = f"{self._download_base_url}/download/{file_id}"
        logger.info(f"회의록 생성 완료: file_id={file_id}, expires_at={expires_dt.isoformat()}")
        return {
            "file_id": file_id,
            "download_url": download_url,
            "expires_at": expires_dt.isoformat(),
        }

    def resolve(self, file_id: str) -> Path:
        """file_id → 실제 파일 경로 변환.

        Raises:
            ValueError: file_id가 UUIDv4 형식이 아닌 경우.
            MeetingFileNotFoundError: 파일이 존재하지 않거나 TTL 초과.
        """
        # UUID v4 형식 검증 (path traversal 방어)
        try:
            parsed = uuid.UUID(file_id, version=4)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"유효하지 않은 file_id 형식: {file_id!r}") from exc

        # UUID 정규화 (하이픈 없는 형식 등 방어)
        safe_id = str(parsed)
        path = self._temp_dir / f"{safe_id}.hwpx"

        if not path.exists():
            raise MeetingFileNotFoundError(f"파일이 존재하지 않거나 만료되었습니다: {file_id}")

        # TTL 확인
        mtime = path.stat().st_mtime
        if time.time() - mtime > self._ttl_seconds:
            # 만료된 파일 즉시 삭제
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise MeetingFileNotFoundError(f"파일이 만료되었습니다: {file_id}")

        return path

    def cleanup_expired(self) -> int:
        """만료된 HWPX 임시 파일을 삭제한다.

        Returns:
            삭제된 파일 수.
        """
        deleted = 0
        now = time.time()
        try:
            with os.scandir(self._temp_dir) as it:
                for entry in it:
                    if not entry.name.endswith(".hwpx"):
                        continue
                    try:
                        mtime = entry.stat().st_mtime
                        age = now - mtime
                        if age > self._ttl_seconds:
                            os.unlink(entry.path)
                            deleted += 1
                            logger.debug(f"만료 파일 삭제: {entry.name} (age={age:.0f}s)")
                    except OSError as exc:
                        logger.warning(f"파일 삭제 실패: {entry.name}: {exc}")
        except OSError as exc:
            logger.error(f"cleanup_expired: temp_dir 접근 실패: {exc}")

        if deleted:
            logger.info(f"cleanup_expired: {deleted}개 파일 삭제")
        return deleted

    def set_agent(self, agent: Any) -> None:
        """init_agent 이후 GemmaChatAgent를 주입한다.

        agent=None으로 생성된 서비스에 agent를 사후 배선할 때 사용.
        None이 아닌 agent만 허용 (좀비 상태 방지).
        """
        if agent is None:
            raise ValueError("agent must not be None")
        self._generator._agent = agent

    async def aclose(self) -> None:
        """AppServiceContext.close 정리 훅. 임시 파일은 보존(다음 기동의 cleanup가 처리)."""
        logger.debug("MeetingMinutesService.aclose() 완료 (임시 파일 보존)")
