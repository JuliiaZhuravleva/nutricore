"""Who reads a meal after it changes (stage 5, slice N, section 3.1 «Readers change»).

Every test holds a known positive first: the reader sees the row, then does not. A
filter that always returned nothing would otherwise pass. Real rows, real screens, a
fake model. The list functions of the CRUD have theirs in ``test_meal_items_data``.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core.config import settings
from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import crud_meal_item
from app.db.session import get_db
from app.main import app
from app.models import Meal, User
from app.schemas.meal import MealCreate, MealUpdate
from app.services import (
    day_card,
    food_groups_retag,
    food_lists,
    meal_items,
    next_step,
    stats_screen,
    usage_meter,
    week_push,
)
from tests.test_meal_card import (  # noqa: F401 (fixtures)
    Session,
    _meal,
    _one,
    db,
    owner,
)

CHROME = os.environ.get("CHROME")
RENDER = Path(__file__).resolve().parent.parent / "render"
ZONE = ZoneInfo("Asia/Tbilisi")  # UTC+4
SATURDAY = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)  # 14:00 local
SUNDAY_EVENING = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)  # 22:00 local
IN_THAT_WEEK = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _gone(db, user, meal) -> None:
    """Deleted the way the person deletes one."""
    assert meal_items.delete_meal(db, user.id, meal.id).status == "deleted"


def _card(db, tab=stats_screen.TODAY, opened=False, now=SATURDAY):
    return stats_screen.open_view(db, 42, tab, now, opened=opened)


# --- a deleted meal does not count anywhere (10.1 «Filter») -----------------


def test_day_card_leaves_out_a_deleted_meal(db, owner):
    meal, _i = _one(db, owner, "творог", meal_time=SATURDAY - timedelta(hours=1))
    before = _card(db, opened=True)
    assert "творог" in before.text.lower() and before.picture["meals"]  # positive

    _gone(db, owner, meal)

    after = _card(db, opened=True)
    assert "творог" not in after.text.lower()
    assert after.picture["empty"] is True


def test_week_card_leaves_out_a_deleted_meal(db, owner):
    meal, _i = _one(db, owner, "творог", meal_time=SATURDAY - timedelta(days=1))
    assert "🔢 В среднем" in _card(db, stats_screen.WEEK, opened=True).text  # positive

    _gone(db, owner, meal)

    assert "🔢 В среднем" not in _card(db, stats_screen.WEEK, opened=True).text


def test_step_count_leaves_out_a_deleted_meal(db, owner):
    focus = next_step.FOCUSES[next_step.PROTEIN]
    group = sorted(focus.groups)[0]
    meal, _i = _one(db, owner, "тофу", meal_time=SATURDAY - timedelta(days=1))
    meal.ai_analysis = {
        "food_groups": [group],
        "food_items": [{"name": "тофу", "groups": [group]}],
    }
    owner.settings = {**owner.settings, next_step.FOCUS: {"id": focus.id}}
    db.commit()
    first, last = date(2026, 10, 25), date(2026, 10, 31)

    count, _month = stats_screen._step_count(db, owner, first, last, ZONE)
    assert (count.now, count.logged_now) == (1, 1)  # positive

    _gone(db, owner, meal)

    count, _month = stats_screen._step_count(db, owner, first, last, ZONE)
    assert (count.now, count.logged_now) == (0, 0)


def _late(db, owner, precision="exact") -> list:
    """Three days whose first food comes late: the pattern that offers «пораньше»."""
    meals = []
    for day in (28, 29, 30):
        for hour in (15, 23):
            moment = datetime(2026, 10, day, hour, 0, tzinfo=ZONE).astimezone(UTC)
            meal, _i = _one(db, owner, "суп", meal_time=moment)
            meal.time_precision = precision
            meals.append(meal)
    db.commit()
    return meals


def _offered(db, owner) -> list:
    screen = stats_screen._choice_screen(db, owner, SATURDAY.date(), ZONE, False)
    return [row[0].text for row in screen.markup.inline_keyboard]


EARLY_BUTTON = next_step.FOCUSES[next_step.EARLY].button


def test_the_early_step_is_not_offered_from_deleted_meals(db, owner):
    meals = _late(db, owner)
    assert EARLY_BUTTON in _offered(db, owner)  # positive

    for meal in meals:
        _gone(db, owner, meal)

    assert EARLY_BUTTON not in _offered(db, owner)


@pytest.mark.parametrize("precision", ["approx", "day_only"])
def test_the_early_step_is_not_offered_from_a_time_nobody_is_sure_of(
    db, owner, precision
):
    meals = _late(db, owner)
    assert EARLY_BUTTON in _offered(db, owner)  # positive

    for meal in meals:
        meal.time_precision = precision
    db.commit()

    assert EARLY_BUTTON not in _offered(db, owner)


def test_week_push_is_not_sent_for_a_week_of_deleted_meals(db, owner):
    meal, _i = _one(db, owner, "суп", meal_time=IN_THAT_WEEK)
    due = week_push.users_due(db, SUNDAY_EVENING)
    assert [user.telegram_id for user, _monday in due] == [42]  # positive

    _gone(db, owner, meal)

    assert week_push.users_due(db, SUNDAY_EVENING) == []


# --- the picture and its caption tell the same time -------------------------


def test_approximate_carries_its_mark_in_the_picture_and_the_caption(db, owner):
    meal, _i = _one(db, owner, "яблоко", meal_time=SATURDAY - timedelta(hours=2))
    exact = _card(db)
    assert "🕐 12:00" in exact.text  # positive: an exact time has no mark
    assert exact.picture["meals"][0]["t"] == "12:00"

    meal.time_precision = "approx"
    db.commit()

    card = _card(db)
    assert "🕐 ≈ 12:00" in card.text
    assert card.picture["meals"][0]["t"] == "≈ 12:00"


def test_no_time_is_said_in_the_picture_and_the_caption(db, owner):
    meal, _i = _one(db, owner, "яблоко", meal_time=SATURDAY - timedelta(hours=2))
    _one(db, owner, "суп", meal_time=SATURDAY - timedelta(hours=3))
    exact = _card(db)
    assert "без времени" not in exact.text  # positive: a clock needs no such words
    assert exact.picture["noTime"] is None
    assert len(exact.picture["meals"]) == 2

    meal.time_precision = "day_only"
    db.commit()

    card = _card(db)  # numbers closed: this is not left to the numbers block
    assert "🕐 11:00\n" in card.text, "the line of times stays as it was"
    assert "\nбез времени · яблоко\n" in card.text
    assert card.picture["noTime"] == "без времени · яблоко"
    assert [mark["t"] for mark in card.picture["meals"]] == ["11:00"]


def test_a_day_of_only_timeless_meals_still_says_so(db, owner):
    meal, _i = _one(db, owner, "яблоко", meal_time=SATURDAY - timedelta(hours=2))
    meal.time_precision = "day_only"
    db.commit()
    card = _card(db)
    assert "🕐" not in card.text and card.picture["meals"] == []
    assert card.picture["noTime"] == "без времени · яблоко"
    assert "без времени · яблоко" in card.text


@pytest.mark.skipif(not CHROME, reason="no CHROME: real drawing did not run")
def test_real_chrome_draws_the_no_time_line(tmp_path):
    report = json.loads((RENDER / "samples" / "day_only_no_time.json").read_text())

    def height(values: dict) -> int:
        path, out = tmp_path / "report.json", tmp_path / "card.png"
        path.write_text(json.dumps(values, ensure_ascii=False))
        done = subprocess.run(
            [sys.executable, RENDER / "watcher.py", "once", path, out],
            env={**os.environ, "RENDER_WARM": "0"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        return struct.unpack(">I", out.read_bytes()[20:24])[0]  # PNG's IHDR

    without = {key: value for key, value in report.items() if key != "noTime"}
    assert height(report) > height(without)


def test_no_time_escapes_a_name_in_the_caption_only(db, owner):
    meal, _i = _one(db, owner, "<b>", meal_time=SATURDAY - timedelta(hours=2))
    meal.time_precision = "day_only"
    db.commit()
    card = _card(db)
    assert "без времени · &lt;b&gt;" in card.text
    assert (
        card.picture["noTime"] == "без времени · <b>"
    )  # the template sets textContent


def test_many_timeless_meals_become_a_few_names_and_the_rest(db, owner):
    names = ["яблоко", "суп", "рис", "хлеб", "сыр", "чай"]
    meals = []
    for name in [*names, "Яблоко"]:
        moment = SATURDAY - timedelta(hours=2)
        meals.append(_one(db, owner, name, meal_time=moment)[0])
    for meal in meals:
        meal.time_precision = "day_only"
    db.commit()
    said = day_card.facts(meals, ZONE, lambda m: m.description)
    assert day_card.no_time_label(said) == (
        "без времени · яблоко, суп, рис, хлеб, ещё 2"
    )


# --- food_lists.seed --------------------------------------------------------


def _reliable(db, owner) -> list:
    db.refresh(owner)
    return (owner.settings or {}).get(next_step.RELIABLE) or []


def _several(db, owner, names, times, days_ago=1) -> list:
    moment = SATURDAY - timedelta(days=days_ago)
    return [_meal(db, owner, names, meal_time=moment) for _ in range(times)]


def test_seed_counts_item_names_not_the_whole_description(db, owner):
    _several(db, owner, ["рис", "курица"], food_lists.REPEATED_AT_LEAST)

    food_lists.seed(db, owner, SATURDAY)

    assert _reliable(db, owner) == ["рис", "курица"]


def test_seed_counts_a_dish_across_different_meals(db, owner):
    moment = SATURDAY - timedelta(days=1)
    for extra in range(food_lists.REPEATED_AT_LEAST - 1):
        _meal(db, owner, ["рис", f"соус {extra}"], meal_time=moment)
    food_lists.seed(db, owner, SATURDAY)
    assert _reliable(db, owner) == []  # one meal short

    _meal(db, owner, ["рис"], meal_time=moment)
    food_lists.seed(db, owner, SATURDAY)
    assert _reliable(db, owner) == ["рис"]  # positive


def test_seed_counts_a_name_once_inside_one_meal(db, owner):
    moment = SATURDAY - timedelta(days=1)
    meal = _meal(db, owner, ["овсянка"], meal_time=moment)
    for _ in range(food_lists.REPEATED_AT_LEAST):
        meal_items.add_item(db, owner.id, meal.id, name="овсянка")
    assert len(crud_meal_item.live(db, meal.id)) > food_lists.REPEATED_AT_LEAST

    food_lists.seed(db, owner, SATURDAY)

    assert _reliable(db, owner) == []


def test_seed_leaves_out_a_removed_dish(db, owner):
    meals = _several(db, owner, ["рис", "курица"], food_lists.REPEATED_AT_LEAST)
    for meal in meals:
        chicken = next(
            item for item in crud_meal_item.live(db, meal.id) if item.name == "курица"
        )
        meal_items.remove_item(db, owner.id, chicken.id)

    food_lists.seed(db, owner, SATURDAY)

    assert _reliable(db, owner) == ["рис"]


def test_seed_still_counts_a_legacy_description(db, owner):
    moment = SATURDAY - timedelta(days=1)
    db.add_all(
        Meal(user_id=owner.id, description="Овсянка", meal_time=moment)
        for _ in range(food_lists.REPEATED_AT_LEAST)
    )
    db.commit()

    food_lists.seed(db, owner, SATURDAY)

    assert _reliable(db, owner) == ["Овсянка"]


@pytest.mark.parametrize("empty", [None, "", "   ", "\n\t"])
def test_seed_skips_an_empty_description_control_a_filled_one_counts(
    db, owner, empty, monkeypatch
):
    moment = SATURDAY - timedelta(days=1)
    db.add_all(
        Meal(user_id=owner.id, description=empty, meal_time=moment)
        for _ in range(food_lists.REPEATED_AT_LEAST)
    )
    db.commit()
    seen = []
    keep = food_lists._fits
    monkeypatch.setattr(
        food_lists, "_fits", lambda name: seen.append(name) or keep(name)
    )

    food_lists.seed(db, owner, SATURDAY)

    assert seen == [] and _reliable(db, owner) == []

    db.add_all(
        Meal(user_id=owner.id, description="суп", meal_time=moment)
        for _ in range(food_lists.REPEATED_AT_LEAST)
    )
    db.commit()
    food_lists.seed(db, owner, SATURDAY)
    assert _reliable(db, owner) == ["суп"]


def test_seed_leaves_out_a_deleted_meal(db, owner):
    meals = _several(db, owner, ["рис"], food_lists.REPEATED_AT_LEAST)
    _gone(db, owner, meals[0])

    food_lists.seed(db, owner, SATURDAY)
    assert _reliable(db, owner) == []  # one short now

    _several(db, owner, ["рис"], 1)
    food_lists.seed(db, owner, SATURDAY)
    assert _reliable(db, owner) == ["рис"]  # positive


# --- food_groups_retag.pending: the raw select ------------------------------


def _strip_groups(db, meal, **fields) -> None:
    """Back to how a meal looked before food groups existed."""
    meal.ai_analysis = None
    meal.nutrients = {"foods": ["тофу"]}
    for key, value in fields.items():
        setattr(meal, key, value)
    db.commit()


def test_retag_leaves_out_a_deleted_meal(db, owner):
    meal, _i = _one(db, owner, "тофу")
    _strip_groups(db, meal)
    assert meal in food_groups_retag.pending(db)  # positive

    _gone(db, owner, meal)

    assert food_groups_retag.pending(db) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"description": None, "nutrients": None},
        {"description": "", "nutrients": {"foods": []}},
        {"description": "  ", "nutrients": None},
        {"description": None, "nutrients": {"foods": ["", " "]}},
    ],
    ids=["nothing", "empty", "blank name", "blank foods"],
)
def test_retag_does_not_pay_for_a_meal_with_nothing_read(db, owner, fields):
    meal, _i = _one(db, owner, "тофу")
    _strip_groups(db, meal)
    assert meal in food_groups_retag.pending(db)  # positive

    _strip_groups(db, meal, **fields)
    assert food_groups_retag.pending(db) == []

    calls = []

    async def tag(foods):
        calls.append(foods)
        usage_meter._current.get().usd += 0.001
        return "{}"

    service = AsyncMock()
    service.tag_food_groups = tag
    report = asyncio.run(food_groups_retag.run(db, service, apply=True))
    assert calls == [] and report.meals == 0


def test_retag_still_asks_about_a_named_meal_without_numbers(db, owner):
    meal, _i = _one(db, owner, "тофу")
    _strip_groups(db, meal, numbers_basis="none", calories=None)
    assert meal in food_groups_retag.pending(db)
    assert food_groups_retag._foods(meal) == ["тофу"]


# --- the export (section 3.4) -----------------------------------------------

_API_TOKEN, _EXPORT_TOKEN = "test-api-token", "test-export-token"
_HEADERS = {"X-API-Token": _API_TOKEN, "X-Export-Token": _EXPORT_TOKEN}
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
CURSOR = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", _API_TOKEN)
    monkeypatch.setattr(settings, "EXPORT_API_TOKEN", _EXPORT_TOKEN)
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()


@pytest.fixture
def person(db_session):
    user = User(telegram_id=4242, username="p", settings={"timezone": "Asia/Tbilisi"})
    db_session.add(user)
    db_session.commit()
    return user


def _saved(db, user, name="суп", meal_time=None) -> Meal:
    meal = meal_items.save_meal(
        db,
        MealCreate(
            description=name,
            meal_time=meal_time or datetime(2026, 10, 26, 9, 0, tzinfo=UTC),
            calories=300.0,
            nutrients={"foods": [name], "calories": 300.0},
        ),
        user.id,
    )
    return meal


def _rewind(db, *meals) -> None:
    """Their last change is long before the cursor."""
    ids = [meal.id for meal in meals]
    db.execute(update(Meal).where(Meal.id.in_(ids)).values(updated_at=LONG_AGO))
    db.commit()
    db.expire_all()


def _pull(client, **params) -> dict:
    params = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in params.items()
    }
    answer = client.get("/api/v1/meals/export", params=params, headers=_HEADERS)
    assert answer.status_code == 200, answer.text
    return {row["id"]: row for row in answer.json()["meals"]}


def test_export_says_deleted_at_on_every_row(client, db_session, person):
    meal = _saved(db_session, person)
    assert _pull(client)[meal.id]["deleted_at"] is None


def test_export_leaves_a_deleted_meal_out_of_a_plain_pull(client, db_session, person):
    kept, gone = _saved(db_session, person), _saved(db_session, person, "хлеб")
    assert set(_pull(client)) == {kept.id, gone.id}  # positive
    assert set(_pull(client, since=LONG_AGO)) == {kept.id, gone.id}

    _gone(db_session, person, gone)

    assert set(_pull(client)) == {kept.id}
    assert set(_pull(client, since=LONG_AGO)) == {kept.id}


def test_export_updated_since_tells_the_consumer_a_meal_was_deleted(
    client, db_session, person
):
    untouched, gone = _saved(db_session, person), _saved(db_session, person, "хлеб")
    _rewind(db_session, untouched, gone)
    assert _pull(client, updated_since=CURSOR) == {}  # positive: the cursor filters

    _gone(db_session, person, gone)

    rows = _pull(client, updated_since=CURSOR)
    assert set(rows) == {gone.id}
    stamp = datetime.fromisoformat(rows[gone.id]["deleted_at"])
    assert stamp.replace(tzinfo=None) > CURSOR.replace(tzinfo=None)


def test_export_updated_since_tells_the_consumer_it_came_back(
    client, db_session, person
):
    meal = _saved(db_session, person)
    change = meal_items.delete_meal(db_session, person.id, meal.id)
    _rewind(db_session, meal)
    assert _pull(client, updated_since=CURSOR) == {}  # positive

    meal_items.undo(db_session, person.id, change.event.id)

    rows = _pull(client, updated_since=CURSOR)
    assert rows[meal.id]["deleted_at"] is None


def test_since_stays_on_meal_time_and_updated_since_finds_a_back_dated_meal(
    client, db_session, person
):
    meal = _saved(db_session, person)
    cursor = datetime(2026, 10, 20, tzinfo=UTC)
    _rewind(db_session, meal)
    assert meal.id in _pull(client, since=cursor)  # positive
    assert _pull(client, updated_since=CURSOR) == {}

    back = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    change = meal_items.set_time(db_session, person.id, meal.id, back, "exact")
    assert change.status == meal_items.DONE

    assert meal.id not in _pull(client, since=cursor)  # as it always was: H1
    assert meal.id in _pull(client, updated_since=CURSOR)


def test_since_and_updated_since_together_are_both_applied(client, db_session, person):
    meal = _saved(db_session, person)
    assert meal.id in _pull(client, since=LONG_AGO, updated_since=CURSOR)  # positive
    late = datetime(2026, 12, 1, tzinfo=UTC)
    assert _pull(client, since=late, updated_since=CURSOR) == {}


def _set_time(db, user, meal):
    moment = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    return meal_items.set_time(db, user.id, meal.id, moment, "exact")


def _quantity(db, user, meal):
    item = crud_meal_item.live(db, meal.id)[0]
    return meal_items.set_quantity(db, user.id, item.id, 2.0)


def _add(db, user, meal):
    return meal_items.add_item(db, user.id, meal.id, name="хлеб")


def _crud_update(db, _user, meal):
    return crud_meal.update(db, meal, MealUpdate(description="уха"))


def _json_direct(db, _user, meal):
    """A direct JSON write, as a new dict: the retag run writes this way."""
    food_groups_retag._write(meal, [{"name": "суп", "groups": []}])
    db.commit()


@pytest.mark.parametrize(
    "change",
    [_set_time, _quantity, _add, _crud_update, _json_direct],
    ids=["time", "quantity", "add", "crud update", "direct JSON"],
)
def test_every_change_moves_updated_at_control_another_row_stays(
    client, db_session, person, change
):
    meal, other = _saved(db_session, person), _saved(db_session, person, "хлеб")
    _rewind(db_session, meal, other)
    assert _pull(client, updated_since=CURSOR) == {}  # positive

    change(db_session, person, meal)

    assert set(_pull(client, updated_since=CURSOR)) == {meal.id}


def test_reading_a_meal_moves_nothing(client, db_session, person):
    meal = _saved(db_session, person)
    _rewind(db_session, meal)
    assert meal.id in _pull(client)  # positive: it was read
    db_session.commit()
    assert _pull(client, updated_since=CURSOR) == {}
