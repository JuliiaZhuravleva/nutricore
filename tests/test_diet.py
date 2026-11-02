"""«Обычное питание»: an answer that changes only the hints, and only when given.

Absent means what the bot did before the answer existed: the plant steps, the reference
on vitamins, every word. Screens and the card run through the real handlers on SQLite.
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import CallbackQuery, Update
from telegram import User as TgUser

from app.core.config import settings
from app.db.base import Base
from app.models.meal import Meal
from app.models.user import User
from app.services import diet, explain_screen, food_groups
from app.services import next_step as ns
from app.services import settings_screen
from app.services import stats_screen as st
from app.services import step_count
from app.services import telegram as tg
from app.services import week_card
from app.services.openai_service import _FOOD_GROUPS_RULE, OpenAIService

ZONE = ZoneInfo("Asia/Tbilisi")
# Fri 30 Oct 2026, 15:00 in Tbilisi (UTC+4).
NOW = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
DAY = date(2026, 10, 30)
EGGY = {"egg", "cottage", "yogurt"}
ANIMAL = EGGY | {"tinned_fish"}
PLANT_STEP_IDS_BEFORE_THE_ANSWER = [
    "hummus", "soy", "fruit", "walnut", "bread", "nuts", "seeds", "tahini", "peanut",
    "berries", "greens", "flax", "beans", "edamame", "oats", "tofu", "frozen",
    "wake_coffee", "wake_home", "wake_first",
]  # fmt: skip
PROTEIN = {ns.FOCUS: {"id": ns.PROTEIN}}


def _with(value, **more):
    answer = {diet.KEY: value} if value else {}
    return {**PROTEIN, **answer, **more}


def _no_days():
    return {}


# --- the answer ------------------------------------------------------------------


def test_the_three_answers_are_the_designs_words():
    assert list(diet.CHOICES.items()) == [
        ("all", "🍖 Всё"),
        ("no_meat", "🥚 Без мяса"),
        ("plants", "🌱 Растения"),
    ]


@pytest.mark.parametrize("value", ["all", "no_meat", "plants"])
def test_an_answer_reads_back(value):
    assert diet.chosen(diet.with_choice({"other": 1}, value)) == value


@pytest.mark.parametrize("damaged", [None, "", "veg", 7, ["all"], {"id": "all"}])
def test_a_damaged_value_reads_as_no_answer(damaged):
    assert diet.chosen({diet.KEY: damaged}) is None


def test_no_answer_is_no_answer():
    assert diet.chosen({}) is None


def test_saving_keeps_the_other_settings_and_a_new_dict():
    before = {"other": 1}
    after = diet.with_choice(before, diet.NO_MEAT)
    assert after == {"other": 1, diet.KEY: diet.NO_MEAT}
    assert before == {"other": 1}


def test_an_unknown_answer_is_not_saved():
    before = {diet.KEY: diet.ALL}
    assert diet.with_choice(before, "carnivore") == before


# --- 👣 one step -----------------------------------------------------------------


def _offered(settings, **kw):
    """Every step the direction can show, walking «🔄 Другой шаг» to the end."""
    seen, skip = [], set()
    while True:
        step = ns._select(settings, kw.get("days", {}), 0, skip)
        if step is None:
            return seen
        seen.append(step.id)
        skip.add(step.id)


def test_no_answer_keeps_todays_steps_exactly():
    allowed = [s.id for s in ns.STEPS if ns._allowed(s, {})]
    assert allowed == PLANT_STEP_IDS_BEFORE_THE_ANSWER


@pytest.mark.parametrize("value", [None, diet.PLANTS, "veg"])
def test_animal_food_is_never_offered_without_being_asked_for(value):
    settings = {ns.FOCUS: {"id": ns.PROTEIN}, diet.KEY: value}
    offered = set(_offered(settings))
    assert "hummus" in offered  # the known positive: the walk does find steps
    assert not offered & ANIMAL


@pytest.mark.parametrize("value", [None, diet.PLANTS])
def test_plants_and_no_answer_choose_as_before(value):
    days = {"legumes": 3, "anchor": 3, "calcium": 0}
    step = ns._select(_with(value), days, 0, ())
    assert step.id == "soy"  # the least-present group decides, as it always did


def test_no_meat_gets_egg_cottage_cheese_and_yogurt_but_no_fish():
    offered = set(_offered(_with(diet.NO_MEAT)))
    assert EGGY <= offered
    assert "tinned_fish" not in offered


def test_all_gets_those_and_tinned_fish():
    assert ANIMAL <= set(_offered(_with(diet.ALL)))


@pytest.mark.parametrize("value", [diet.ALL, diet.NO_MEAT])
def test_the_chosen_diets_food_comes_first_and_plants_stay_behind_the_swap(value):
    # Legumes are rare in ordinary eating: the rule by group alone would say «хумус».
    days = {"legumes": 0, "anchor": 6, "calcium": 3}
    walked = _offered(_with(value), days=days)
    wanted = ANIMAL if value == diet.ALL else EGGY
    assert set(walked[: len(wanted)]) == wanted
    assert "soy" in walked and "hummus" in walked


def test_calcium_low_brings_dairy_for_those_who_eat_it():
    days = {"legumes": 7, "anchor": 7, "calcium": 0}
    step = ns._select(_with(diet.NO_MEAT), days, 0, ())
    assert step.id in {"cottage", "yogurt"}
    assert "calcium" in step.groups


def test_protein_steps_carry_the_groups_their_direction_counts():
    steps = {s.id: s for s in ns.STEPS}
    assert "anchor" in steps["egg"].groups
    assert {"anchor", "calcium"} <= set(steps["cottage"].groups)
    assert {"anchor", "calcium"} <= set(steps["yogurt"].groups)
    assert "anchor" in steps["tinned_fish"].groups


def test_tinned_fish_stays_out_of_the_nuts_direction():
    extras = {ns.FOCUS: {"id": ns.EXTRAS}, diet.KEY: diet.ALL}
    assert not set(_offered(extras)) & ANIMAL


def test_small_modes_still_reach_a_ready_animal_step():
    low = _with(diet.ALL, **{ns.ENERGY: ns.LOW})
    simple = _with(diet.ALL, **{ns.ENERGY: ns.SIMPLE})
    assert {"cottage", "yogurt", "tinned_fish"} <= set(_offered(low))
    assert "egg" not in _offered(low)  # five minutes is more than the mode allows
    assert set(_offered(simple)) & {"cottage", "yogurt"}


@pytest.mark.parametrize(
    "word, gone",
    [
        ("творог", {"cottage"}),
        ("яйца", {"egg"}),
        ("яйцо", {"egg"}),
        ("рыба", {"tinned_fish"}),
        ("йогурт", {"yogurt"}),
        ("молочное", {"cottage", "yogurt"}),
    ],
)
def test_not_suggesting_reaches_the_new_steps(word, gone):
    settings = _with(diet.ALL, **{ns.NO_SUGGEST: [word]})
    offered = set(_offered(settings))
    assert not offered & gone
    assert ANIMAL - gone <= offered  # the rest stay


def test_a_reliable_food_finds_its_step():
    settings = _with(diet.ALL, **{ns.ENERGY: ns.LOW, ns.RELIABLE: ["Творог с ягодами"]})
    assert ns._select(settings, {}, 0, ()).id == "cottage"


def test_a_direction_is_not_offered_on_steps_the_answer_shuts_out():
    # Every plant step of the direction refused: only animal ones would be left.
    refused = {ns.NO_SUGGEST: ["хумус", "соя", "арахис", "фасоль", "эдамаме", "тофу"]}
    assert ns.PROTEIN not in ns.options(refused, {}, False)
    assert ns.PROTEIN not in ns.options({**refused, diet.KEY: diet.PLANTS}, {}, False)
    # And for one who eats them the same refusals leave it open.
    assert ns.PROTEIN in ns.options({**refused, diet.KEY: diet.NO_MEAT}, {}, False)


@pytest.mark.parametrize("value", [None, diet.PLANTS])
def test_a_changed_answer_takes_an_open_animal_step_off_the_card(value):
    state = {"day": DAY.isoformat(), "step": "egg", "state": ns.OPEN, "seen": ["egg"]}
    settings = _with(value, **{ns.STATE: state})
    after = ns.current(settings, DAY, _no_days)
    assert after[ns.STATE]["step"] not in ANIMAL


def test_a_changed_answer_takes_it_off_tomorrow_too():
    yesterday = (DAY - timedelta(days=1)).isoformat()
    state = {"day": yesterday, "step": "egg", "state": ns.DONE, "seen": ["egg"]}
    after = ns.current(_with(diet.PLANTS, **{ns.STATE: state}), DAY, _no_days)
    assert after[ns.STATE]["step"] not in ANIMAL


def test_a_step_done_today_stays_done_whatever_the_answer_now():
    state = {"day": DAY.isoformat(), "step": "egg", "state": ns.DONE, "seen": ["egg"]}
    settings = _with(diet.PLANTS, **{ns.STATE: state})
    assert ns.current(settings, DAY, _no_days) == settings


def test_an_answer_that_still_allows_the_step_leaves_it_alone():
    state = {"day": DAY.isoformat(), "step": "egg", "state": ns.OPEN, "seen": ["egg"]}
    settings = _with(diet.NO_MEAT, **{ns.STATE: state})
    assert ns.current(settings, DAY, _no_days) == settings


def test_bringing_a_step_back_after_the_answer_changed_does_not_show_it():
    state = {"day": DAY.isoformat(), "step": "egg", "state": ns.LATER, "seen": ["egg"]}
    settings = _with(diet.PLANTS, **{ns.STATE: state})
    after, _toast = ns.tap(settings, ns.BRING_BACK, "egg", DAY, _no_days)
    shown = ns.current(after, DAY, _no_days)
    assert shown[ns.STATE]["step"] not in ANIMAL


# --- on the card, through the real screens ----------------------------------------


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    with factory() as db:
        db.add(User(id=1, telegram_id=42, settings={st.DAY_CARD_SEEN: True}))
        db.commit()
    yield factory
    engine.dispose()


def _set(Session, **values):
    with Session() as db:
        user = db.query(User).one()
        user.settings = {**(user.settings or {}), **values}
        db.commit()


def _stored(Session):
    with Session() as db:
        return db.query(User).one().settings


def _meal(db, items, groups, back=0):
    day = DAY - timedelta(days=back)
    when = datetime(day.year, day.month, day.day, 9, tzinfo=ZONE)
    db.add(
        Meal(
            user_id=1,
            description="обед",
            meal_time=when.astimezone(UTC),
            calories=400,
            proteins=10,
            fats=5,
            carbohydrates=50,
            nutrients={},
            ai_analysis={"food_items": items, "food_groups": list(groups)},
        )
    )
    db.commit()


def _card(Session):
    with Session() as db:
        return st.open_view(db, 42, st.TODAY, NOW)


def test_the_card_offers_the_chosen_diets_step(Session):
    _set(Session, **{ns.FOCUS: {"id": ns.PROTEIN}, diet.KEY: diet.ALL})
    with Session() as db:
        _meal(db, [{"name": "суп", "groups": []}], [])
    assert "творог" in _card(Session).text


def test_the_card_with_no_answer_says_what_it_says_today(Session):
    _set(Session, **{ns.FOCUS: {"id": ns.PROTEIN}})
    with Session() as db:
        _meal(db, [{"name": "суп", "groups": []}], [])
    assert "готовый хумус к хлебу" in _card(Session).text


def test_changing_the_answer_changes_the_card_at_once(Session):
    _set(Session, **{ns.FOCUS: {"id": ns.PROTEIN}, diet.KEY: diet.ALL})
    with Session() as db:
        _meal(db, [{"name": "суп", "groups": []}], [])
    assert "творог" in _card(Session).text
    with Session() as db:
        settings_screen.handle_callback(db, 42, "set:diet:plants", NOW)
    text = _card(Session).text
    assert "творог" not in text and "готовый хумус" in text


def test_the_choice_of_a_direction_uses_the_answer(Session):
    refused = {ns.NO_SUGGEST: ["хумус", "соя", "арахис", "фасоль", "эдамаме", "тофу"]}
    _set(Session, **refused)

    def offered():
        with Session() as db:
            user = db.query(User).one()
            screen = st._choice_screen(db, user, DAY, ZONE, False)
        return [b.text for row in screen.markup.inline_keyboard for b in row]

    assert "🥫 Добавить готовое с белком" not in offered()
    _set(Session, **{diet.KEY: diet.NO_MEAT})
    assert "🥫 Добавить готовое с белком" in offered()


# --- ⚙️ settings ------------------------------------------------------------------


def _query(data, telegram_id=42):
    query = SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=None
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=telegram_id)
    )
    return update, query


def _tap_settings(data):
    update, query = _query(data)
    asyncio.run(tg.settings_callback(update, SimpleNamespace()))
    (text,), kwargs = query.edit_message_text.await_args
    return text, kwargs["reply_markup"]


def _rows(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _within_limits(markup):
    rows = markup.inline_keyboard
    assert len(rows) <= 5
    assert all(len(row) <= 3 for row in rows)


def test_the_hub_has_the_row_and_keeps_its_budget(Session):
    text, markup = _tap_settings("set:open")
    assert _rows(markup) == [
        ["🔢 Цифры сразу: выкл", "📋 Черновик: выкл"],
        ["🗓 Итог недели: вкл", "💸 Стоимость: выкл"],
        ["🧺 Надёжная еда", "✋ Не предлагать"],
        ["🕐 Пояс", "💊 Рутины", "💬 Фидбэк"],
        ["🍽 Обычная еда", "ℹ️ Как это работает"],
    ]
    _within_limits(markup)
    assert "🍽 Обычная еда: не выбрано" in text


def test_no_label_on_the_hub_is_long_enough_for_telegram_to_cut():
    # «🍽 Обычная еда» third in a row came out as «Об…ая еда» on a phone.
    hub = settings_screen.screen(True, "Asia/Tbilisi", NOW, draft=True)
    three = [row for row in hub.markup.inline_keyboard if len(row) == 3]
    assert three and all(len(b.text) <= 12 for row in three for b in row), three


def test_the_hub_says_each_thing_on_a_line_of_its_own_and_explains_nothing():
    hub = settings_screen.screen(True, "Asia/Tbilisi", NOW, draft=True)
    lines = hub.text.split("\n")
    assert len(lines) <= 9 and all(len(line) <= 40 for line in lines), lines
    assert "🔢" not in hub.text and "📋" not in hub.text and "💸" not in hub.text


def test_every_two_button_label_on_the_hub_fits_the_row():
    hub = settings_screen.screen(True, "Asia/Tbilisi", NOW, draft=True)
    for row in hub.markup.inline_keyboard:
        if len(row) == 2:
            assert all(len(b.text) <= 20 for b in row), row


def test_the_picker_asks_and_goes_back_to_settings(Session):
    text, markup = _tap_settings("set:diet")
    assert text.startswith("<b>🍽 Обычная еда</b>")
    assert (
        "Что обычно ешь? От этого зависят только подсказки, записывать можно любую еду."
        in text
    )
    assert _rows(markup) == [
        ["🍖 Всё", "🥚 Без мяса", "🌱 Растения"],
        ["⬅️ Настройки"],
    ]
    _within_limits(markup)
    assert markup.inline_keyboard[-1][0].callback_data == "set:open"


@pytest.mark.parametrize(
    "value, label",
    [("all", "🍖 Всё"), ("no_meat", "🥚 Без мяса"), ("plants", "🌱 Растения")],
)
def test_a_pick_saves_and_says_so_on_the_hub(Session, value, label):
    _set(Session, **{settings_screen.SHOW_COSTS: True})
    text, markup = _tap_settings(f"set:diet:{value}")
    assert text.startswith(f"<i>✓ Обычная еда: {label}</i>")
    assert f"🍽 Обычная еда: {label}" in text
    assert "<b>⚙️ Настройки</b>" in text
    assert _stored(Session)[diet.KEY] == value
    assert _stored(Session)[settings_screen.SHOW_COSTS] is True  # the rest is kept
    assert "🍽 Обычная еда" in [b for row in _rows(markup) for b in row]


def test_the_picker_marks_the_answer_in_use_within_the_label_budget(Session):
    _set(Session, **{diet.KEY: diet.NO_MEAT})
    _, markup = _tap_settings("set:diet")
    row = _rows(markup)[0]
    assert row == ["🍖 Всё", "✓ 🥚 Без мяса", "🌱 Растения"]
    assert all(len(label) <= 13 for label in row)


def test_a_stale_pick_lands_on_the_hub_and_saves_nothing(Session, caplog):
    text, _ = _tap_settings("set:diet:carnivore")
    assert "<b>⚙️ Настройки</b>" in text and "✓" not in text.split("\n")[0]
    assert diet.KEY not in (_stored(Session) or {})
    assert "unknown callback data" in caplog.text


@pytest.mark.parametrize("data", ["set:diet", "set:diet:all"])
def test_the_bot_routes_the_picker_to_settings(data):
    application = tg.create_bot_application()
    query = CallbackQuery("1", TgUser(42, "u", False), "chat", data=data)
    update = Update(1, callback_query=query)
    matched = [h for h in application.handlers[0] if h.check_update(update)]
    assert matched and matched[0].callback is tg.settings_callback


def test_slice_three_can_ask_with_its_own_callback_and_save_through_one_door(Session):
    row = settings_screen.diet_row("first:diet:", None)
    assert [b.text for b in row] == ["🍖 Всё", "🥚 Без мяса", "🌱 Растения"]
    assert [b.callback_data for b in row] == [
        "first:diet:all",
        "first:diet:no_meat",
        "first:diet:plants",
    ]
    with Session() as db:
        assert settings_screen.save_diet(db, 42, diet.ALL) is True
        assert settings_screen.save_diet(db, 42, "carnivore") is False
    assert _stored(Session)[diet.KEY] == diet.ALL


# --- ℹ️ the reference --------------------------------------------------------------


def _explain(Session, data):
    with Session() as db:
        return explain_screen.handle_callback(db, 42, data)


def _all_list_labels(Session):
    screen = _explain(Session, explain_screen.OPEN)
    labels = [b.text for row in screen.markup.inline_keyboard for b in row]
    arrow = [
        b.callback_data
        for row in screen.markup.inline_keyboard
        for b in row
        if b.text == "▶️"
    ]
    if arrow:
        second = _explain(Session, arrow[0])
        labels += [b.text for row in second.markup.inline_keyboard for b in row]
    return labels


@pytest.mark.parametrize("value", [None, diet.NO_MEAT, diet.PLANTS])
def test_the_reference_stays_for_anyone_but_all(Session, value):
    if value:
        _set(Session, **{diet.KEY: value})
    assert explain_screen.NUTRIENTS_LIST_LABEL in _all_list_labels(Session)
    routine = _explain(Session, explain_screen._TOPIC + "routines")
    assert explain_screen.NUTRIENTS_TITLE in [
        b.text for row in routine.markup.inline_keyboard for b in row
    ]
    assert "в справке" in routine.text
    assert "веганах" in _explain(Session, explain_screen.NUTRIENTS_OPEN).text


def test_all_does_not_see_the_reference_on_the_list(Session):
    _set(Session, **{diet.KEY: diet.ALL})
    labels = _all_list_labels(Session)
    assert explain_screen.NUTRIENTS_LIST_LABEL not in labels
    assert "💊 Рутины" in labels  # the known positive: the list is there


def test_all_sees_routines_with_no_word_about_the_reference(Session):
    _set(Session, **{diet.KEY: diet.ALL})
    routine = _explain(Session, explain_screen._TOPIC + "routines")
    assert [b.text for row in routine.markup.inline_keyboard for b in row] == [
        "⬅️ Как это работает"
    ]
    assert "справк" not in routine.text
    assert "B12" in routine.text and "не советует" in routine.text


@pytest.mark.parametrize(
    "data",
    [explain_screen.NUTRIENTS_OPEN, explain_screen._NUTRIENT + "b12"],
)
def test_an_old_reference_button_lands_on_the_list_for_all(Session, data):
    _set(Session, **{diet.KEY: diet.ALL})
    screen = _explain(Session, data)
    assert screen.text == "<b>ℹ️ Как это работает</b>"
    assert screen.toast == "Этой справки здесь нет, вот список"


def test_the_list_for_all_keeps_the_layout_limits(Session):
    _set(Session, **{diet.KEY: diet.ALL})
    screen = _explain(Session, explain_screen.OPEN)
    _within_limits(screen.markup)
    assert screen.markup.inline_keyboard[-1][0].text == "⬅️ Настройки"
    # A page past the end, from a list that had one more entry, still opens a page.
    page = _explain(Session, explain_screen._PAGE + "1")
    assert page.text.startswith("<b>ℹ️ Как это работает</b>")


def test_the_bot_shows_the_list_without_the_reference_for_all(Session):
    _set(Session, **{diet.KEY: diet.ALL})
    update, query = _query(explain_screen.NUTRIENTS_OPEN)
    asyncio.run(tg.explain_callback(update, SimpleNamespace()))
    (text,), _ = query.edit_message_text.await_args
    assert text == "<b>ℹ️ Как это работает</b>"


def test_the_groups_help_says_fatty_fish_counts():
    how = explain_screen.TOPICS["groups"].how
    assert "жирной рыбы" in how
    assert "только из растений" not in how
    assert "лён" in how and "грецкие орехи" in how


def test_the_step_help_says_when_animal_food_is_offered():
    how = explain_screen.TOPICS["step"].how
    assert "🍖 Всё" in how and "🥚 Без мяса" in how


# --- omega-3 in the tagging prompt ----------------------------------------------


def test_the_prompt_counts_fatty_fish_for_omega():
    rule = _FOOD_GROUPS_RULE.split('"omega"')[1]
    assert "fatty fish" in rule and "salmon" in rule
    assert "flax" in rule and "walnuts" in rule
    assert "plant omega-3 sources only" not in _FOOD_GROUPS_RULE


def test_the_real_call_sends_the_new_rule():
    service = OpenAIService()
    fake = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )
    service.client.chat.completions.create = AsyncMock(return_value=fake)
    asyncio.run(service.analyze_food_entry("лосось с рисом"))
    kwargs = service.client.chat.completions.create.call_args.kwargs
    system = next(m["content"] for m in kwargs["messages"] if m["role"] == "system")
    assert "fatty fish" in system


# --- fish counts as omega-3, and is still no plant ----------------------------------

SALMON = {"name": "лосось", "groups": ["anchor", "omega"]}
FLAX_OIL = {"name": "льняное масло", "groups": ["omega"]}
WALNUT = {"name": "грецкие орехи", "groups": ["nuts", "omega"]}
HEMP = {"name": "конопляные семечки", "groups": ["anchor", "nuts", "omega"]}


def _a_meal(items, groups=None):
    confirmed = (
        groups if groups is not None else food_groups.from_items(items)
    )  # as the bot stores it
    return Meal(
        meal_time=datetime(2026, 10, 30, 9, tzinfo=UTC),
        nutrients={},
        ai_analysis={"food_items": items, "food_groups": confirmed},
    )


def _facts(items):
    return week_card.facts([(DAY, [_a_meal(items)])], [], ZONE)


def test_salmon_shows_as_omega_on_the_week():
    assert dict(_facts([SALMON]).groups)["omega"] == 1


def test_salmon_is_not_a_plant():
    assert _facts([SALMON]).plants == 0


def test_a_new_fish_is_not_the_weeks_new_plant():
    earlier = [_a_meal([{"name": "гречка", "groups": ["grain"]}])]
    found = week_card.facts([(DAY, [_a_meal([SALMON])])], earlier, ZONE)
    assert found.new_food is None


@pytest.mark.parametrize("item", [FLAX_OIL, WALNUT, HEMP])
def test_a_plant_source_of_omega_is_still_a_plant(item):
    assert _facts([item]).plants == 1


def test_a_plate_with_fish_still_counts_its_plants():
    items = [SALMON, {"name": "брокколи", "groups": ["veg"]}, WALNUT]
    assert _facts(items).plants == 2


def test_step_groups_drop_only_an_omega_that_fish_alone_gave():
    assert "omega" not in week_card.step_groups(_a_meal([SALMON]))
    assert "anchor" in week_card.step_groups(_a_meal([SALMON]))
    assert "omega" in week_card.step_groups(_a_meal([SALMON, WALNUT]))
    assert "omega" in week_card.step_groups(_a_meal([FLAX_OIL]))
    # A group ticked by hand has no item to come from: it stays as the person set it.
    assert "omega" in week_card.step_groups(_a_meal([], ["omega"]))


def _count_extras(items):
    return step_count.count_days(
        [_a_meal(items)], {"id": ns.EXTRAS}, ZONE, DAY - timedelta(days=6), DAY
    )


def test_a_day_of_salmon_is_not_a_day_of_nuts_or_grains():
    assert _count_extras([SALMON]) == 0


@pytest.mark.parametrize("item", [FLAX_OIL, WALNUT])
def test_a_day_of_flax_oil_or_walnuts_counts_as_it_did(item):
    assert _count_extras([item]) == 1


def test_a_fish_eater_is_still_offered_nuts_on_the_choice_screen(Session):
    """Salmon on six days must not read as nuts or grains nearly every day."""
    with Session() as db:
        for back in range(6):
            _meal(db, [SALMON], ["anchor", "omega"], back=back)
        user = db.query(User).one()
        days = st._group_days(db, user, DAY, ZONE)
    assert days.get("omega", 0) == 0
    assert days["anchor"] == 6


def test_group_days_for_steps_still_see_a_plant_omega(Session):
    with Session() as db:
        _meal(db, [WALNUT], ["nuts", "omega"])
        days = st._group_days(db, db.query(User).one(), DAY, ZONE)
    assert days["omega"] == 1 and days["nuts"] == 1


# --- the ordinary dishes for the real check -----------------------------------------


def test_the_ordinary_dishes_file_fits_the_eval_script():
    path = (
        Path(__file__).parent / "fixtures" / "food_groups_eval_ordinary.json"
    )  # scripts/eval_food_groups.py --set <this>
    meals = json.loads(path.read_text(encoding="utf-8"))
    assert 8 <= len(meals) <= 12
    for meal in meals:
        assert set(meal) == {"text", "must", "maybe"}
        assert set(meal["must"]) | set(meal["maybe"]) <= set(food_groups.GROUPS)
    salmon = next(m for m in meals if "лосос" in m["text"])
    assert "omega" in salmon["must"]
