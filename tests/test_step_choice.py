"""The step the person chose, and the count of days (stage 5, slice K)."""

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
from app.services import step_count as sc

ZONE = ZoneInfo("Asia/Tbilisi")
# Fri 30 Oct 2026, 15:00 in Tbilisi (UTC+4).
NOW = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
DAY = date(2026, 10, 30)
CHOOSE_FIRST = "🎯 Выбрать один шаг"
CHOOSE_OTHER = "🎯 Выбрать другое"
RECORDS_ONLY = "📋 Пока только записи"


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


def _meal(db, groups=(), back=0, hour=9, minute=0):
    """``back``: days before Friday the 30th, at ``hour`` local."""
    day = DAY - timedelta(days=back)
    when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE)
    db.add(
        Meal(
            user_id=1,
            description="суп",
            meal_time=when.astimezone(UTC),
            calories=400,
            proteins=10,
            fats=5,
            carbohydrates=50,
            nutrients={},
            ai_analysis={"food_items": [], "food_groups": list(groups)},
        )
    )
    db.commit()


def _user(db):
    db.expire_all()
    return db.query(User).one()


def _card(db, now=NOW, tab=st.TODAY):
    return st.open_view(db, 42, tab, now)


def _rows(screen):
    return [[b.text for b in row] for row in screen.markup.inline_keyboard]


def _labels(screen):
    return [label for row in _rows(screen) for label in row]


def _press(db, screen, label, now=NOW):
    for row in screen.markup.inline_keyboard:
        for button in row:
            if button.text == label or button.text == f"✓ {label}":
                return st.handle_callback(db, 42, button.callback_data, now)
    raise AssertionError(f"no {label} in {_rows(screen)}")


def _picker(db, now=NOW):
    return _press(db, _card(db, now), CHOOSE_FIRST, now)


def _late_week(db, hour=19):
    """Seven days whose first food comes at ``hour``."""
    for back in range(7):
        _meal(db, back=back, hour=hour)
        _meal(db, back=back, hour=23)


# --- no step until one is chosen --------------------------------------------


def test_a_card_with_records_has_no_step_only_the_button_until_one_is_chosen(db):
    _meal(db)
    card = _card(db)
    assert "👣" not in card.text
    assert card.picture.get("step") is None
    assert _rows(card)[0] == [CHOOSE_FIRST]
    assert not any("Силы" in label for label in _labels(card))


def test_an_empty_day_has_no_button_to_choose(db):
    assert CHOOSE_FIRST not in _labels(_card(db))


def test_the_choice_screen_says_one_and_can_be_changed_and_has_a_way_back(db):
    _meal(db)
    picker = _picker(db)
    assert "🎯 <b>Что попробовать первым?</b>" in picker.text
    assert "Одно на выбор. Сменить можно в любой момент." in picker.text
    assert picker.picture is None
    assert _rows(picker)[-1] == ["⬅️ Назад: сегодня"]
    assert "👣" not in _press(db, picker, "⬅️ Назад: сегодня").text


def test_the_choice_offers_two_or_three_and_always_records_only(db):
    _meal(db)
    labels = _labels(_picker(db))
    assert RECORDS_ONLY in labels
    assert 2 <= len(labels) - 2 <= 3


def test_a_group_seen_nearly_every_day_is_not_offered(db):
    for back in range(7):
        _meal(db, ["veg", "fruit"], back=back)
    assert "🥕 Добавить овощ или фрукт" not in _labels(_picker(db))


def test_the_least_present_comes_first():
    days = {"veg": 3, "fruit": 3, "legumes": 0, "anchor": 1, "nuts": 2, "grain": 2}
    assert ns.options({}, days, False)[0] == ns.PROTEIN


def test_with_everything_present_there_are_still_two_to_choose_from():
    days = {group: 7 for group in ("veg", "fruit", "legumes", "anchor", "nuts")}
    assert len(ns.options({}, {**days, "grain": 7, "omega": 7}, False)) >= 2


def test_a_direction_with_every_step_refused_is_not_offered():
    refused = {ns.NO_SUGGEST: ["овощи", "банан", "яблоко", "ягоды"]}
    assert ns.PLANTS not in ns.options(refused, {}, False)


def test_earlier_is_offered_only_when_first_food_is_late_in_the_records(db):
    for back in range(7):
        _meal(db, back=back, hour=9)
        _meal(db, back=back, hour=19)
    assert "🕐 Поесть один раз пораньше" not in _labels(_picker(db))


def test_earlier_is_offered_for_a_late_first_food(db):
    _late_week(db)
    assert _labels(_picker(db))[0] == "🕐 Поесть один раз пораньше"


def test_earlier_needs_a_few_days_of_records(db):
    _meal(db, back=0, hour=19)
    _meal(db, back=1, hour=19)
    assert "🕐 Поесть один раз пораньше" not in _labels(_picker(db))


# --- the chosen step --------------------------------------------------------


def test_choosing_puts_a_step_of_that_direction_on_the_card(db):
    _meal(db)
    card = _press(db, _picker(db), "🥕 Добавить овощ или фрукт")
    shown = ns.view(_user(db).settings).step
    assert {"veg", "fruit"} & set(shown.groups)
    assert f"{shown.title}" in card.text
    assert _rows(card)[0] == ["✅ Готово", "🔄 Другой шаг", "😴 Не сейчас"]
    assert card.picture["step"]["title"]
    assert card.toast


def test_swap_stays_inside_the_direction(db):
    _meal(db)
    card = _press(db, _picker(db), "🥕 Добавить овощ или фрукт")
    for _ in range(6):
        card = _press(db, card, "🔄 Другой шаг")
        assert {"veg", "fruit"} & set(ns.view(_user(db).settings).step.groups)


def test_the_step_stays_from_day_to_day_until_it_is_changed(db):
    _meal(db)
    _press(db, _picker(db), "🥕 Добавить овощ или фрукт")
    chosen = ns.view(_user(db).settings).step.id
    tomorrow = NOW + timedelta(days=1)
    _meal(db, back=-1)
    card = _card(db, tomorrow)
    assert ns.view(_user(db).settings).step.id == chosen
    assert _rows(card)[0] == ["✅ Готово", "🔄 Другой шаг", "😴 Не сейчас"]


def test_two_not_nows_in_a_row_bring_a_different_step_next_day(db):
    _meal(db)
    card = _press(db, _picker(db), "🥕 Добавить овощ или фрукт")
    first = ns.view(_user(db).settings).step.id
    _press(db, card, "😴 Не сейчас")
    tomorrow = NOW + timedelta(days=1)
    _meal(db, back=-1)
    _press(db, _card(db, tomorrow), "😴 Не сейчас", tomorrow)
    later = NOW + timedelta(days=2)
    _meal(db, back=-2)
    _card(db, later)
    assert ns.view(_user(db).settings).step.id != first


def test_the_other_direction_is_a_tap_away_in_every_state(db):
    _meal(db)
    opened = _press(db, _picker(db), "🥕 Добавить овощ или фрукт")
    assert CHOOSE_OTHER in _labels(opened)
    done = _press(db, opened, "✅ Готово")
    assert CHOOSE_OTHER in _labels(done)
    later = _press(db, _press(db, done, "↩️ Отменить"), "😴 Не сейчас")
    assert CHOOSE_OTHER in _labels(later)


def test_the_other_direction_marks_the_one_in_use_and_replaces_it(db):
    _meal(db)
    card = _press(db, _picker(db), "🥕 Добавить овощ или фрукт")
    done = _press(db, card, "✅ Готово")
    picker = _press(db, done, CHOOSE_OTHER)
    assert "Что попробовать теперь?" in picker.text
    assert "✓ 🥕 Добавить овощ или фрукт" in _labels(picker)
    card = _press(db, picker, "🥫 Добавить готовое с белком")
    shown = ns.view(_user(db).settings)
    assert {"legumes", "anchor"} & set(shown.step.groups)
    assert shown.state == ns.OPEN


def test_choosing_the_same_direction_twice_keeps_the_step_and_its_state(db):
    _meal(db)
    card = _press(db, _picker(db), "🥕 Добавить овощ или фрукт")
    _press(db, card, "✅ Готово")
    before = _user(db).settings
    picker = _press(db, _card(db), CHOOSE_OTHER)
    _press(db, picker, "🥕 Добавить овощ или фрукт")
    assert _user(db).settings == before


def test_records_only_shows_no_step_and_no_energy_and_the_button_stays(db):
    _meal(db)
    card = _press(db, _picker(db), RECORDS_ONLY)
    assert "👣" not in card.text
    assert _rows(card)[0] == [CHOOSE_FIRST]
    assert not any("Силы" in label for label in _labels(card))
    assert _user(db).settings[ns.FOCUS] == {"id": ns.RECORDS}


def test_records_only_marks_itself_on_the_next_choice(db):
    _meal(db)
    card = _press(db, _picker(db), RECORDS_ONLY)
    assert f"✓ {RECORDS_ONLY}" in _labels(_press(db, card, CHOOSE_FIRST))


def test_a_low_mode_with_no_step_does_not_hide_ticks_or_show_its_line(db):
    _meal(db)
    user = _user(db)
    user.settings = {**user.settings, ns.ENERGY: ns.SIMPLE}
    db.commit()
    card = _card(db)
    assert "Режим" not in card.text


def test_a_direction_from_a_broken_button_changes_nothing(db):
    _meal(db)
    before = dict(_user(db).settings)
    screen = st.handle_callback(db, 42, f"{st.CALLBACK_PREFIX}{st.CHOOSE}:nope:", NOW)
    assert "<b>📊 Сегодня" in screen.text
    assert _user(db).settings == before


def test_a_stale_earlier_button_with_too_few_records_changes_nothing(db):
    _meal(db)
    before = dict(_user(db).settings)
    data = f"{st.CALLBACK_PREFIX}{st.CHOOSE}:{ns.EARLY}:"
    st.handle_callback(db, 42, data, NOW)
    assert _user(db).settings == before


@pytest.mark.parametrize("damaged", [None, "early", 7, {}, {"id": 3}, {"id": "nope"}])
def test_a_damaged_direction_reads_as_none_chosen(db, damaged):
    _meal(db)
    user = _user(db)
    user.settings = {**user.settings, ns.FOCUS: damaged}
    db.commit()
    assert _rows(_card(db))[0] == [CHOOSE_FIRST]


def test_an_old_daily_step_without_a_direction_is_not_shown(db):
    _meal(db)
    user = _user(db)
    old = {"day": DAY.isoformat(), "step": "hummus", "state": ns.OPEN}
    user.settings = {**user.settings, ns.STATE: old}
    db.commit()
    assert "👣" not in _card(db).text


def test_an_old_steps_button_without_a_direction_changes_nothing(db):
    _meal(db)
    user = _user(db)
    old = {"day": DAY.isoformat(), "step": "hummus", "state": ns.OPEN}
    user.settings = {**user.settings, ns.STATE: old}
    db.commit()
    data = f"{st.CALLBACK_PREFIX}{st.STEP}:{ns.TAKE}:20261030:hummus:"
    st.handle_callback(db, 42, data, NOW)
    assert _user(db).settings[ns.STATE]["state"] == ns.OPEN


# --- earlier: a clock of the person's own day -------------------------------


def _moments(*hours):
    """Meals at these local hours on each of five days ending on the 30th."""
    found = []
    for back in range(5):
        day = DAY - timedelta(days=back)
        for hour in hours:
            found.append(
                Meal(
                    meal_time=datetime(
                        day.year, day.month, day.day, hour, tzinfo=ZONE
                    ).astimezone(UTC)
                )
            )
    return found


def test_the_hour_is_about_two_earlier_than_the_usual_first_food():
    assert sc.early_clock(_moments(19, 23), ZONE)["hour"] == 17


def test_food_at_four_in_the_morning_is_the_end_of_the_day_not_its_start():
    """14:00, 23:00, 04:00 is a day that starts at 14:00, sleep is 04:00 to 14:00."""
    clock = sc.early_clock(_moments(14, 23, 4), ZONE)
    assert clock["hour"] == 12


def test_a_day_of_regular_morning_food_gets_no_earlier():
    assert sc.early_clock(_moments(8, 13, 19), ZONE) is None


def test_food_at_every_hour_has_no_clock_and_no_crash():
    assert sc.early_clock(_moments(*range(24)), ZONE) is None


def test_meals_without_a_time_are_left_out():
    assert sc.early_clock([Meal(meal_time=None)], ZONE) is None


def test_a_four_am_meal_does_not_count_as_early():
    """It closes the day before: the count is of days whose first food is early."""
    clock = {"id": ns.EARLY, "hour": 12, "turn": 9}
    meals = _moments(14, 23, 4)
    first, last = DAY - timedelta(days=3), DAY - timedelta(days=1)
    assert sc.count_days(meals, clock, ZONE, first, last) == 0


def test_a_day_with_first_food_before_the_hour_counts_once():
    clock = {"id": ns.EARLY, "hour": 12, "turn": 9}
    meals = _moments(11, 14, 23)
    assert sc.count_days(meals, clock, ZONE, DAY, DAY) == 1


def test_the_choice_saves_the_hour_so_the_count_does_not_move_with_the_habit(db):
    _late_week(db)
    _press(db, _picker(db), "🕐 Поесть один раз пораньше")
    saved = _user(db).settings[ns.FOCUS]
    assert saved["id"] == ns.EARLY
    assert saved["hour"] == 17
    assert isinstance(saved["turn"], int)


def test_earlier_steps_name_a_moment_not_a_clock_hour():
    for step in ns.STEPS:
        if step.cue:
            assert ":00" not in step.title
            assert step.minutes <= 2
            assert "когда встанешь" in step.title or "первым" in step.title


def test_an_earlier_step_goes_on_the_card_and_every_mode_has_one(db):
    _late_week(db)
    card = _press(db, _picker(db), "🕐 Поесть один раз пораньше")
    assert ns.view(_user(db).settings).step.cue
    assert "когда встанешь" in card.text or "первым" in card.text
    for mode in ns.MODES:
        settings = {ns.FOCUS: {"id": ns.EARLY, "hour": 17, "turn": 9}, ns.ENERGY: mode}
        chosen = ns.current(settings, DAY, lambda: {})
        assert ns.view(chosen).step is not None


# --- the count on the week --------------------------------------------------


def _plants_chosen(db):
    user = _user(db)
    user.settings = {**user.settings, ns.FOCUS: {"id": ns.PLANTS}}
    db.commit()


def _week(db):
    return _card(db, tab=st.WEEK)


def _plant_days(db, now_days, before_days, logged=7):
    for back in range(logged):
        _meal(db, ["veg"] if back < now_days else ["grain"], back=back, hour=9)
        _meal(db, ["veg"] if back < before_days else ["grain"], back=back + 7, hour=9)


def test_more_days_says_so_with_the_month(db):
    _plants_chosen(db)
    _plant_days(db, 4, 2)
    text = _week(db).text
    assert "🎯 Овощи или фрукты: 4 дня, на прошлой неделе 2." in text
    # Two in the week before, of which those on or after the 1st.
    assert "В октябре таких дней 6." in text


def test_the_same_days_offers_to_keep_simplify_or_change(db):
    _plants_chosen(db)
    _plant_days(db, 3, 3)
    screen = _week(db)
    assert "🎯 Овощи или фрукты: 3 дня, и на прошлой неделе 3." in screen.text
    assert "Можно оставить как есть, сделать проще или выбрать другое." in screen.text
    assert CHOOSE_OTHER in _labels(screen)
    assert "🪫 Сделать проще" in _labels(screen)
    assert "В октябре" not in screen.text


def test_fewer_days_says_so_plainly(db):
    _plants_chosen(db)
    _plant_days(db, 1, 3)
    screen = _week(db)
    assert "🎯 Овощи или фрукты: 1 день, на прошлой неделе 3." in screen.text
    assert "Можно оставить как есть или сделать шаг меньше." in screen.text
    assert "🪫 Сделать проще" in _labels(screen)


def test_few_records_shows_the_count_without_a_comparison(db):
    _plants_chosen(db)
    for back in range(3):
        _meal(db, ["veg"], back=back)
    for back in range(7, 14):
        _meal(db, ["veg"], back=back)
    text = _week(db).text
    assert "🎯 Овощи или фрукты: 3 дня." in text
    assert "Для сравнения пока не хватает записей." in text
    assert "на прошлой неделе" not in text


def test_four_days_of_records_in_both_weeks_is_enough_and_three_is_not():
    enough = sc.Count("Еда", 2, 2, 4, 4, 2)
    assert sc.state(enough) == sc.SAME
    assert sc.state(sc.Count("Еда", 2, 2, 3, 4, 2)) == sc.FEW
    assert sc.state(sc.Count("Еда", 2, 2, 4, 3, 2)) == sc.FEW


def test_nothing_to_count_and_too_few_records_says_nothing():
    assert sc.lines(sc.Count("Еда", 0, 0, 2, 2, 0), "октябре") == []


def test_two_weeks_of_zeros_say_nothing():
    assert sc.lines(sc.Count("Еда", 0, 0, 7, 7, 0), "октябре") == []


def test_no_month_line_when_the_month_only_just_began():
    """The week is partly October: November's count is under the week's."""
    lines = sc.lines(sc.Count("Еда", 4, 2, 7, 7, 1), "ноябре")
    assert not any("ноябре" in line for line in lines)


def test_no_direction_and_records_only_have_no_count(db):
    _plant_days(db, 4, 2)
    assert "🎯" not in _week(db).text.replace("🎯 Выбрать", "")
    user = _user(db)
    user.settings = {**user.settings, ns.FOCUS: {"id": ns.RECORDS}}
    db.commit()
    assert "🎯" not in _week(db).text


def test_make_it_simpler_lowers_the_mode_once_and_opens_today(db):
    _plants_chosen(db)
    _plant_days(db, 3, 3)
    screen = _press(db, _week(db), "🪫 Сделать проще")
    assert _user(db).settings[ns.ENERGY] == ns.LOW
    assert "<b>📊 Сегодня" in screen.text


def test_at_the_lowest_mode_there_is_nothing_simpler_to_offer(db):
    _plants_chosen(db)
    user = _user(db)
    user.settings = {**user.settings, ns.ENERGY: ns.SIMPLE}
    db.commit()
    _plant_days(db, 3, 3)
    labels = _labels(_week(db))
    assert "🪫 Сделать проще" not in labels
    assert CHOOSE_OTHER in labels


def test_the_step_itself_is_never_checked_up_on_the_day_card(db):
    _plants_chosen(db)
    _plant_days(db, 4, 2)
    assert "на прошлой неделе" not in _card(db).text


def test_an_empty_week_has_no_count(db):
    _plants_chosen(db)
    assert "🎯" not in _card(db, NOW + timedelta(days=30), st.WEEK).text


def test_the_early_count_uses_the_saved_hour(db):
    user = _user(db)
    user.settings = {**user.settings, ns.FOCUS: {"id": ns.EARLY, "hour": 17, "turn": 9}}
    db.commit()
    for back in range(7):
        _meal(db, back=back, hour=16 if back < 4 else 19)
        _meal(db, back=back + 7, hour=16 if back < 2 else 19)
    assert "🎯 Еда до 17:00: 4 дня, на прошлой неделе 2." in _week(db).text


def test_sunday_message_carries_the_same_count_and_buttons(db):
    _plants_chosen(db)
    _plant_days(db, 3, 3)
    monday = date(2026, 10, 26)
    # Mon 26 to Sun 1 Nov: records stop on the 30th, so four days in this week.
    screen = st.summary_view(db, _user(db), monday, NOW + timedelta(days=2))
    assert "🎯 Овощи или фрукты:" in screen.text


def test_the_picture_carries_the_same_count(db):
    _plants_chosen(db)
    _plant_days(db, 4, 2)
    block = _week(db).picture["count"]
    assert block["text"] == "Овощи или фрукты: 4 дня, на прошлой неделе 2."
    assert block["note"] == "В октябре таких дней 6."


def test_sizes_of_russian_days():
    assert [sc.days_word(n) for n in (0, 1, 2, 4, 5, 11, 21, 22)] == [
        "дней", "день", "дня", "дня", "дней", "дней", "день", "дня",
    ]  # fmt: skip


# --- the bot explains it ----------------------------------------------------


def test_the_explanation_says_who_chooses_and_that_earlier_is_a_guess():
    topic = explain_screen.TOPICS["step"]
    assert "выбираешь" in topic.what
    assert "догадка" in topic.why
    assert "Счёт" in topic.how
    assert "завтра будет другой" not in topic.how
