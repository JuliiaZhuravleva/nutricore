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


def _rows(screen):
    return [[b.text for b in row] for row in screen.markup.inline_keyboard]


def _pages():
    """Every page of the list, by the arrow a person taps."""
    pages = [_tap(explain.OPEN)]
    while True:
        arrow = [b for b in _buttons(pages[-1]) if b.text == "▶️"]
        if not arrow:
            return pages
        following = _tap(arrow[0].callback_data)
        if following.text == pages[0].text and _rows(following) == _rows(pages[0]):
            return pages
        pages.append(following)


def test_the_settings_hub_opens_the_list_and_keeps_its_row_budget():
    hub = settings_screen.screen(False, "Asia/Tbilisi", datetime.now(UTC))
    rows = hub.markup.inline_keyboard
    button = next(b for b in _buttons(hub) if b.text == "ℹ️ Как это работает")
    assert button.callback_data == explain.OPEN
    assert len(rows) <= 5 and all(len(row) <= 3 for row in rows)
    assert explain.handle_callback(None, 42, button.callback_data).text.startswith(
        "<b>ℹ️ Как это работает</b>"
    )


def test_the_list_offers_every_topic_and_goes_back_to_settings():
    assert _rows(_tap(explain.OPEN)) == [
        ["📊 Карточка дня", "📊 Карточка недели"],
        ["👣 Один шаг", "🏷 Группы еды"],
        ["💊 Рутины", "🍽 Приём и блюда"],
        ["◀️", "1/2", "▶️"],
        ["⬅️ Настройки"],
    ]
    pages = _pages()
    assert _rows(pages[1]) == [
        ["🧪 Витамины и минералы"],
        ["◀️", "2/2", "▶️"],
        ["⬅️ Настройки"],
    ]
    for page in pages:
        assert page.text.startswith("<b>ℹ️ Как это работает</b>")
        assert (
            _buttons(page)[-1].callback_data == settings_screen.CALLBACK_PREFIX + "open"
        )
    offered = [b.text for page in pages for b in _buttons(page)]
    for label in [t.label for t in explain.TOPICS.values()] + [explain.NUTRIENTS_TITLE]:
        assert offered.count(label) == 1, label


def test_every_page_of_the_list_keeps_the_layout_limits():
    for page in _pages():
        rows = _rows(page)
        assert len(rows) <= 5, rows
        for row in rows:
            budget = {1: 34, 2: 20, 3: 13}[len(row)]
            assert all(len(label) <= budget for label in row), row
        for button in _buttons(page):
            assert len(button.callback_data.encode()) <= 64


def test_the_pager_says_where_it_is_and_walks_round():
    first, second = _pages()
    pager = first.markup.inline_keyboard[-2]
    assert [b.callback_data for b in pager] == ["hw:p:1", "hw:nop", "hw:p:1"]
    pager = second.markup.inline_keyboard[-2]
    assert [b.callback_data for b in pager] == ["hw:p:0", "hw:nop", "hw:p:0"]
    assert _rows(_tap("hw:p:0")) == _rows(first)
    assert _tap("hw:p:1").toast is None


def test_a_list_that_fits_one_page_has_no_pager(monkeypatch):
    # Control for the pager above: the same screen, few topics.
    monkeypatch.setattr(explain, "TOPICS", {"day": explain.TOPICS["day"]})
    assert _rows(_tap(explain.OPEN)) == [
        ["📊 Карточка дня"],
        ["🧪 Витамины и минералы"],
        ["⬅️ Настройки"],
    ]


def test_the_middle_of_the_pager_does_nothing():
    screen = _tap("hw:nop")
    assert (screen.text, screen.markup, screen.toast) == ("", None, None)


def test_the_bot_answers_the_pager_tap_and_leaves_the_message(monkeypatch):
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=True))

    async def tap(data):
        query = SimpleNamespace(
            data=data, answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        update = SimpleNamespace(
            callback_query=query, effective_user=SimpleNamespace(id=42)
        )
        await tg.explain_callback(update, SimpleNamespace())
        return query

    middle = asyncio.run(tap("hw:nop"))
    middle.answer.assert_awaited_once()
    middle.edit_message_text.assert_not_awaited()
    # Control: the arrow does edit, through the same handler.
    arrow = asyncio.run(tap("hw:p:1"))
    assert "🧪 Витамины и минералы" in str(arrow.edit_message_text.await_args)


@pytest.mark.parametrize("data", ["hw:p:1", "hw:nop", "hw:t:meal"])
def test_the_bot_routes_the_new_taps_to_the_explanation_screen(data):
    application = tg.create_bot_application()
    query = CallbackQuery("1", User(42, "u", False), "chat", data=data)
    update = Update(1, callback_query=query)
    matched = [h for h in application.handlers[0] if h.check_update(update)]
    assert matched and matched[0].callback is tg.explain_callback


@pytest.mark.parametrize("data", ["hw:p:", "hw:p:x", "hw:p:-1", "hw:p:1:2", "hw:nop2"])
def test_a_malformed_page_lands_on_the_list_with_a_notice_and_a_warning(data, caplog):
    with caplog.at_level("WARNING", logger=explain.logger.name):
        screen = _tap(data)
    assert screen.text.startswith("<b>ℹ️ Как это работает</b>")
    assert screen.toast == "Этой темы уже нет, вот список"
    assert data in caplog.text


def test_a_page_past_the_end_after_a_release_still_opens_a_page():
    screen = _tap("hw:p:99")
    assert screen.text.startswith("<b>ℹ️ Как это работает</b>")
    assert screen.toast is None


def test_the_meal_topic_is_the_spec_copy():
    screen = _tap("hw:t:meal")
    assert screen.text == "\n\n".join(
        [
            "<b>🍽 Приём и блюда</b>",
            "<b>Что это.</b> Приём состоит из блюд, и каждое можно поправить отдельно.",
            "<b>Как работает.</b> В приёме блюдо записано таким, каким было в тот день. "
            "Правка блюда меняет будущие приёмы, прошлые остаются как были. "
            "Ранние записи не разделены на блюда: цифры у них на всю запись. "
            "Удалённый приём не считается ни на карточке дня, ни на карточке недели, "
            "но хранится, чтобы его можно было вернуть.",
            "<b>Откуда идея.</b> С фото лучше определяется, что было, чем сколько, "
            "поэтому сначала выбирается блюдо, а граммы идут вторыми. "
            "Деление на блюда сделано проектом и в исследованиях не проверялась.",
            "<i>Источник: PMID 30060528</i>",
        ]
    )
    assert _rows(screen) == [["⬅️ Как это работает"]]
    assert _buttons(screen)[0].callback_data == "hw:open"


def test_the_later_slices_topics_have_not_come_early():
    # M15b to M15e say what M and O ship; said now, they would be untrue.
    assert not {"capture", "voice", "time", "dishes"} & set(explain.TOPICS)


@pytest.mark.parametrize("key", list(explain.TOPICS))
def test_every_topic_says_what_how_why_and_source(key):
    topic = explain.TOPICS[key]
    screen = _tap(
        next(
            b for page in _pages() for b in _buttons(page) if b.text == topic.label
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
