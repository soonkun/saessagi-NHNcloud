#!/usr/bin/env python3
"""설치 후 실행: mecab_shim 적용 + MeCab Tagger 스텁 패치.

install.sh / install.bat에서 venv 생성 후 호출된다.
"""
from __future__ import annotations

import os
import platform
import shutil
import site
import sys
from pathlib import Path


def _sp() -> Path:
    """현재 Python 인터프리터의 site-packages 디렉토리."""
    paths = site.getsitepackages()
    for p in paths:
        if "site-packages" in p:
            return Path(p)
    return Path(paths[0])


def apply_mecab_shim() -> None:
    """macOS 전용: mecab_shim 패키지를 site-packages에 설치."""
    if platform.system() != "Darwin":
        print("[post_install] mecab_shim: macOS 전용, 건너뜀")
        return

    sp = _sp()
    vendor = Path(__file__).parent.parent / "vendor" / "mecab_shim"
    if not vendor.exists():
        print(f"[post_install] vendor/mecab_shim 없음, 건너뜀: {vendor}")
        return

    # mecab_shim/ 디렉토리 복사
    dest_shim = sp / "mecab_shim"
    if dest_shim.exists():
        shutil.rmtree(dest_shim)
    shutil.copytree(vendor / "mecab_shim", dest_shim / "mecab")
    # .pth 파일 생성
    pth = sp / "mecab_shim.pth"
    pth.write_text("mecab_shim\n", encoding="utf-8")
    print(f"[post_install] mecab_shim 설치 완료: {sp}")


def patch_mecab_tagger() -> None:
    """macOS 전용: MeCab/__init__.py에 Tagger 스텁 추가."""
    if platform.system() != "Darwin":
        return

    sp = _sp()
    # HFS+ 대소문자 비구분 — MeCab 또는 mecab 디렉토리 탐색
    mecab_init = None
    for name in ("MeCab", "mecab"):
        candidate = sp / name / "__init__.py"
        if candidate.exists():
            mecab_init = candidate
            break

    if mecab_init is None:
        print("[post_install] MeCab/__init__.py 없음, Tagger 패치 건너뜀")
        return

    content = mecab_init.read_text(encoding="utf-8")
    if "class Tagger" in content:
        print("[post_install] MeCab Tagger 이미 패치됨")
        return

    tagger_stub = '''

class Tagger:
    """Legacy MeCab.Tagger() stub (melo Japanese loader 모듈 레벨 호출 충족용).
    Korean 전용 배포에서는 실제로 호출되지 않는다."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def parse(self, text: str) -> str:
        raise RuntimeError("MeCab.Tagger.parse()는 스텁입니다. Korean 전용 배포에서는 사용 불가.")
'''
    mecab_init.write_text(content + tagger_stub, encoding="utf-8")
    print(f"[post_install] MeCab Tagger 스텁 패치 완료: {mecab_init}")


def main() -> None:
    print(f"[post_install] Python {sys.version}")
    print(f"[post_install] platform: {platform.system()} {platform.machine()}")
    apply_mecab_shim()
    patch_mecab_tagger()
    print("[post_install] 완료")


if __name__ == "__main__":
    main()
