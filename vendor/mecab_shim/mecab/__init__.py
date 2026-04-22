# mecab compatibility shim
# g2pkk expects: import mecab; mecab.MeCab()
# MeCab/mecab.py expects: from mecab.types import ...; from mecab.utils import ...
# __getattr__ prevents circular import when MeCab/mecab.py is mid-load.

def __getattr__(name: str) -> object:
    if name in ("MeCab", "MeCabError", "mecabrc_path"):
        from MeCab.mecab import MeCab as _C, MeCabError as _E, mecabrc_path as _p
        globals()["MeCab"] = _C
        globals()["MeCabError"] = _E
        globals()["mecabrc_path"] = _p
        return globals()[name]
    raise AttributeError(f"module 'mecab' has no attribute {name!r}")
