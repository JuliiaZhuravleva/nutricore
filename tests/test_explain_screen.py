import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    assert labels == [t.label for t in explain.TOPICS.values()] + [
        explain.NUTRIENTS_TITLE,
        "⬅️ Настройки",
    ]
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
    assert _buttons(screen)[-1].text == "⬅️ Как это работает"
    assert _buttons(screen)[-1].callback_data == explain.OPEN
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


def test_the_routines_topic_opens_the_reference():
    screen = _tap(explain._TOPIC + "routines")
    assert explain.NUTRIENTS_OPEN in [b.callback_data for b in _buttons(screen)]
    assert "B12" in screen.text and "не советует" in screen.text
    assert explain.NUTRIENTS_OPEN not in [
        b.callback_data for b in _buttons(_tap(explain._TOPIC + "week"))
    ]


def test_the_reference_says_what_it_is_and_offers_every_nutrient():
    screen = _tap(explain.NUTRIENTS_OPEN)
    assert "ничего не назначает" in screen.text and "39923894" in screen.text
    rows = screen.markup.inline_keyboard
    assert max(len(row) for row in rows) <= 3 and len(rows) <= 5
    assert [b.text for b in _buttons(screen)] == [
        *(item.label for item in explain.NUTRIENTS.values()),
        "⬅️ Как это работает",
    ]


@pytest.mark.parametrize("key", list(explain.NUTRIENTS))
def test_each_nutrient_has_links_limits_and_leaves_the_decision_open(key):
    screen = _tap(explain._NUTRIENT + key)
    assert re.search(
        r'<a href="https://pubmed\.ncbi\.nlm\.nih\.gov/\d+/">', screen.text
    )
    assert "<b>Чего не знаем.</b>" in screen.text
    assert "решать тебе и врачу" in screen.text
    assert len(screen.text) < 4096
    assert _buttons(screen)[-1].callback_data == explain.NUTRIENTS_OPEN
    assert _buttons(screen)[-1].text == "⬅️ Витамины и минералы"
    assert len(explain._NUTRIENT + key) <= 64


@pytest.mark.parametrize("key", list(explain.NUTRIENTS))
def test_no_nutrient_screen_gives_a_dose_or_tells_to_take_anything(key):
    text = _tap(explain._NUTRIENT + key).text
    assert not re.search(r"\d\s*(мкг|мг|ме|МЕ)(?!/)", text)
    assert not re.search(
        r"(?i)принимай|пей |стоит принимать|рекомендуем|советуем", text
    )


def test_a_nutrient_gone_after_a_release_shows_the_list_with_a_notice():
    screen = _tap(explain._NUTRIENT + "gone")
    assert screen.toast == "Этой темы уже нет, вот список"


def test_links_in_the_reference_open_no_preview_over_the_screen(monkeypatch):
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=True))
    query = SimpleNamespace(
        data=explain._NUTRIENT + "b12",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=42)
    )
    asyncio.run(tg.explain_callback(update, SimpleNamespace()))
    (text,), kwargs = query.edit_message_text.await_args
    assert "pubmed.ncbi.nlm.nih.gov/39373282" in text
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["link_preview_options"].is_disabled is True
