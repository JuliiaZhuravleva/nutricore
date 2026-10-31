"""The custom emoji set (``render/emoji``): drawn marks match the bot's strings."""

import importlib.util
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "build_tiles", ROOT / "render" / "emoji" / "build_tiles.py"
)
build_tiles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_tiles)

# Arrows and dingbats below the emoji planes that the bot does use as marks.
_LOW_MARKS = "↩⬅➕➖✖"
# Text here, not marks: a bar, a divider line and a plain state tick.
_TEXT = "▰▱✓─"


def _is_mark(ch: str) -> bool:
    if ch in _TEXT:
        return False
    return ch in _LOW_MARKS or (unicodedata.category(ch) == "So" and ord(ch) >= 0x2300)


def _marks_in_bot() -> set:
    found = set()
    for path in (ROOT / "app" / "services").glob("*.py"):
        found |= {ch for ch in path.read_text(encoding="utf-8") if _is_mark(ch)}
    return found


def test_scan_sees_known_marks():
    """Control: an empty scan would pass the coverage test below for nothing."""
    assert {"🍽", "⬅", "💊", "⚖"} <= _marks_in_bot()


def test_every_mark_in_the_bot_is_drawn():
    drawn = {mark for mark, _, _ in build_tiles.SET}
    assert _marks_in_bot() - drawn == set()


def test_every_mark_outside_the_services_is_drawn_too():
    """bot.py and the rest of ``app``: the scan above reads ``app/services`` only."""
    found = set()
    for path in [ROOT / "bot.py", *(ROOT / "app").rglob("*.py")]:
        found |= {ch for ch in path.read_text(encoding="utf-8") if _is_mark(ch)}
    assert found >= _marks_in_bot()  # control: it reads at least as much
    assert found - {mark for mark, _, _ in build_tiles.SET} == set()


def test_every_mark_of_slice_o_in_the_spec_is_drawn():
    """Section 6, the copy and the keyboards a person will see: a mark drawn late would
    come out as the stock emoji beside the set's."""
    spec = (ROOT / "docs" / "stages" / "05-gentle-review-screens.md").read_text(
        encoding="utf-8"
    )
    start = spec.index("**`O1`**") if "**`O1`**" in spec else spec.index("`O1`")
    section = spec[start : spec.index("### 10.3")]
    blocks = re.findall(r"```(?:tg|kb)\n(.*?)```", section, re.DOTALL)
    assert len(blocks) > 30  # control: the blocks were found
    marks = {ch for block in blocks for ch in block if _is_mark(ch)}
    assert {"🍲", "🔗", "🕘", "➕"} <= marks  # control: slice O's own
    assert marks - {mark for mark, _, _ in build_tiles.SET} == set()


def test_info_is_the_one_mark_the_scan_does_not_call_a_mark():
    """``ℹ`` is a letter to Unicode (Ll), so the scan never asks for it. It is a shipped
    mark on the stock emoji, and section 11 says slice O adds no use of it: when the set
    draws it, this goes red and it joins ``_LOW_MARKS``."""
    assert unicodedata.category("ℹ") == "Ll" and not _is_mark("ℹ")
    assert "ℹ" not in {mark for mark, _, _ in build_tiles.SET}
    dishes = (ROOT / "app" / "services" / "dishes.py").read_text(encoding="utf-8")
    favorites = (ROOT / "app" / "services" / "favorites.py").read_text(encoding="utf-8")
    assert "ℹ" not in dishes + favorites


def test_control_the_scan_sees_a_mark_nobody_drew():
    undrawn = "🦖"
    assert _is_mark(undrawn)
    assert undrawn not in {mark for mark, _, _ in build_tiles.SET}
    assert not _is_mark("я") and not _is_mark("✓")  # must not refuse plain text


def test_every_row_has_a_drawn_icon_and_a_fill():
    js = (ROOT / "render" / "templates" / "icons.js").read_text(encoding="utf-8")
    js += (ROOT / "render" / "emoji" / "marks.js").read_text(encoding="utf-8")
    for _, icon, keywords in build_tiles.SET:
        assert re.search(rf"\b{icon}: '", js) or re.search(rf"\b{icon}: \w", js), icon
        assert re.search(rf'\b{icon}: ("#|"transparent|\{{ name)', js), icon
        assert keywords


def test_a_row_is_one_plain_emoji():
    """custom_emoji.py maps by the sticker's first emoji, without U+FE0F."""
    for mark, _, _ in build_tiles.SET:
        assert len(mark) == 1, mark
    assert len({m for m, _, _ in build_tiles.SET}) == len(build_tiles.SET)
    assert len(build_tiles.SET) <= 200
