#!/usr/bin/env python3
"""회의 결과보고 템플릿.hwpx에 placeholder 단락 삽입.

단락 구조:
  [0]  paraPrIDRef=80 styleIDRef=145 → 제목 헤더 (표 안, 건드리지 않음)
  [2]  paraPrIDRef=79 → 두 번째 "제목" 단락 → {{TITLE}}
  [4]  paraPrIDRef=81 → 날짜·소속과 단락 → {{DATE_DEPT}}
  [5]  paraPrIDRef=84 → " 개요" 섹션 헤더 → 변경 안 함
  [6]  paraPrIDRef=85 → 일시·장소 단락 → {{DT_PLACE}}
  [7]  paraPrIDRef=85 → 참석자 단락 → {{ATTENDEES}}
  [8]  paraPrIDRef=85 → "주요내용" ○ 단락 → {{SUMMARY_O}}
  [9]  paraPrIDRef=85 → - 단락 → {{SUMMARY_DASH}}
  [10] paraPrIDRef=86 → * 단락 → {{SUMMARY_STAR}}
  [11] paraPrIDRef=87 → " 세부내용" 섹션 헤더 → 변경 안 함
  [12] paraPrIDRef=85 → 세부내용 ○ 단락 → {{DETAIL_O}}
  [13] paraPrIDRef=85 → - 단락 → {{DETAIL_DASH}}
  [14] paraPrIDRef=86 → * 단락 → {{DETAIL_STAR}}
  [15]~[20] → 추가 견본 단락들 → 제거
  [21] paraPrIDRef=87 → " 향후계획" 섹션 헤더 → 변경 안 함
  [22] paraPrIDRef=85 → 향후계획 ○ 단락 → {{NEXT_O}}
  [23]~    → 사진 단락들 → 그대로 유지
"""

import io
import zipfile
from pathlib import Path

from lxml import etree

TEMPLATE = Path("data/Template/회의 결과보고 템플릿.hwpx")

# placeholder 매핑: 단락 인덱스 → placeholder 텍스트 (None이면 제거)
PLACEHOLDER_MAP = {
    2: "{{TITLE}}",
    4: "{{DATE_DEPT}}",
    6: "{{DT_PLACE}}",
    7: "{{ATTENDEES}}",
    8: "{{SUMMARY_O}}",
    9: "{{SUMMARY_DASH}}",
    10: "{{SUMMARY_STAR}}",
    12: "{{DETAIL_O}}",
    13: "{{DETAIL_DASH}}",
    14: "{{DETAIL_STAR}}",
    22: "{{NEXT_O}}",
}

# 제거할 단락 인덱스 (여분 견본 단락들)
REMOVE_INDICES = {15, 16, 17, 18, 19, 20}


def _set_text(p: etree._Element, ns: dict[str, str], text: str) -> None:
    """단락의 모든 hp:t 텍스트를 교체. 첫 hp:t에 text 설정, 나머지는 빈 문자열."""
    t_elems = p.findall(".//hp:t", ns)
    if not t_elems:
        return
    t_elems[0].text = text
    for t in t_elems[1:]:
        t.text = ""


def main() -> None:
    with zipfile.ZipFile(TEMPLATE, "r") as in_zip:
        original_names = in_zip.namelist()
        files: dict[str, bytes] = {name: in_zip.read(name) for name in original_names}

    # section0.xml 파싱
    xml_bytes = files["Contents/section0.xml"]
    root = etree.fromstring(xml_bytes)

    hp_ns = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    ns = {"hp": hp_ns}

    paras = root.findall(".//hp:p", ns)
    print(f"총 단락 수: {len(paras)}")

    # placeholder 삽입
    for idx, placeholder in PLACEHOLDER_MAP.items():
        if idx < len(paras):
            p = paras[idx]
            orig_texts = [t.text or "" for t in p.findall(".//hp:t", ns)]
            print(f"  [{idx}] '{' '.join(orig_texts)[:50]}' → '{placeholder}'")
            _set_text(p, ns, placeholder)
        else:
            print(f"  [{idx}] 인덱스 범위 초과, 스킵")

    # 제거 대상 단락 삭제 (역순으로 삭제해야 인덱스 안 밀림)
    paras = root.findall(".//hp:p", ns)  # 다시 조회
    to_remove = []
    for idx in sorted(REMOVE_INDICES, reverse=True):
        if idx < len(paras):
            p = paras[idx]
            parent = p.getparent()
            if parent is not None:
                orig_texts = [t.text or "" for t in p.findall(".//hp:t", ns)]
                print(f"  [{idx}] 제거: '{' '.join(orig_texts)[:50]}'")
                to_remove.append((parent, p))

    for parent, p in to_remove:
        parent.remove(p)

    # XML 직렬화
    new_xml = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    files["Contents/section0.xml"] = new_xml

    # HWPX 재압축 (mimetype 반드시 첫 번째 ZIP_STORED)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out_zip:
        # mimetype은 항상 첫 번째, ZIP_STORED
        if "mimetype" in files:
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            out_zip.writestr(info, files["mimetype"])

        for name in original_names:
            if name == "mimetype":
                continue
            compress_type = zipfile.ZIP_DEFLATED if name != "mimetype" else zipfile.ZIP_STORED
            info = zipfile.ZipInfo(name)
            info.compress_type = compress_type
            out_zip.writestr(info, files[name])

    # 덮어쓰기
    TEMPLATE.write_bytes(buf.getvalue())
    print(f"\n완료: {TEMPLATE} (크기={len(buf.getvalue())} bytes)")


if __name__ == "__main__":
    main()
