"""The opt-in draft (M10, M10a, M10b) and the rollout line (M12), through real handlers.

Every test sends a real ``Update`` into ``create_bot_application()``. The model is a
fake, so what is asserted is what would reach Telegram and what the rows hold. No
network, no paid call.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import UTC, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from telegram import Update
from telegram.error import TelegramError

from app.core.config import settings
from app.crud.crud_meal import crud_meal
from app.db.session import get_db
from app.main import app as rest_app
from app.models import Meal, MealMessage, MealSource, PersonalFood, User
from app.services import (
    capture,
    food_groups_retag,
    food_lists,
    meal_card,
    settings_screen,
    stats_screen,
)
from app.services import telegram as tg
from tests.test_capture_failures import _tap_update
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    NOW_TS,
    NUTRITION,
    OWNER,
    Session,
    Wire,
    _voice,
    _with_app,
    clock,
    db,
    owner,
    photo,
    reader,
    send,
    text,
)
from tests.test_capture_voice import OGG, FakeWhisper

M10 = "<i>✓ Черновик перед записью: вкл. Еда попадёт в дневник после «✅ Записать».</i>"
M10A = "<i>✓ Черновик перед записью: выкл. Еда записывается сразу.</i>"
M10B = (
    "<b>📋 Черновик, пн 26 октября, 13:20</b>\n"
    "рис, курица и овощи\n"
    "В дневнике появится после кнопки «✅ Записать»."
)
M12 = (
    "Теперь еда записывается сразу, без подтверждения. "
    "Как это работает: ⚙️ Настройки → ℹ️ Как это работает."
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", None)
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", 0.05)
    numbers = itertools.count(5000, 500)
    start = Wire.__init__

    def go_on(self):
        start(self)
        self.next_id = next(numbers)

    monkeypatch.setattr(Wire, "__init__", go_on)
    monkeypatch.setattr(tg._card_delivery, "_screens", {})


@pytest.fixture(autouse=True)
def _hint_is_old(db, owner):
    owner.settings = {
        **(owner.settings or {}),
        "capture_hint_seen": True,
        "rollout_seen": True,
    }
    db.commit()


@pytest.fixture
def drafting(db, owner):
    owner.settings = {**owner.settings, "draft_mode": True}
    db.commit()


def capture_temporary_failure() -> Exception:
    return RuntimeError("down")


async def _drained() -> None:
    while tg._meal_reads:
        await asyncio.gather(*list(tg._meal_reads))


def _run(steps) -> Wire:
    box = {}

    async def wrapped(application, wire):
        box["wire"] = wire
        await steps(application, wire)
        await _drained()

    asyncio.run(_with_app(wrapped))
    return box["wire"]


def tap(data: str, message_id: int = 4900) -> Wire:
    tg._card_delivery._screens.clear()

    async def steps(application, _wire):
        await application.process_update(_tap_update(application, data, message_id))

    return _run(steps)


def _keyboard(params: dict) -> list:
    markup = params.get("reply_markup")
    if markup is None:
        return []
    markup = json.loads(markup) if isinstance(markup, str) else markup
    return markup["inline_keyboard"]


def _datas(params: dict) -> list:
    return [[b["callback_data"] for b in row] for row in _keyboard(params)]


def _labels(params: dict) -> list:
    return [[b["text"] for b in row] for row in _keyboard(params)]


def _last(db) -> Meal:
    db.expire_all()
    return db.scalars(select(Meal).order_by(Meal.id.desc())).first()


def _draft_read(db, words: str = "рис") -> Meal:
    send(text(words))
    return _last(db)


def _day_card(db) -> str:
    return stats_screen.handle_callback(db, OWNER, "st:t", now=NOW).text


# --- M10: the switch --------------------------------------------------------------


def test_the_hub_has_the_switch_beside_the_week_and_stays_at_five_rows(db, owner):
    wire = tap("set:open")
    params = wire.edits()[0]
    assert _labels(params)[1] == ["🗓 Итог недели: вкл", "📋 Черновик: выкл"]
    assert _datas(params)[1] == ["set:week", "set:draft"]
    assert len(_keyboard(params)) == 5
    assert "📋 Черновик: выкл. Еда записывается сразу." in params["text"]


def test_the_switch_says_so_and_the_row_holds_it(db, owner):
    on = tap("set:draft").edits()[0]
    assert on["text"].startswith(M10 + "\n\n<b>⚙️ Настройки</b>")
    assert _labels(on)[1] == ["🗓 Итог недели: вкл", "📋 Черновик: вкл"]
    assert "📋 Черновик: вкл. Еда попадёт в дневник после «✅ Записать»." in on["text"]
    db.expire_all()
    assert db.get(User, owner.id).settings["draft_mode"] is True

    off = tap("set:draft").edits()[0]
    assert off["text"].startswith(M10A + "\n\n<b>⚙️ Настройки</b>")
    assert _labels(off)[1] == ["🗓 Итог недели: вкл", "📋 Черновик: выкл"]
    db.expire_all()
    assert db.get(User, owner.id).settings["draft_mode"] is False
    # What else the person set is still there.
    assert db.get(User, owner.id).settings["timezone"] == "Asia/Tbilisi"


def test_a_hub_that_never_flipped_it_reads_off(db, owner):
    assert "draft_mode" not in (owner.settings or {})
    assert settings_screen.draft_on(owner) is False
    assert settings_screen.draft_on(None) is False


def test_turning_it_off_leaves_a_draft_a_draft_and_the_next_meal_is_written(
    db, owner, reader, drafting
):
    draft = _draft_read(db)
    assert draft.is_draft
    tap("set:draft")
    db.expire_all()
    assert db.get(Meal, draft.id).is_draft
    wire = send(text("суп"))
    assert not _last(db).is_draft
    assert wire.edits()[-1]["text"] == "Записано: рис, курица и овощи."


# --- control: with it off, nothing here shows ----------------------------------------


def test_control_with_the_setting_off_a_meal_is_written_and_nothing_says_draft(
    db, owner, reader
):
    wire = send(text("рис"), photo())
    assert not any(meal.is_draft for meal in db.scalars(select(Meal)))
    assert not any("Черновик" in said or "черновик" in said for said in wire.seen())
    labels = [
        label for p in wire.sent() + wire.edits() for row in _labels(p) for label in row
    ]
    assert "✅ Записать" not in labels and labels  # known-positive: buttons were there


# --- M10b: the draft ---------------------------------------------------------------


def test_a_text_is_a_draft_on_a_real_row_with_the_exact_screen(
    db, owner, reader, drafting
):
    wire = send(text("рис"))
    meal = _last(db)
    assert meal.is_draft and meal.reading_status == "read"
    first = wire.sent()[0]
    assert first["text"].startswith("<b>📋 Черновик, пн 26 октября, 13:20</b>\nрис\n")
    assert first["parse_mode"] == "HTML"
    done = wire.edits()[-1]
    assert done["text"] == M10B
    assert done["parse_mode"] == "HTML"
    assert _labels(done) == [
        ["✅ Записать", "✏️ Исправить"],
        ["🕐 Время", "🗑 Не записывать"],
    ]
    mid = meal.id
    assert _datas(done) == [
        [f"ml:ok:{mid}", f"ml:o:{mid}:r"],
        [f"ml:t:{mid}:r", f"ml:d:{mid}:r"],
    ]


def test_the_first_line_of_a_draft_has_its_buttons_while_the_read_is_out(
    db, owner, reader, drafting
):
    wire = send(text("рис"))
    mid = _last(db).id
    assert _datas(wire.sent()[0])[0] == [f"ml:ok:{mid}", f"ml:o:{mid}:r"]


def test_a_photo_is_a_draft_and_its_line_never_says_it_is_in_the_diary(
    db, owner, reader, drafting
):
    wire = send(photo())
    meal = _last(db)
    assert meal.is_draft
    assert wire.sent()[0]["text"] == "Фото сохранено, читаю…"
    assert wire.edits()[-1]["text"] == M10B
    assert not any("в дневнике" in said.lower() for said in wire.seen()[:1])


def test_a_new_photo_during_a_draft_is_its_own_draft(db, owner, reader, drafting):
    send(photo())
    send(photo())
    meals = db.scalars(select(Meal).order_by(Meal.id)).all()
    assert len(meals) == 2
    assert all(meal.is_draft for meal in meals)


def test_an_album_is_one_draft(db, owner, reader, drafting):
    wire = send(photo(media_group_id="a"), photo(media_group_id="a"), drain=False)
    meals = db.scalars(select(Meal)).all()
    assert len(meals) == 1 and meals[0].is_draft
    assert wire.sent()[0]["text"] == "Фото сохранено, читаю…"
    assert not any("фото в дневнике" in said for said in wire.seen())


def test_an_album_split_keeps_each_part_a_draft_and_no_line_says_diary(
    db, owner, reader, drafting
):
    send(photo(media_group_id="a"), photo(media_group_id="a"))
    meal = _last(db)
    wire = tap(f"ml:za:{meal.id}")
    db.expire_all()
    meals = db.scalars(select(Meal).where(Meal.deleted_at.is_(None))).all()
    assert len(meals) == 2
    assert all(row.is_draft for row in meals)
    assert not any("в дневнике, читаю" in said for said in wire.seen())
    assert wire.seen()  # known-positive: the lines did go out


def test_an_exact_dish_is_a_draft_too_and_final_at_once(db, owner, reader, drafting):
    db.add(PersonalFood(user_id=owner.id, canonical_name="Овсянка"))
    db.commit()
    wire = send(text("овсянка"))
    assert reader.text == []  # known-negative of the paid call
    meal = _last(db)
    assert meal.is_draft
    assert crud_meal.get_user_meals(db, owner.id) == []
    assert wire.sent()[0]["text"] == (
        "<b>📋 Черновик, пн 26 октября, 13:20</b>\n"
        "Овсянка\n"
        "В дневнике появится после кнопки «✅ Записать»."
    )
    assert _datas(wire.sent()[0])[0][0] == f"ml:ok:{meal.id}"


def test_a_draft_has_a_time_of_its_own_and_comes_back_to_itself(
    db, owner, reader, drafting
):
    meal = _draft_read(db)
    wire = tap(f"ml:r:{meal.id}")
    assert wire.edits()[0]["text"] == M10B
    assert _datas(wire.edits()[0])[0][0] == f"ml:ok:{meal.id}"


def test_the_card_of_a_draft_says_what_it_is_and_can_record_it(
    db, owner, reader, drafting
):
    meal = _draft_read(db)
    wire = tap(f"ml:o:{meal.id}:r")
    card = wire.edits()[0]
    assert card["text"].startswith("<b>📋 Черновик, пн 26 октября, 13:20</b>")
    assert _datas(card)[0] == [f"ml:ok:{meal.id}"]
    assert _labels(card)[0] == ["✅ Записать"]


def test_control_the_card_of_a_recorded_meal_has_no_record_button(db, owner, reader):
    send(text("рис"))
    meal = _last(db)
    card = tap(f"ml:o:{meal.id}:r").edits()[0]
    assert card["text"].startswith("<b>🍽 Приём, ")
    assert "✅ Записать" not in [b for row in _labels(card) for b in row]


def test_a_correction_means_the_draft_and_it_stays_a_draft(
    db, owner, reader, clock, drafting
):
    meal = _draft_read(db)
    reader.answer = {**NUTRITION, "foods": ["сыр"]}
    wire = send(text("ещё сыр"))
    db.expire_all()
    assert db.get(Meal, meal.id).is_draft
    assert crud_meal.get_user_meals(db, owner.id) == []
    assert any("сыр" in said for said in wire.seen())


def test_a_bare_time_moves_the_draft_not_the_meal_before_it(db, owner, reader, clock):
    send(text("суп", date=NOW_TS - 1800))
    recorded = _last(db)
    tap("set:draft")
    draft = _draft_read(db)
    before = recorded.meal_time
    send(text("в 13:00"))
    db.expire_all()
    assert db.get(Meal, recorded.id).meal_time == before
    assert db.get(Meal, draft.id).meal_time != before
    assert db.get(Meal, draft.id).is_draft


# --- ✅ Записать ---------------------------------------------------------------------


def test_record_turns_it_into_m1a_and_it_counts_from_then_on(
    db, owner, reader, drafting
):
    meal = _draft_read(db)
    assert crud_meal.get_user_meals(db, owner.id) == []
    wire = tap(f"ml:ok:{meal.id}")
    db.expire_all()
    assert not db.get(Meal, meal.id).is_draft
    line = wire.edits()[0]
    assert line["text"] == "Записано: рис, курица и овощи."
    assert "✅ Записать" not in [b for row in _labels(line) for b in row]
    assert [row.id for row in crud_meal.get_user_meals(db, owner.id)] == [meal.id]
    assert "Рис" in _day_card(db)


def test_a_second_tap_on_record_changes_nothing(db, owner, reader, drafting):
    meal = _draft_read(db)
    tap(f"ml:ok:{meal.id}")
    db.expire_all()
    rev = db.get(Meal, meal.id).rev
    wire = tap(f"ml:ok:{meal.id}")
    db.expire_all()
    assert not db.get(Meal, meal.id).is_draft and db.get(Meal, meal.id).rev == rev
    assert wire.edits()[0]["text"] == "Записано: рис, курица и овощи."


def test_record_while_the_read_is_out_lets_the_read_land(db, owner, reader, drafting):
    async def steps(application, wire):
        reader.gate = asyncio.Event()
        message = text("рис")
        await application.process_update(
            Update.de_json(
                {"update_id": message["message_id"], "message": message},
                application.bot,
            )
        )
        with Session_factory() as session:
            meal_id = session.scalars(select(Meal)).one().id
        line_id = wire.next_id
        await application.process_update(
            _tap_update(application, f"ml:ok:{meal_id}", line_id)
        )
        reader.gate.set()

    Session_factory = tg.SessionLocal
    wire = _run(steps)
    meal = _last(db)
    assert not meal.is_draft
    assert meal.reading_status == "read"
    # What the read found is on the record, and the line says what it now is.
    assert crud_meal.get_user_meals(db, owner.id)[0].id == meal.id
    assert wire.seen()[-1] == "Записано: рис, курица и овощи."


def test_record_tells_an_incremental_pull_the_meal_is_there(
    db, owner, reader, drafting
):
    meal = _draft_read(db)
    before = db.get(Meal, meal.id).updated_at
    tap(f"ml:ok:{meal.id}")
    db.expire_all()
    after = db.get(Meal, meal.id).updated_at
    assert after is not None and (before is None or after >= before)
    assert meal.id in [
        row.id
        for row in crud_meal.get_for_export(
            db, owner_telegram_ids=[owner.telegram_id], updated_since=after
        )
    ]


def test_someone_elses_draft_is_not_recorded(db, owner, reader, drafting):
    other = User(telegram_id=77, settings={})
    db.add(other)
    db.commit()
    from app.schemas.meal import MealCreate

    theirs = crud_meal.build(MealCreate(meal_time=NOW, description="x"), other.id)
    theirs.is_draft = True
    db.add(theirs)
    db.commit()
    tap(f"ml:ok:{theirs.id}")
    db.expire_all()
    assert db.get(Meal, theirs.id).is_draft


def test_a_deleted_draft_is_not_recorded_by_an_old_button(db, owner, reader, drafting):
    meal = _draft_read(db)
    tap(f"ml:d:{meal.id}:r")
    wire = tap(f"ml:ok:{meal.id}")
    db.expire_all()
    assert db.get(Meal, meal.id).is_draft
    assert "Записано" not in " ".join(wire.seen())


# --- 🗑 Не записывать ---------------------------------------------------------------


def test_not_writing_takes_it_out_and_the_way_back_is_still_a_draft(
    db, owner, reader, drafting
):
    meal = _draft_read(db)
    wire = tap(f"ml:d:{meal.id}:r")
    db.expire_all()
    assert db.get(Meal, meal.id).deleted_at is not None
    undo = next(
        data for row in _datas(wire.edits()[0]) for data in row if "ml:u" in data
    )
    tap(undo)
    db.expire_all()
    back = db.get(Meal, meal.id)
    assert back.deleted_at is None and back.is_draft
    assert crud_meal.get_user_meals(db, owner.id) == []


# --- the filter: never counted before ✅ --------------------------------------------


def test_a_draft_is_in_no_reader_a_person_sees(db, owner, reader, drafting):
    draft = _draft_read(db, "рис")
    day = NOW - timedelta(days=1), NOW + timedelta(days=1)
    assert crud_meal.get_user_meals(db, owner.id) == []
    assert crud_meal.get_user_meals_between(db, owner.id, *day) == []
    assert crud_meal.get_multi(db) == []
    assert crud_meal.get_for_export(db, owner_telegram_ids=[owner.telegram_id]) == []
    assert food_groups_retag.pending(db) == []
    assert "Рис" not in _day_card(db)
    # Known-positive: the same row, once written, is read by them all.
    draft = db.get(Meal, draft.id)
    draft.is_draft = False
    db.commit()
    assert crud_meal.get_user_meals(db, owner.id)
    assert crud_meal.get_user_meals_between(db, owner.id, *day)
    assert crud_meal.get_multi(db)
    assert crud_meal.get_for_export(db, owner_telegram_ids=[owner.telegram_id])
    assert "Рис" in _day_card(db)


def test_the_reliable_food_list_does_not_learn_from_a_draft(
    db, owner, reader, drafting
):
    for _ in range(4):
        send(text("рис"))
    food_lists.seed(db, owner, NOW)
    db.expire_all()
    assert food_lists.counts(db.get(User, owner.id))[0] == 0


def test_a_dish_takes_no_numbers_or_groups_from_a_draft(db, owner, reader, drafting):
    from app.models import MealItem
    from app.services import meal_items

    send(text("рис"))
    item = db.scalars(select(MealItem)).first()
    assert item is not None
    item.personal_food_id = 9
    item.numbers_source = "model"
    for field in meal_items.FIELDS:
        setattr(item, f"base_{field}", 10.0)
    db.commit()
    assert meal_items._last_item(db, 9) is None
    meal = db.get(Meal, item.meal_id)
    meal.ai_analysis = {
        **(meal.ai_analysis or {}),
        "food_groups": ["grains"],
        "food_items": [{"name": item.name, "groups": ["grains"]}],
    }
    db.commit()
    assert meal_items._groups_of_dish(db, 9, item.name) is None
    # Known-positive: the same item in a written meal is found.
    meal.is_draft = False
    db.commit()
    assert meal_items._last_item(db, 9) is not None
    assert meal_items._groups_of_dish(db, 9, item.name) == ["grains"]


def test_the_rest_api_does_not_hand_out_a_draft(
    db, owner, reader, drafting, monkeypatch
):
    draft = _draft_read(db)
    monkeypatch.setattr(settings, "API_TOKEN", "t", raising=False)
    rest_app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(rest_app, base_url="http://localhost")
        headers = {"X-API-Token": "t"}
        assert (
            client.get(f"/api/v1/meals/{draft.id}", headers=headers).status_code == 404
        )
        assert (
            client.delete(f"/api/v1/meals/{draft.id}", headers=headers).status_code
            == 404
        )
        put = client.put(
            f"/api/v1/meals/{draft.id}", headers=headers, json={"meal_type": "x"}
        )
        assert put.status_code == 404
        # Known-positive: the door is open and the row was only hidden.
        draft.is_draft = False
        db.commit()
        assert (
            client.get(f"/api/v1/meals/{draft.id}", headers=headers).status_code == 200
        )
    finally:
        rest_app.dependency_overrides.pop(get_db, None)


# --- Приёмы -------------------------------------------------------------------------


def test_the_list_of_the_day_names_a_draft_and_its_chip_opens_it(
    db, owner, reader, drafting
):
    reader.answer = {**NUTRITION, "foods": ["рис"]}
    meal = _draft_read(db)
    wire = tap("ml:dl:20261026:t")
    screen = wire.edits()[0]
    assert "13:20 · черновик: рис" in screen["text"]
    assert f"ml:o:{meal.id}:t" in [d for row in _datas(screen) for d in row]
    assert "В этом дне записей нет." not in screen["text"]


def test_the_list_keeps_a_written_meal_beside_a_draft_in_time_order(db, owner, reader):
    reader.answer = {**NUTRITION, "foods": ["суп"]}
    send(text("суп", date=NOW_TS - 3600))
    tap("set:draft")
    reader.answer = {**NUTRITION, "foods": ["рис"]}
    send(text("рис"))
    said = tap("ml:dl:20261026:t").edits()[0]["text"]
    assert "12:20 · суп" in said and "13:20 · черновик: рис" in said
    assert said.index("12:20 · суп") < said.index("13:20 · черновик: рис")


def test_a_deleted_draft_leaves_the_list(db, owner, reader, drafting):
    meal = _draft_read(db)
    tap(f"ml:d:{meal.id}:r")
    assert "черновик" not in tap("ml:dl:20261026:t").edits()[0]["text"]


# --- a restart --------------------------------------------------------------------


def test_a_restart_keeps_the_draft_and_reads_what_it_had_not_read(
    db, owner, reader, drafting
):
    async def before(application, wire):
        reader.gate = asyncio.Event()
        message = photo()
        await application.process_update(
            Update.de_json(
                {"update_id": message["message_id"], "message": message},
                application.bot,
            )
        )
        for task in list(tg._meal_reads):
            task.cancel()
        await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)

    asyncio.run(_with_app(before))
    meal = _last(db)
    assert meal.is_draft
    assert meal.reading_status == "pending"
    assert capture.pending_meals(db) == [meal.id]
    reader.gate = None

    async def after(application, wire):
        await tg._replay_pending(application, [meal.id])
        await _drained()
        box.append(wire)

    box = []
    asyncio.run(_with_app(after))
    done = _last(db)
    assert done.is_draft and done.reading_status == "read"
    assert box[0].seen()[-1] == M10B
    # The next day it is all still here, and no message reminded anybody.
    assert not any("напомин" in said.lower() for said in box[0].seen())


def test_the_sweep_gives_up_on_a_stuck_draft_and_the_line_says_no_diary(
    db, owner, reader, drafting, monkeypatch
):
    async def born(application, wire):
        reader.gate = asyncio.Event()
        message = photo()
        await application.process_update(
            Update.de_json(
                {"update_id": message["message_id"], "message": message},
                application.bot,
            )
        )
        for task in list(tg._meal_reads):
            task.cancel()
        await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)

    asyncio.run(_with_app(born))
    meal = _last(db)
    late = meal.created_at + capture.STUCK_AFTER + timedelta(minutes=1)
    monkeypatch.setattr(capture, "_now", lambda: late.replace(tzinfo=UTC))
    assert capture.stuck_meals(db, {}) == [meal.id]  # known-positive: it is found
    reader.gate = None

    async def sweep(application, wire):
        tg._live_reads.clear()
        await tg._sweep(application)
        await _drained()

    wire = _run(sweep)
    assert meal.id not in capture.stuck_meals(db, {})
    said = wire.seen()[0]
    assert said.startswith("Фото сохранено, распознать еду сейчас не вышло.")
    assert "В дневнике" not in said
    assert _last(db).is_draft


def test_a_failed_draft_is_read_again_by_itself_and_is_still_a_draft(
    db, owner, reader, drafting, monkeypatch
):
    reader.error = capture_temporary_failure()
    send(photo())
    meal = _last(db)
    assert meal.reading_status == "failed"
    assert capture._may_read_by_itself(db, meal)


def test_a_dead_read_of_a_draft_can_still_be_opened_and_recorded(
    db, owner, reader, drafting
):
    reader.error = RuntimeError("down")
    send(photo())
    meal = _last(db)
    card = tap(f"ml:o:{meal.id}:r").edits()[0]
    assert "В дневнике запись" not in card["text"]
    assert _datas(card)[0] == [f"ml:ok:{meal.id}"]
    said = tap(f"ml:ok:{meal.id}").edits()[0]["text"]
    assert "В дневнике запись с этим фото." in said  # now it is true
    assert not _last(db).is_draft


def test_control_a_recorded_failed_photo_still_says_it_is_in_the_diary(
    db, owner, reader
):
    reader.error = RuntimeError("down")
    wire = send(photo())
    assert "В дневнике запись с этим фото." in wire.seen()[-1]


def test_the_hints_do_not_say_a_draft_is_written(db, reader, owner):
    owner.settings = {
        "timezone": "Asia/Tbilisi",
        "draft_mode": True,
        "rollout_seen": True,
    }
    db.commit()
    wire = send(text("рис"))
    assert not any("На этом всё" in said for said in wire.seen())
    db.expire_all()
    assert not db.get(User, owner.id).settings.get("capture_hint_seen")


def test_logs_of_a_draft_carry_no_body(db, owner, reader, drafting, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        meal = _draft_read(db, "секретный рис")
        tap(f"ml:ok:{meal.id}")
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
    assert not any("секретный" in said for said in ours)


# --- M12: the rollout line ----------------------------------------------------------


@pytest.fixture
def unseen(db, owner):
    owner.settings = {k: v for k, v in owner.settings.items() if k != "rollout_seen"}
    db.commit()


def _old_meal(db, owner, **fields) -> Meal:
    meal = Meal(
        user_id=owner.id, meal_time=NOW - timedelta(days=30), description="x", **fields
    )
    db.add(meal)
    db.commit()
    return meal


def _rollouts(wire) -> list:
    return [p for p in wire.sent() if p["text"] == M12]


def test_m12_goes_after_the_first_line_silent_with_the_keyboard(
    db, owner, reader, unseen
):
    _old_meal(db, owner)
    wire = send(text("рис"))
    assert wire.sent()[0]["text"] != M12  # known-positive: the line is first
    told = _rollouts(wire)
    assert len(told) == 1
    assert wire.sent().index(told[0]) == 1
    assert told[0]["disable_notification"] is True
    assert "reply_parameters" not in told[0]
    markup = told[0]["reply_markup"]
    markup = json.loads(markup) if isinstance(markup, str) else markup
    assert "keyboard" in markup and "inline_keyboard" not in markup
    assert (
        db.scalars(
            select(MealMessage).where(MealMessage.message_id == wire.next_id)
        ).first()
        is None
    )


def test_m12_is_sent_once(db, owner, reader, unseen):
    _old_meal(db, owner)
    assert len(_rollouts(send(text("рис")))) == 1
    db.expire_all()
    assert db.get(User, owner.id).settings["rollout_seen"] is True
    assert _rollouts(send(text("суп"))) == []


def test_m12_a_newcomer_never_gets_it_and_never_will(db, owner, reader, unseen):
    wire = send(text("рис"))
    assert _rollouts(wire) == [] and wire.sent()  # known-positive: there was a line
    db.expire_all()
    assert db.get(User, owner.id).settings["rollout_seen"] is True
    _old_meal(db, owner)
    assert _rollouts(send(text("суп"))) == []


def test_m12_a_deleted_old_meal_still_means_an_old_person(db, owner, reader, unseen):
    _old_meal(db, owner, deleted_at=NOW - timedelta(days=2))
    assert len(_rollouts(send(text("рис")))) == 1


def test_m12_someone_elses_old_meal_means_nothing(db, owner, reader, unseen):
    other = User(telegram_id=77, settings={})
    db.add(other)
    db.commit()
    _old_meal(db, other)
    assert _rollouts(send(text("рис"))) == []


def test_m12_an_album_gets_one(db, owner, reader, unseen):
    _old_meal(db, owner)
    wire = send(photo(media_group_id="a"), photo(media_group_id="a"))
    assert len(_rollouts(wire)) == 1


def test_m12_an_exact_dish_gets_it_too(db, owner, reader, unseen):
    _old_meal(db, owner)
    db.add(PersonalFood(user_id=owner.id, canonical_name="Овсянка"))
    db.commit()
    wire = send(text("овсянка"))
    assert wire.sent()[0]["text"].startswith("Записано: Овсянка.")
    assert len(_rollouts(wire)) == 1


def test_m12_a_telegram_refusal_leaves_it_for_the_next_capture(
    db, owner, reader, unseen, monkeypatch
):
    _old_meal(db, owner)
    from telegram import Message

    real = Message.reply_text

    async def refuse(self, words, *args, **kwargs):
        if words == M12:
            raise TelegramError("no")
        return await real(self, words, *args, **kwargs)

    monkeypatch.setattr(Message, "reply_text", refuse)
    wire = send(text("рис"))
    assert wire.sent() and _rollouts(wire) == []
    assert _last(db).reading_status == "read"  # the read did not pay for it
    monkeypatch.setattr(Message, "reply_text", real)
    db.expire_all()
    assert not db.get(User, owner.id).settings.get("rollout_seen")
    assert len(_rollouts(send(text("суп")))) == 1


def test_m12_is_not_sent_while_it_would_be_false(db, owner, reader, unseen):
    _old_meal(db, owner)
    owner.settings = {**owner.settings, "draft_mode": True}
    db.commit()
    assert _rollouts(send(text("рис"))) == []
    db.expire_all()
    assert db.get(User, owner.id).settings["rollout_seen"] is True


def test_m12_control_a_second_sentence_about_dishes_waits_for_its_slice():
    assert "Блюда" not in capture.M12_ROLLOUT
    assert capture.M12_ROLLOUT == M12
    assert "🍲 Блюда" not in str(tg.main_keyboard.to_dict())


# --- layout -------------------------------------------------------------------------


def test_every_button_of_a_draft_fits(db, owner, reader, drafting):
    meal = _draft_read(db)
    line = tap(f"ml:r:{meal.id}").edits()[0]
    card = tap(f"ml:o:{meal.id}:r").edits()[0]
    for params in (line, card):
        rows = _keyboard(params)
        assert len(rows) <= 5
        for row in rows:
            budget = {1: 34, 2: 20, 3: 13}[len(row)]
            for button in row:
                assert len(button["text"]) <= budget, button["text"]
                assert len(button["callback_data"].encode()) <= 64


def test_a_voice_draft_keeps_the_way_to_what_was_heard(db, owner):
    from app.models import MealReading

    meal = crud_meal.build(
        capture.MealCreate(meal_time=NOW, description="суп"), owner.id
    )
    meal.is_draft = True
    db.add(meal)
    db.flush()
    db.add(
        MealReading(
            meal_id=meal.id,
            source_ids=[],
            kind="transcript",
            status=meal_card.HEARD,
            output={"transcript": "суп"},
            applied=True,
        )
    )
    db.commit()
    if meal_card.heard_of(db, meal.id) is None:
        pytest.fail("the fixture did not make a heard voice")
    rows = meal_card.line_markup(db, meal).inline_keyboard
    assert [b.text for b in rows[0]] == ["✅ Записать", "✏️ Исправить"]
    assert [b.text for b in rows[1]] == ["🕐 Время", "🗑 Не записывать"]
    assert [b.text for b in rows[2]] == ["🎙 Текст"]


@pytest.fixture
def whisper(monkeypatch):
    fake = FakeWhisper()
    monkeypatch.setattr(settings, "WHISPER_URL", fake.url)
    monkeypatch.setattr(Wire, "file_bytes", OGG)
    yield fake
    fake.close()


def test_a_voice_draft_keeps_the_disclosure_and_never_says_it_is_in_the_diary(
    db, owner, reader, whisper, drafting
):
    wire = send(_voice())
    assert wire.sent()[0]["text"] == "Голосовое сохранено, слушаю…"
    meal = _last(db)
    assert meal.is_draft
    said = wire.edits()[-1]["text"]
    assert said.startswith("<b>📋 Черновик, пн 26 октября, 13:20</b>\n")
    assert (
        "Голосовые пока в пробном режиме. Что услышано, видно по кнопке «🎙 Текст». "
        "Если сервис проекта не отвечает, голосовое уходит во внешний сервис."
    ) in said
    assert "запись уже в дневнике" not in said
    assert _datas(wire.edits()[-1])[2] == [f"ml:vt:{meal.id}:1"]
    assert _datas(wire.edits()[-1])[0][0] == f"ml:ok:{meal.id}"


def test_it_is_food_on_a_draft_day_is_a_draft_too(db, owner, reader, drafting):
    reader.answer = {"foods": [], "calories": 0}
    send(text("кошка на окне сидит"))
    source = db.scalars(select(MealSource)).one()
    reader.answer = dict(NUTRITION)
    wire = tap(f"ml:nf:{source.id}", message_id=4960)
    db.expire_all()
    meal = db.scalars(select(Meal).where(Meal.deleted_at.is_(None))).one()
    assert meal.is_draft
    said = wire.edits()[-1]["text"]
    assert said.startswith("<b>📋 Черновик, ") and "Записано" not in said
    assert crud_meal.get_user_meals(db, owner.id) == []


def test_a_draft_is_offered_no_join(db, owner, reader, clock):
    send(text("суп", date=NOW_TS - 600))
    tap("set:draft")
    wire = send(text("рис"))
    labels = [b for p in wire.sent() + wire.edits() for row in _labels(p) for b in row]
    assert "🔗 Объединить" not in labels


def test_sources_of_a_draft_are_kept_like_any_other(db, owner, reader, drafting):
    send(text("рис"))
    source = db.scalars(select(MealSource)).one()
    assert source.meal_id == _last(db).id and source.text == "рис"
