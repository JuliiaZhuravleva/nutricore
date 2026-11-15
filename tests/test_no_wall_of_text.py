"""No screen is a heap of text (owner, 2026-11-15: «never, anywhere»).

The rule, on what Telegram shows open (collapsed ``<blockquote expandable>`` quotes are
what a person opens on purpose):

- a block between blank lines holds at most ``PROSE`` chars of prose, a line over 60
  chars that is not a ``• `` point;
- no line, open or folded, runs over ``LINE`` chars, about three phone lines.

The screens below are the long ones built without a database; the sim walk
(``scripts/sim.sh``, ``tests/sim/README.md``) is the way to look at all of them.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime

import pytest

from app.services import explain_screen as explain
from app.services import feedback_screen, food_lists, reminders, welcome_screen

PROSE = 160
LINE = 130
_FOLD = re.compile(r"<blockquote expandable>(.*?)</blockquote>", re.S)


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def walls(text: str) -> list[str]:
    """What makes ``text`` a wall of text; empty when it reads as short lines."""
    found = []
    shown = _plain(_FOLD.sub("\n", text))
    for block in re.split(r"\n\s*\n", shown):
        lines = [line for line in block.split("\n") if line.strip()]
        prose = sum(len(ln) for ln in lines if len(ln) > 60 and not ln.startswith("• "))
        if prose > PROSE:
            found.append(f"a block of {prose} chars of prose: {lines[0][:40]!r}")
    for part in [shown, *map(_plain, _FOLD.findall(text))]:
        found += [
            f"a line of {len(ln)} chars: {ln[:40]!r}"
            for ln in part.split("\n")
            if len(ln) > LINE
        ]
    return found


# --- the rule itself: it catches the old screens and passes short points --------------

_OLD_REMINDER = (
    "🔔 <b>Еда за сегодня</b>\n"
    "Можно не отвечать. Если что-то вспоминается, можно рассказать голосовым, "
    "написать или нажать на блюдо из ⭐, примерно и не всё, а время можно назвать "
    "словами, и без времени запись встанет на этот день.\n\n"
    "<i>Приходит в 21:00 по времени Тбилиси (UTC+4), без звука. Не приходит, если за "
    "три часа до него были записи. Выключить — кнопкой ✖. Текст на экране блокировки "
    "скрывается в настройках уведомлений Telegram.</i>"
)


def test_the_rule_catches_a_paragraph_of_the_old_reminder():
    found = walls(_OLD_REMINDER)
    assert any(f.startswith("a block of") for f in found), found
    assert any(f.startswith("a line of") for f in found), found


def test_the_rule_catches_a_long_line_inside_a_folded_quote():
    long = "Очень длинная строка без конца, " * 6
    assert walls(f"<b>Т</b>\n<blockquote expandable>{long}</blockquote>")


def test_the_rule_passes_short_points_and_a_long_folded_list():
    points = "\n".join(f"• Пункт номер {i}, короткий и понятный." for i in range(12))
    text = f"<b>Т</b>\nОдна строка о том, что это.\n\n{points}\n"
    assert walls(text + f"<blockquote expandable>{points}</blockquote>") == []


# --- the screens -------------------------------------------------------------------


def _screens():
    for key, topic in explain.TOPICS.items():
        yield f"topic:{key}", explain.topic_screen(topic).text
        yield f"topic:{key}:no-ref", explain.topic_screen(topic, nutrients=False).text
    yield "nutrients", explain.nutrients_screen().text
    for key, item in explain.NUTRIENTS.items():
        yield f"nutrient:{key}", explain.nutrient_screen(item).text
    yield "welcome", welcome_screen.welcome().text
    yield "feedback", feedback_screen.prompt_screen().text
    yield "reliable", food_lists.reliable_screen(None).text
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)
    yield "reminders:settings", reminders.settings_view(None, None, now).text
    footnote = reminders._FOOTNOTE.format(
        slot="21:00", zone="Тбилиси (UTC+4)", sound="без звука"
    )
    push = "\n".join([reminders.EVENING_TITLE, reminders._FIRST, footnote])
    yield "reminders:push", push


@pytest.mark.parametrize("name,text", list(_screens()), ids=lambda v: str(v)[:30])
def test_no_screen_is_a_wall_of_text(name, text):
    assert text.strip(), name  # an empty builder would pass the rule unseen
    assert walls(text) == [], name
