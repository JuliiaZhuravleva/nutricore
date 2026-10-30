"""One next step (stage 5, slice F): it adds, it can be swapped, it never counts."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models.meal import Meal
from app.models.user import User
from app.services import explain_screen
from app.services import next_step as ns
from app.services import stats_screen as st

ZONE = ZoneInfo("Asia/Tbilisi")
# Fri 30 Oct 2026, 15:00 in Tbilisi (UTC+4).
NOW = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
DAY = date(2026, 10, 30)


ALL = ns.Focus(
    "all",
    "",
    "",
    frozenset(
        {"legumes", "anchor", "grain", "veg", "fruit", "calcium", "omega", "nuts"}
    ),
)


@pytest.fixture(autouse=True)
def _every_direction(monkeypatch):
    """How a step behaves inside a direction: here, one direction with every food."""
    monkeypatch.setattr(ns, "focus", lambda settings: ALL)


def _no_days():
    raise AssertionError("the step was chosen again")


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(User(id=1, telegram_id=42, settings={st.DAY_CARD_SEEN: True}))
        session.commit()
        yield session
    engine.dispose()


def _meal(db, groups=(), day=30):
    # ``day`` may run past the month's end: 32 is the 1st of November.
    first = datetime(2026, 10, 1, 9, tzinfo=ZONE)
    when = (first + timedelta(days=day - 1)).astimezone(UTC)
    tags = {"food_items": [], "food_groups": list(groups)}
    db.add(
        Meal(
            user_id=1,
            description="суп",
            meal_time=when,
            calories=400,
            proteins=10,
            fats=5,
            carbohydrates=50,
            nutrients={},
            ai_analysis=tags,
        )
    )
    db.commit()


def _card(db, now=NOW, tab=st.TODAY):
    return st.open_view(db, 42, tab, now)


def _rows(screen):
    return [[b.text for b in row] for row in screen.markup.inline_keyboard]


def _press(db, screen, label, now=NOW):
    for row in screen.markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return st.handle_callback(db, 42, button.callback_data, now)
    raise AssertionError(f"no {label} in {_rows(screen)}")


def _user(db):
    db.expire_all()
    return db.query(User).one()


def _state(db):
    return _user(db).settings[ns.STATE]


def test_the_card_offers_one_step_in_the_mocks_words_with_three_buttons(db):
    _meal(db, ["legumes", "anchor", "grain", "veg", "fruit", "calcium", "omega"])
    user = _user(db)
    user.settings = {**user.settings, ns.NO_SUGGEST: ["грецкие"]}
    db.commit()
    card = _card(db)
    assert (
        "👣 <b>Один шаг, ≈2 мин:</b> горсть орехов к перекусу. Можно прямо из пачки."
        in card.text
    )
    assert _rows(card)[0] == ["✅ Готово", "🔄 Другой шаг", "😴 Не сейчас"]
    assert _rows(card)[-2:] == [
        ["🔢 Цифры", "🔋 Силы: обычно"],
        ["✓ Сегодня", "Вчера", "7 дней"],
    ]
    assert card.markup.inline_keyboard[0][0].api_kwargs == {"style": "success"}


def test_the_step_comes_from_the_weeks_least_present_group(db):
    # Real records: every group on two days, nuts on none of them.
    others = ["legumes", "anchor", "grain", "veg", "fruit", "calcium", "omega"]
    for day in (29, 30):
        _meal(db, others, day=day)
    _card(db)
    assert "nuts" in ns.view(_user(db).settings).step.groups


def test_a_group_seen_every_day_is_not_what_the_step_adds():
    days = {
        key: 7
        for key in ("legumes", "anchor", "calcium", "omega", "nuts", "grain", "veg")
    }
    state = ns.current({}, DAY, lambda: days)
    assert ns.view(state).step.groups == ("fruit",)


def test_the_rank_is_benefit_times_chance_over_effort():
    days = {
        key: 7
        for key in ("anchor", "calcium", "omega", "nuts", "grain", "veg", "fruit")
    }
    state = ns.current({}, DAY, lambda: days)
    assert ns.view(state).step.id == "hummus"  # legumes, the highest rank of them


def test_the_step_stays_for_the_day_and_the_next_day_too(db):
    _meal(db)
    first = _state_after_open(db)
    _meal(
        db, ["legumes", "anchor", "calcium", "omega", "nuts", "grain", "veg", "fruit"]
    )
    assert _state_after_open(db)["step"] == first["step"]
    tomorrow = NOW + timedelta(days=1)
    _meal(db, day=31)
    _card(db, tomorrow)
    assert _state(db)["step"] == first["step"]
    assert _state(db)["day"] == "2026-10-31"


def _state_after_open(db):
    _card(db)
    return _state(db)


def test_done_undoes_with_one_button_and_offers_nothing_new_that_day(db):
    _meal(db)
    card = _card(db)
    title = ns.view(_user(db).settings).step.title
    done = _press(db, card, "✅ Готово")
    assert done.toast == "✓ Отмечено"
    assert f"👣 {title[:1].upper()}{title[1:]} — ✓ готово." in done.text
    assert _rows(done)[0] == ["↩️ Отменить", st.STEP_OTHER_BUTTON]
    assert "✓ готово" in _card(db).text
    back = _press(db, done, "↩️ Отменить")
    assert "Один шаг" in back.text and "готово" not in back.text


def test_not_now_is_neutral_and_comes_back_with_a_tap(db):
    _meal(db)
    later = _press(db, _card(db), "😴 Не сейчас")
    assert "😴 Шаг отложен до завтра." in later.text
    assert _rows(later)[0] == ["👣 Вернуть шаг", st.STEP_OTHER_BUTTON]
    back = _press(db, later, "👣 Вернуть шаг")
    assert "Один шаг" in back.text
    assert _state(db)["row"] == 0


def _put_off_on(db, day_shift):
    now = NOW + timedelta(days=day_shift)
    _meal(db, day=30 + day_shift)
    return _press(db, _card(db, now), "😴 Не сейчас", now)


def test_two_not_nows_in_a_row_make_the_next_step_smaller_without_a_word(db):
    for shift in (0, 1):
        _put_off_on(db, shift)
    now = NOW + timedelta(days=2)
    _meal(db, day=32)
    card = _card(db, now)
    assert _state(db)["day"] == "2026-11-01" and _state(db)["row"] == 2
    step = ns.view(_user(db).settings).step
    assert step.minutes <= 2 and step.title in card.text
    for word in ("отлож", "меньше", "проще", "два раза", "пропу"):
        assert word not in card.text.casefold().replace("😴", "")


def test_two_not_nows_shrink_a_ten_minute_size_to_two_even_where_only_big_ones_fit():
    # All quick ones refused: unshrunk, ten minutes are on offer; shrunk, they are not.
    refused = [s.foods[0] for s in ns.STEPS if s.minutes <= 2 and not s.cue]
    fresh = ns.current({ns.NO_SUGGEST: refused}, DAY, lambda: {})
    assert ns.view(fresh).step is not None and ns.view(fresh).step.minutes > 2
    shrunk = ns.current(
        {
            ns.NO_SUGGEST: refused,
            ns.STATE: {"day": "2026-10-29", "step": None, "row": 2},
        },
        DAY,
        lambda: {},
    )
    assert ns.view(shrunk).step is None


def test_done_clears_the_row_and_its_undo_puts_it_back():
    settings = ns.current({ns.STATE: {"day": "2026-10-29", "row": 1}}, DAY, lambda: {})
    shown = settings[ns.STATE]["step"]
    done, _ = ns.tap(settings, ns.TAKE, shown, DAY, _no_days)
    assert done[ns.STATE]["row"] == 0
    undone, _ = ns.tap(done, ns.UNDO, shown, DAY, _no_days)
    assert undone[ns.STATE]["row"] == 1


def test_a_step_not_done_is_nowhere_the_week_or_yesterday(db):
    _meal(db, ["veg"], day=29)
    _meal(db, ["veg"], day=30)
    _press(db, _card(db), "😴 Не сейчас")
    week = _card(db, tab=st.WEEK)
    yesterday = _card(db, tab=st.YESTERDAY)
    tomorrow = NOW + timedelta(days=1)
    _meal(db, day=31)
    next_day = _card(db, tomorrow)
    for screen in (week, yesterday, next_day):
        assert "😴" not in screen.text and "отложен" not in screen.text
    for screen in (week, yesterday):
        assert "👣" not in screen.text and "Силы" not in str(_rows(screen))


def test_an_empty_day_has_no_step_and_no_energy_button(db):
    card = _card(db)
    assert "👣" not in card.text
    assert not any("Силы" in label for row in _rows(card) for label in row)
    assert ns.STATE not in _user(db).settings


def test_swap_keeps_the_size_and_says_so(db):
    _meal(db)
    card = _card(db)
    before = ns.view(_user(db).settings).step
    swapped = _press(db, card, "🔄 Другой шаг")
    after = ns.view(_user(db).settings).step
    assert after.id != before.id and after.minutes == before.minutes
    assert swapped.toast == "Другой шаг того же размера"
    assert after.title in swapped.text


def test_swap_goes_through_every_step_of_the_mode_and_then_again():
    settings = ns.current({ns.ENERGY: ns.SIMPLE}, DAY, lambda: {})
    quick = {step.id for step in ns.STEPS if step.minutes <= 1 and not step.cue}
    seen = set()
    for _ in range(len(quick) * 2 + 1):
        shown = settings[ns.STATE]["step"]
        seen.add(shown)
        settings, toast = ns.tap(settings, ns.SWAP, shown, DAY, lambda: {})
        assert toast != "Другого шага сейчас нет"
        assert settings[ns.STATE]["step"] != shown
    assert seen == quick


def test_with_one_step_left_swap_says_there_is_no_other():
    refused = ["хумус", "соя", "орех", "хлеб"]
    settings = ns.current(
        {ns.NO_SUGGEST: refused, ns.ENERGY: ns.SIMPLE}, DAY, lambda: {}
    )
    assert settings[ns.STATE]["step"] == "fruit"
    again, toast = ns.tap(settings, ns.SWAP, "fruit", DAY, lambda: {})
    assert toast == "Другого шага сейчас нет" and again[ns.STATE]["step"] == "fruit"


def test_a_refused_food_also_drops_the_step_it_would_go_with():
    assert ns._refused(ns._BY_ID["hummus"], {ns.NO_SUGGEST: ["хлеб"]})


@pytest.mark.parametrize(
    "typed, gone",
    [
        ("орехи", {"walnut", "nuts"}),
        ("Грецкие орехи", {"walnut"}),
        ("арахис", {"peanut"}),
        ("нут", {"hummus", "beans"}),
        ("лён", {"flax"}),
        ("  ТОФУ ", {"tofu"}),
    ],
)
def test_the_no_suggest_list_is_honoured_endings_and_case_aside(typed, gone):
    settings = {ns.NO_SUGGEST: [typed]}
    for step in ns.STEPS:
        assert ns._refused(step, settings) == (step.id in gone), step.id


def test_a_word_in_the_middle_of_another_does_not_refuse():
    assert not ns._refused(ns._BY_ID["bread"], {ns.NO_SUGGEST: ["ель"]})


def test_nothing_left_means_no_step_and_the_card_still_works(db):
    _meal(db)
    user = _user(db)
    refused = [word for s in ns.STEPS for word in s.foods]
    user.settings = {**user.settings, ns.NO_SUGGEST: refused}
    db.commit()
    card = _card(db)
    assert "👣" not in card.text
    assert _rows(card)[0] == [st.STEP_FIRST_BUTTON]


def test_a_food_refused_later_drops_an_open_step_but_not_one_done():
    settings = ns.current({}, DAY, lambda: {})
    shown = ns.view(settings).step
    refused = {**settings, ns.NO_SUGGEST: [shown.foods[0]]}
    assert ns.view(ns.current(refused, DAY, lambda: {})).step.id != shown.id
    done, _ = ns.tap(settings, ns.TAKE, shown.id, DAY, _no_days)
    refused = {**done, ns.NO_SUGGEST: [shown.foods[0]]}
    assert ns.view(ns.current(refused, DAY, _no_days)).step.id == shown.id


def test_energy_goes_round_by_taps_and_the_toast_says_what_changed(db):
    _meal(db)
    card = _card(db)
    assert ns.view(_user(db).settings).mode == ns.NORMAL
    low = _press(db, card, "🔋 Силы: обычно")
    assert low.toast == "🪫 Мало сил: шаги на минуту-две, из готового"
    assert "🪫 Режим: мало сил" in low.text
    assert ns.view(_user(db).settings).step.minutes <= 2
    assert low.picture["mode"] == "low"
    simple = _press(db, low, "🪫 Силы: мало")
    assert simple.toast.startswith("🛋 Минимум сил")
    assert "🛋 Режим: минимум сил" in simple.text
    assert "👣 <b>Если захочется:</b>" in simple.text
    assert ns.view(_user(db).settings).step.minutes <= 1
    assert simple.picture["mode"] == "simple"
    normal = _press(db, simple, "🛋 Силы: минимум")
    assert normal.toast == "🔋 Обычно: шаги до 10 минут"
    assert "Режим" not in normal.text and "mode" not in normal.picture


def test_the_bot_never_changes_the_mode_by_itself(db):
    for day in (30, 31):
        _meal(db, day=day)
    _press(db, _card(db), "🔋 Силы: обычно")
    _card(db, NOW + timedelta(days=1))
    assert ns.view(_user(db).settings).mode == ns.LOW


def test_a_double_tap_on_energy_moves_it_once(db):
    _meal(db)
    data = next(
        b.callback_data
        for row in _card(db).markup.inline_keyboard
        for b in row
        if "Силы" in b.text
    )
    st.handle_callback(db, 42, data, NOW)
    again = st.handle_callback(db, 42, data, NOW)
    assert ns.view(_user(db).settings).mode == ns.LOW
    assert again.toast is None


def test_low_reaches_for_reliable_food_first_and_falls_back_without_a_match():
    settings = ns.current({ns.ENERGY: ns.LOW, ns.RELIABLE: ["тахини"]}, DAY, lambda: {})
    assert ns.view(settings).step.id == "tahini"
    settings = ns.current({ns.ENERGY: ns.LOW, ns.RELIABLE: ["борщ"]}, DAY, lambda: {})
    assert ns.view(settings).step.minutes <= 2


def test_a_double_tap_and_an_old_button_change_nothing(db):
    _meal(db)
    card = _card(db)
    button = card.markup.inline_keyboard[0]
    swap = button[1].callback_data
    st.handle_callback(db, 42, swap, NOW)
    now_shown = _state(db)["step"]
    again = st.handle_callback(db, 42, swap, NOW)
    assert _state(db)["step"] == now_shown
    assert again.toast == "Шаг уже другой, вот нынешний"
    done_old = st.handle_callback(db, 42, button[0].callback_data, NOW)
    assert _state(db)["state"] == ns.OPEN and "Один шаг" in done_old.text


def test_a_button_from_yesterdays_card_shows_today_with_a_notice(db):
    _meal(db)
    old = _card(db)
    tomorrow = NOW + timedelta(days=1)
    _meal(db, day=31)
    screen = _press(db, old, "✅ Готово", tomorrow)
    assert "Это была карточка прошлого дня" in screen.text
    assert _state(db)["state"] == ns.OPEN


@pytest.mark.parametrize(
    "data",
    [
        "st:s",
        "st:s:d",
        "st:s:z:20261030:nuts:",
        "st:e",
        "st:e:huge",
        "st:s:d:bad:nuts:",
    ],
)
def test_broken_data_is_todays_card_and_changes_nothing(db, data):
    _meal(db)
    _card(db)
    before = dict(_state(db))
    screen = st.handle_callback(db, 42, data, NOW)
    assert "📊 Сегодня" in screen.text
    assert _state(db) == before


@pytest.mark.parametrize(
    "bad", ["мусор", 7, [], {"day": "x"}, {"step": "gone", "day": "2026-10-30"}]
)
def test_a_damaged_saved_step_is_chosen_again_not_a_crash(db, bad):
    _meal(db)
    user = _user(db)
    user.settings = {**user.settings, ns.STATE: bad}
    db.commit()
    assert "👣" in _card(db).text


def test_numbers_stay_open_through_a_step_tap(db):
    _meal(db)
    opened = st.open_view(db, 42, st.TODAY, NOW, True)
    done = _press(db, opened, "✅ Готово")
    assert "🔢 За день" in done.text


def test_the_setting_with_open_numbers_leaves_energy_alone_on_its_row(db):
    from app.services.settings_screen import SHOW_NUMBERS

    _meal(db)
    user = _user(db)
    user.settings = {**user.settings, SHOW_NUMBERS: True}
    db.commit()
    assert _rows(_card(db))[-2:] == [
        ["🔋 Силы: обычно"],
        ["✓ Сегодня", "Вчера", "7 дней"],
    ]


def test_minimum_mode_keeps_ticks_off_the_keyboard_but_in_the_text(db):
    from app.services.routines import ROUTINES

    _meal(db)
    user = _user(db)
    user.settings = {
        **user.settings,
        ROUTINES: [{"id": "abc", "name": "Прогулка", "emoji": "🚶"}],
    }
    db.commit()
    card = _card(db)
    assert any("Прогулка" in label for row in _rows(card) for label in row)
    low = _press(db, card, "🔋 Силы: обычно")
    simple = _press(db, low, "🪫 Силы: мало")
    assert not any("Прогулка" in label for row in _rows(simple) for label in row)


def test_the_first_card_says_the_step_is_optional(db):
    _meal(db)
    user = _user(db)
    user.settings = {}
    db.commit()
    assert "Шаг внизу — по желанию." in _card(db).text


def test_the_picture_carries_the_same_step_in_every_state(db):
    _meal(db)
    card = _card(db)
    step = ns.view(_user(db).settings).step
    assert card.picture["step"]["state"] == ns.OPEN
    assert card.picture["step"]["title"].casefold() == step.title.casefold()
    assert card.picture["step"]["min"] == f"≈{step.minutes} мин"
    assert card.picture["step"]["icon"] in {
        "legumes", "anchor", "veg", "fruit", "grain", "nuts", "calcium", "omega",
    }  # fmt: skip
    done = _press(db, card, "✅ Готово")
    assert done.picture["step"]["state"] == ns.DONE
    later = _press(db, _press(db, done, "↩️ Отменить"), "😴 Не сейчас")
    assert later.picture["step"]["state"] == ns.LATER


FORBIDDEN = re.compile(
    r"вместо|меньше|убрать|убери|замени|откажи|без |не ешь|похуд|калори|вес\b|лишн",
    re.IGNORECASE,
)


@pytest.mark.parametrize("step", ns.STEPS, ids=lambda s: s.id)
def test_every_step_only_adds_and_stays_within_its_size(step):
    assert not FORBIDDEN.search(f"{step.title} {step.note}")
    assert step.minutes in (1, 2, 5, 10)
    assert step.title[0].islower()
    assert len(step.title) <= 55
    assert all(group in st.day_card.food_groups.GROUPS for group in step.groups)
    assert step.foods or step.cue


def test_every_mode_has_something_to_offer_and_every_group_has_a_step():
    for mode in ns.MODES:
        settings = ns.current({ns.ENERGY: mode}, DAY, lambda: {})
        assert ns.view(settings).step is not None
    covered = {group for step in ns.STEPS for group in step.groups}
    assert covered == set(st.day_card.food_groups.GROUPS)


def test_the_bot_explains_the_step():
    topic = explain_screen.TOPICS["step"]
    assert "Не сейчас" in topic.how and "Силы" in topic.how
    assert "Carrero" in topic.source


def test_a_step_brought_back_after_the_mode_went_lower_is_chosen_again():
    settings = ns.current(
        {ns.NO_SUGGEST: ["хумус", "соя", "орех", "хлеб", "банан"]}, DAY, lambda: {}
    )
    shown = ns.view(settings).step
    assert shown.minutes == 2
    later, _ = ns.tap(settings, ns.PUT_OFF, shown.id, DAY, _no_days)
    low, _ = ns.switch_energy(later, ns.SIMPLE, DAY, lambda: {})
    assert ns.view(low).step.id == shown.id  # put off, it stays
    back, _ = ns.tap(low, ns.BRING_BACK, shown.id, DAY, lambda: {})
    assert (
        ns.view(ns.current(back, DAY, lambda: {})).step is None
    )  # nothing of 1 min is left


def test_a_step_undone_after_the_mode_went_lower_fits_the_mode():
    settings = ns.current({ns.NO_SUGGEST: ["хумус"]}, DAY, lambda: {"legumes": 0})
    settings = {**settings, ns.STATE: {**settings[ns.STATE], "step": "tofu"}}
    done, _ = ns.tap(settings, ns.TAKE, "tofu", DAY, _no_days)
    low, _ = ns.switch_energy(done, ns.LOW, DAY, lambda: {})
    undone, _ = ns.tap(low, ns.UNDO, "tofu", DAY, lambda: {})
    assert ns.view(ns.current(undone, DAY, lambda: {})).step.minutes <= 2


@pytest.mark.parametrize(
    "damaged",
    [
        {ns.ENERGY: []},
        {ns.ENERGY: {"a": 1}},
        {ns.STATE: {"day": "2026-10-30", "step": []}},
        {ns.STATE: {"day": "2026-10-30", "step": {}, "state": []}},
        {ns.STATE: {"day": "2026-10-29", "step": "nuts", "last": []}},
        {ns.STATE: {"day": "2026-10-29", "step": [], "row": "два"}},
        {ns.RELIABLE: 5, ns.ENERGY: ns.LOW, ns.NO_SUGGEST: "орехи"},
    ],
)
def test_damaged_values_never_cost_the_card_and_it_mends_itself(damaged):
    settings = ns.current(damaged, DAY, lambda: {})
    assert ns.view(settings).step is not None
    shown = ns.view(settings).step.id
    settings, toast = ns.tap(settings, ns.SWAP, shown, DAY, lambda: {})
    assert toast


@pytest.mark.parametrize("seen", [7, [[]], [1], "nuts", None])
def test_a_damaged_seen_list_does_not_break_swap(seen):
    settings = ns.current({}, DAY, lambda: {})
    settings = {**settings, ns.STATE: {**settings[ns.STATE], "seen": seen}}
    shown = settings[ns.STATE]["step"]
    swapped, toast = ns.tap(settings, ns.SWAP, shown, DAY, lambda: {})
    assert toast and swapped[ns.STATE]["step"] != shown


@pytest.mark.parametrize("state", [{}, {"state": "x"}, {"state": []}])
def test_an_unknown_state_reads_as_open_and_its_buttons_work(state):
    settings = {ns.STATE: {"day": "2026-10-30", "step": "hummus", **state}}
    assert ns.view(settings).state == ns.OPEN
    done, toast = ns.tap(settings, ns.TAKE, "hummus", DAY, _no_days)
    assert toast == "✓ Отмечено" and ns.view(done).state == ns.DONE


def test_the_only_step_left_comes_every_day_not_every_other_day():
    settings = {ns.NO_SUGGEST: ["хумус", "соя", "орех", "хлеб"], ns.ENERGY: ns.SIMPLE}
    for shift in range(3):
        settings = ns.current(settings, DAY + timedelta(days=shift), lambda: {})
        assert settings[ns.STATE]["step"] == "fruit"


def test_energy_on_an_old_card_with_no_meals_today_chooses_nothing(db):
    _meal(db)
    data = next(
        b.callback_data
        for row in _card(db).markup.inline_keyboard
        for b in row
        if "Силы" in b.text
    )
    tomorrow = NOW + timedelta(days=1)
    screen = st.handle_callback(db, 42, data, tomorrow)
    assert "👣" not in screen.text
    assert _state(db)["day"] == "2026-10-30"
    assert ns.view(_user(db).settings).mode == ns.LOW
    _meal(db, day=31)
    assert "👣" in _card(db, tomorrow).text
