from __future__ import annotations
from enum import Enum
from pathlib import Path
from typing import NamedTuple, Optional
import _mecab


class Span(NamedTuple):
    start: int
    end: int


class Feature(NamedTuple):
    pos: str
    semantic: Optional[str] = None
    has_jongseong: Optional[bool] = None
    reading: Optional[str] = None
    type: Optional[str] = None
    start_pos: Optional[str] = None
    end_pos: Optional[str] = None
    expression: Optional[str] = None

    @classmethod
    def _from_feature(cls, feature: str) -> "Feature":
        values = feature.split(",")
        assert len(values) == 8
        d = {
            field: value if value != "*" else None for field, value in zip(Feature._fields, values)
        }
        if d["has_jongseong"] == "T":
            d["has_jongseong"] = True
        elif d["has_jongseong"] == "F":
            d["has_jongseong"] = False
        return cls(**d)

    def __str__(self) -> str:
        feature = {k: v if v is not None else "*" for k, v in self._asdict().items()}
        feature["has_jongseong"] = str(feature["has_jongseong"])[0]
        return ",".join(feature.values())


class Morpheme(NamedTuple):
    span: Span
    surface: str
    feature: Feature

    @property
    def pos(self) -> str:
        return self.feature.pos

    @classmethod
    def _from_node(cls, span: tuple, node: _mecab.Node) -> "Morpheme":
        return cls(
            surface=node.surface, feature=Feature._from_feature(node.feature), span=Span(*span)
        )


class Dictionary(NamedTuple):
    class Type(Enum):
        SYSTEM = _mecab.MECAB_SYS_DIC
        USER = _mecab.MECAB_USR_DIC
        UNNOWN = _mecab.MECAB_UNK_DIC

    path: Path
    number_of_words: int
    type: Type
    version: int

    @classmethod
    def _from_dictionary_info(cls, dictionary_info: _mecab.dictionary_info) -> list:
        result = []
        while dictionary_info is not None:
            result.append(
                cls(
                    path=Path(dictionary_info.filename),
                    number_of_words=dictionary_info.size,
                    type=cls.Type(dictionary_info.type),
                    version=dictionary_info.version,
                )
            )
            dictionary_info = dictionary_info.next
        return result
