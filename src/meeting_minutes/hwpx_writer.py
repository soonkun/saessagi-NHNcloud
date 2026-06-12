# src/meeting_minutes/hwpx_writer.py
"""M_13 MeetingMinutes — HWPX 템플릿 기반 파일 생성기."""

from __future__ import annotations

import copy
import io
import logging
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from .errors import HwpxTemplateError, HwpxWriteError
from .types import MeetingDraft

logger = logging.getLogger(__name__)

# 반드시 존재해야 하는 placeholder 목록
_REQUIRED_PLACEHOLDERS = frozenset(
    {
        "{{TITLE}}",
        "{{DATE_DEPT}}",
        "{{DT_PLACE}}",
        "{{ATTENDEES}}",
        "{{SUMMARY_O}}",
        "{{SUMMARY_DASH}}",
        "{{SUMMARY_STAR}}",
        "{{DETAIL_O}}",
        "{{DETAIL_DASH}}",
        "{{DETAIL_STAR}}",
        "{{NEXT_O}}",
    }
)

# 반복이 아닌 단순 치환 placeholder (1:1 매핑)
_SIMPLE_PLACEHOLDERS = frozenset({"{{TITLE}}", "{{DATE_DEPT}}", "{{DT_PLACE}}", "{{ATTENDEES}}"})

# 반복(clone) placeholder
_CLONE_PLACEHOLDERS = frozenset(
    {
        "{{SUMMARY_O}}",
        "{{SUMMARY_DASH}}",
        "{{SUMMARY_STAR}}",
        "{{DETAIL_O}}",
        "{{DETAIL_DASH}}",
        "{{DETAIL_STAR}}",
        "{{NEXT_O}}",
    }
)


def _get_full_text(p: etree._Element, ns: dict[str, str]) -> str:
    """단락의 모든 hp:t 텍스트를 연결해 반환."""
    parts: list[str] = []
    for t in p.findall(".//hp:t", ns):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _set_para_text(p: etree._Element, ns: dict[str, str], text: str) -> None:
    """단락의 첫 hp:t 텍스트를 교체하고 나머지는 빈 문자열로."""
    t_elems = p.findall(".//hp:t", ns)
    if not t_elems:
        return
    t_elems[0].text = text
    for t in t_elems[1:]:
        t.text = ""


class HwpxWriter:
    """HWPX 템플릿 ZIP을 풀고 section0.xml에 단락을 삽입한 뒤 재압축한다.

    템플릿은 클래스 인스턴스당 1회 메모리에 로드(불변). `write()` 호출마다
    template_bytes를 in-memory ZIP으로 풀어 새 인스턴스를 만든다.
    """

    def __init__(self, template_path: Path) -> None:
        """HWPX 템플릿을 로드하고 placeholder 단락 위치를 캐시한다.

        Raises:
            HwpxTemplateError: template_path 부재, ZIP 손상, section0.xml 누락,
                               또는 필수 placeholder 누락.
        """
        if not template_path.exists():
            raise HwpxTemplateError(f"템플릿 파일이 존재하지 않습니다: {template_path}")

        try:
            self._template_bytes = template_path.read_bytes()
        except OSError as exc:
            raise HwpxTemplateError(f"템플릿 파일 읽기 실패: {exc}") from exc

        # ZIP 파일 검증
        try:
            with zipfile.ZipFile(io.BytesIO(self._template_bytes), "r") as zf:
                names = zf.namelist()
                if "Contents/section0.xml" not in names:
                    raise HwpxTemplateError(
                        f"템플릿에 Contents/section0.xml이 없습니다. 파일 목록: {names}"
                    )
                section0_bytes = zf.read("Contents/section0.xml")
        except zipfile.BadZipFile as exc:
            raise HwpxTemplateError(f"템플릿이 유효한 ZIP 파일이 아닙니다: {exc}") from exc

        # XML 파싱 및 네임스페이스 추출
        try:
            root = etree.fromstring(section0_bytes)
        except etree.XMLSyntaxError as exc:
            raise HwpxTemplateError(f"section0.xml XML 파싱 실패: {exc}") from exc

        # 런타임 네임스페이스 추출 (2011/2016 혼재 대응)
        self._nsmap: dict[str, str] = {}
        for prefix, uri in root.nsmap.items():
            if prefix is not None:
                self._nsmap[prefix] = uri

        hp_uri = self._nsmap.get("hp")
        if not hp_uri:
            raise HwpxTemplateError("section0.xml에 'hp' 네임스페이스가 없습니다.")

        ns: dict[str, str] = {"hp": hp_uri}

        # placeholder 노드 위치 캐시 (단락 전체 텍스트 기반으로 탐색)
        self._placeholder_cache: dict[str, dict[str, Any]] = {}
        paras = root.findall(".//hp:p", ns)
        for p in paras:
            text = _get_full_text(p, ns)
            for ph in _REQUIRED_PLACEHOLDERS:
                if ph in text:
                    parent = p.getparent()
                    if parent is not None:
                        idx = list(parent).index(p)
                        self._placeholder_cache[ph] = {
                            "node": p,
                            "parent": parent,
                            "idx": idx,
                        }

        # 누락된 placeholder 확인
        missing = _REQUIRED_PLACEHOLDERS - set(self._placeholder_cache.keys())
        if missing:
            raise HwpxTemplateError(f"필수 placeholder 누락: {sorted(missing)}")

        # 원본 ZIP 파일 목록 보존 (재압축 시 순서 유지)
        with zipfile.ZipFile(io.BytesIO(self._template_bytes), "r") as zf:
            self._original_names: list[str] = zf.namelist()

        logger.info(
            f"HwpxWriter 초기화 완료: template={template_path}, "
            f"placeholders={sorted(self._placeholder_cache.keys())}"
        )

    def write(self, draft: MeetingDraft, out_path: Path) -> None:
        """MeetingDraft를 HWPX 파일로 직렬화해 out_path에 저장한다.

        Raises:
            HwpxWriteError: lxml 파싱 실패 또는 I/O 오류.
            FileNotFoundError: out_path 부모 디렉토리 부재.
        """
        if not out_path.parent.exists():
            raise FileNotFoundError(f"출력 디렉토리가 존재하지 않습니다: {out_path.parent}")

        # 원본 ZIP에서 모든 파일 읽기
        try:
            with zipfile.ZipFile(io.BytesIO(self._template_bytes), "r") as zf:
                files: dict[str, bytes] = {name: zf.read(name) for name in self._original_names}
        except Exception as exc:
            raise HwpxWriteError(f"템플릿 ZIP 읽기 실패: {exc}") from exc

        # section0.xml 파싱
        try:
            root = etree.fromstring(files["Contents/section0.xml"])
        except etree.XMLSyntaxError as exc:
            raise HwpxWriteError(f"section0.xml XML 파싱 실패: {exc}") from exc

        hp_uri = self._nsmap.get("hp", "http://www.hancom.co.kr/hwpml/2011/paragraph")
        ns: dict[str, str] = {"hp": hp_uri}

        # placeholder 노드 다시 탐색 (deepcopy한 새 root에서)
        ph_nodes: dict[str, dict[str, Any]] = {}
        paras = root.findall(".//hp:p", ns)
        for p in paras:
            text = _get_full_text(p, ns)
            for ph in _REQUIRED_PLACEHOLDERS:
                if ph in text:
                    parent = p.getparent()
                    if parent is not None:
                        idx = list(parent).index(p)
                        ph_nodes[ph] = {"node": p, "parent": parent, "idx": idx}

        try:
            self._fill_document(root, draft, ns, ph_nodes)
        except Exception as exc:
            raise HwpxWriteError(f"문서 채우기 실패: {exc}") from exc

        # XML 직렬화
        try:
            new_xml: bytes = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
        except Exception as exc:
            raise HwpxWriteError(f"XML 직렬화 실패: {exc}") from exc

        files["Contents/section0.xml"] = new_xml

        # HWPX 재압축
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out_zip:
                # mimetype은 반드시 첫 번째, ZIP_STORED
                if "mimetype" in files:
                    info = zipfile.ZipInfo("mimetype")
                    info.compress_type = zipfile.ZIP_STORED
                    out_zip.writestr(info, files["mimetype"])

                for name in self._original_names:
                    if name == "mimetype":
                        continue
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    out_zip.writestr(info, files[name])

            out_path.write_bytes(buf.getvalue())
        except OSError as exc:
            raise HwpxWriteError(f"출력 파일 쓰기 실패: {exc}") from exc

        logger.info(f"HWPX 생성 완료: {out_path} ({out_path.stat().st_size} bytes)")

    def _fill_document(
        self,
        root: etree._Element,
        draft: MeetingDraft,
        ns: dict[str, str],
        ph_nodes: dict[str, dict[str, Any]],
    ) -> None:
        """draft 내용을 root XML 트리에 채워 넣는다."""
        # 1. 단순 placeholder 치환 (빈 값은 공문서 관행에 맞게 보정 — E-41)
        _set_para_text(ph_nodes["{{TITLE}}"]["node"], ns, draft.title)
        date_dept = f"{draft.date} {draft.department}".strip()
        _set_para_text(ph_nodes["{{DATE_DEPT}}"]["node"], ns, date_dept)
        dt_place = draft.datetime_place.strip() or draft.date
        _set_para_text(ph_nodes["{{DT_PLACE}}"]["node"], ns, f"○ 일시·장소 : {dt_place}")
        attendees = draft.attendees_str.strip() or "-"
        _set_para_text(ph_nodes["{{ATTENDEES}}"]["node"], ns, f"○ 참 석 자 : {attendees}")

        # 2. summary_items clone 삽입 (SUMMARY_O/DASH/STAR)
        self._insert_items(
            draft.summary_items,
            ph_nodes,
            ns,
            o_key="{{SUMMARY_O}}",
            dash_key="{{SUMMARY_DASH}}",
            star_key="{{SUMMARY_STAR}}",
            prefix="○ ",
        )

        # 3. detail_items clone 삽입 (DETAIL_O/DASH/STAR)
        self._insert_items(
            draft.detail_items,
            ph_nodes,
            ns,
            o_key="{{DETAIL_O}}",
            dash_key="{{DETAIL_DASH}}",
            star_key="{{DETAIL_STAR}}",
            prefix="○ ",
        )

        # 4. next_steps clone 삽입 (NEXT_O)
        self._insert_next_steps(draft.next_steps, ph_nodes, ns)

        # 5. 원본 placeholder 노드 제거
        for ph_key in _CLONE_PLACEHOLDERS:
            if ph_key in ph_nodes:
                entry = ph_nodes[ph_key]
                parent = entry["parent"]
                node = entry["node"]
                if node in list(parent):
                    parent.remove(node)
                    logger.debug(f"placeholder {ph_key} 원본 노드 제거")

        # 6. 빈 섹션의 고아 헤더 제거 — detail_items가 없으면 '주요내용' 헤더가
        #    내용 없이 남아 문서가 어색해진다 (E-41). generator의 normalize가
        #    detail_items를 채워 넣으므로 실제로 비는 경우는 ○가 1개뿐일 때 정도.
        if not draft.detail_items:
            self._remove_section_header(root, ns, "주요내용")
        if not draft.next_steps:
            self._remove_section_header(root, ns, "향후계획")

    @staticmethod
    def _remove_section_header(root: etree._Element, ns: dict[str, str], header_text: str) -> None:
        """본문에서 header_text를 담은 짧은 섹션 헤더 단락을 제거한다."""
        for p in root.findall(".//hp:p", ns):
            text = _get_full_text(p, ns).strip()
            # 양식에 따라 헤더에 부가 문구가 붙어 있을 수 있어 포함 매칭 (단, 짧은 단락만)
            if header_text in text and len(text) <= len(header_text) + 20:
                parent = p.getparent()
                if parent is not None:
                    parent.remove(p)
                    logger.debug(f"빈 섹션 헤더 제거: {header_text}")
                return

    def _insert_items(
        self,
        items: tuple[Any, ...],
        ph_nodes: dict[str, dict[str, Any]],
        ns: dict[str, str],
        o_key: str,
        dash_key: str,
        star_key: str,
        prefix: str,
    ) -> None:
        """SummaryItem 또는 DetailItem 목록을 clone 패턴으로 삽입."""
        o_entry = ph_nodes[o_key]
        o_parent = o_entry["parent"]
        o_template = o_entry["node"]
        o_idx = list(o_parent).index(o_template)

        dash_entry = ph_nodes[dash_key]
        dash_template = dash_entry["node"]

        star_entry = ph_nodes[star_key]
        star_template = star_entry["node"]

        insert_idx = o_idx  # 삽입 위치 (○ template 앞에 삽입 후 template 제거)

        for item in items:
            # ○ 단락 삽입
            o_node = copy.deepcopy(o_template)
            _set_para_text(o_node, ns, f"{prefix}{item.text}")
            o_parent.insert(insert_idx, o_node)
            insert_idx += 1

            # - 단락들 삽입.
            # LLM이 detail(*)을 여러 개 만들려고 동일한 - 텍스트의 sub를 복제하는
            # 경우가 있어, 직전과 같은 - 텍스트면 단락을 중복 삽입하지 않고
            # *만 이어 붙인다 (E-41).
            prev_dash_text: str | None = None
            for sub in item.subs:
                if sub.text != prev_dash_text:
                    dash_node = copy.deepcopy(dash_template)
                    _set_para_text(dash_node, ns, f"  - {sub.text}")
                    o_parent.insert(insert_idx, dash_node)
                    insert_idx += 1
                    prev_dash_text = sub.text

                # * 단락 삽입 (detail이 있는 경우)
                if sub.detail:
                    star_node = copy.deepcopy(star_template)
                    _set_para_text(star_node, ns, f"   * {sub.detail}")
                    o_parent.insert(insert_idx, star_node)
                    insert_idx += 1

    def _insert_next_steps(
        self,
        next_steps: tuple[Any, ...],
        ph_nodes: dict[str, dict[str, Any]],
        ns: dict[str, str],
    ) -> None:
        """향후계획 항목들을 clone 패턴으로 삽입."""
        next_entry = ph_nodes["{{NEXT_O}}"]
        next_parent = next_entry["parent"]
        next_template = next_entry["node"]
        next_idx = list(next_parent).index(next_template)

        for step in next_steps:
            next_node = copy.deepcopy(next_template)
            date_suffix = f" ({step.date})" if step.date else ""
            _set_para_text(next_node, ns, f"○ {step.text}{date_suffix}")
            next_parent.insert(next_idx, next_node)
            next_idx += 1
