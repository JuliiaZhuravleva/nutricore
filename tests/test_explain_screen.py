"""«ℹ️ Как это работает» (G5-R22): reached from settings, one screen per topic."""

import re
from datetime import UTC, datetime

import pytest
from telegram import CallbackQuery, Update, User

from app.services import explain_screen as explain
from app.services import food_groups, settings_screen
from app.services import telegram as tg


def _buttons(screen):
    return [b for row in screen.markup.inline_keyboard for b in row]


def _tap(data):
    return explain.handle_callback(None, 42, data)


def test_the_settings_hub_opens_the_list_and_keeps_its_row_budget():
    hub = settings_screen.screen(False, "Asia/Tbilisi", datetime.now(UTC))
    rows = hub.markup.inline_keyboard
    button = next(b for b in _buttons(hub) if b.text == "ℹ️ Как это работает")
    assert button.callback_data == explain.OPEN
    assert len(rows) <= 4 and all(len(row) <= 3 for row in rows)
    assert explain.handle_callback(None, 42, button.callback_data).text.startswith(
        "<b>ℹ️ Как это работает</b>"
    )


def test_the_list_offers_every_topic_and_goes_back_to_settings():
    screen = _tap(explain.OPEN)
    labels = [b.text for b in _buttons(screen)]
    assert labels == [t.label for t in explain.TOPICS.values()] + ["⬅️ Настройки"]
    assert (
        _buttons(screen)[-1].callback_data == settings_screen.CALLBACK_PREFIX + "open"
    )


@pytest.mark.parametrize("key", list(explain.TOPICS))
def test_every_topic_says_what_how_why_and_source(key):
    topic = explain.TOPICS[key]
    screen = _tap(
        next(
            b for b in _buttons(_tap(explain.OPEN)) if b.text == topic.label
        ).callback_data
    )
    assert screen.text.startswith(f"<b>{topic.label}</b>")
    for part in ("Что это.", "Как работает.", "Откуда идея.", "Источник:"):
        assert part in screen.text
    assert [b.text for b in _buttons(screen)] == ["⬅️ Как это работает"]
    assert _buttons(screen)[0].callback_data == explain.OPEN
    # Telegram refuses a message over 4096 characters and tags it does not know.
    assert len(screen.text) < 4096
    assert set(re.findall(r"</?(\w+)", screen.text)) <= {"b", "i"}
    assert not re.search(r"&(?!amp;|lt;|gt;)", screen.text)


def test_the_groups_topic_names_every_group_the_bot_has():
    text = explain.TOPICS["groups"].what
    for mark, _label, word in food_groups.GROUPS.values():
        assert f"{mark} {word}" in text


def test_a_routine_topic_stays_out_of_supplement_advice():
    # G5-R08: the only mention is the promise not to advise.
    text = _tap(explain._TOPIC + "routines").text
    assert text.count("добавк") == 1
    assert "ничего не советует про добавки" in text


@pytest.mark.parametrize("data", ["hw:t:gone", "hw:nonsense", "hw:t:"])
def test_a_stale_button_lands_on_the_list_with_a_notice(data):
    screen = _tap(data)
    assert screen.text.startswith("<b>ℹ️ Как это работает</b>")
    assert screen.toast == "Этой темы уже нет, вот список"


def test_the_bot_routes_the_prefix_to_the_explanation_screen():
    application = tg.create_bot_application()
    query = CallbackQuery("1", User(42, "u", False), "chat", data=explain.OPEN)
    update = Update(1, callback_query=query)
    matched = [h for h in application.handlers[0] if h.check_update(update)]
    assert matched and matched[0].callback is tg.explain_callback
