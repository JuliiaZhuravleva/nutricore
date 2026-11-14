"""Names matched by word stem: «гречку» is «гречка», «зарядку» is «Зарядка».

One rule for the dish planner and the routine ticks, so the two agree on what
«the same word» means.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[^\W\d_]+")


def words(text: str | None) -> list[str]:
    """The letter-words of ``text``, casefolded, ё as е."""
    return _WORD.findall((text or "").casefold().replace("ё", "е"))


def akin(a: str, b: str, least: int = 4) -> bool:
    """One word of two forms: «гречку» and «гречка». Short words must be the same."""
    if a == b:
        return True
    k = max(min(least, len(a), len(b)), min(len(a), len(b)) - 2)
    return len(a) >= 3 and len(b) >= 3 and a[:k] == b[:k]
