"""A voice is typed words said aloud: once heard, its words go where the same words
typed would go (a weight, «ещё хлеб» to the meal before it, «убери суп» in reply to a
meal), and only food is read. Through the real handlers, with the local service a real
HTTP server on the loopback and the model a fake: nothing here is paid for.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import BodyMetric, Meal, MealReading, MealSource
from app.services import capture
from tests.test_capture_failures import _a_clean_run  # noqa: F401 (autouse)
from tests.test_capture_findings_m import refuse  # noqa: F401 (fixture)
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    OWNER,
    Session,
    _item_names,
    _mapped,
    _meal_now,
    _voice,
    clock,
    db,
    owner,
    reader,
    send,
    text,
)
from tests.test_capture_voice import (  # noqa: F401 (fixtures)
    HEARD,
    M4A,
    _ogg,
    paid,
    seen,
    tap,
    whisper,
)


def _voice_meal(db) -> Meal:
    db.expire_all()
    source = db.scalars(
        select(MealSource).where(MealSource.kind == "voice").order_by(MealSource.id)
    ).first()
    return db.get(Meal, source.origin_meal_id)


def _weights(db) -> list:
    db.expire_all()
    return [m.weight for m in db.scalars(select(BodyMetric))]


def test_a_spoken_weight_is_a_weight_and_no_meal(db, seen, reader, whisper, clock):
    whisper.answers("72,5 кг")
    wire = send(_voice())
    assert _weights(db) == [72.5]
    assert reader.text == []  # never read as food
    meal = _voice_meal(db)
    assert meal.deleted_at is not None
    assert "72,5" in wire.edits()[-1]["text"]
    # The words stay heard, on the voice's own meal, as a voice with no food keeps them.
    heard = db.scalars(select(MealReading).where(MealReading.meal_id == meal.id)).one()
    assert heard.output["transcript"] == "72,5 кг"


def test_spoken_more_goes_to_the_meal_before_the_voice(
    db, seen, reader, whisper, clock
):
    before = _meal_now(db, seen, minutes_ago=20, names=("суп",))
    whisper.answers("ещё хлеб")
    wire = send(_voice())
    db.expire_all()
    assert sorted(_item_names(db, before)) == ["суп", "хлеб"]
    assert "ещё хлеб" not in reader.text  # the words were not read as a meal
    assert _voice_meal(db).deleted_at is not None
    assert "хлеб" in wire.edits()[-1]["text"]
    assert [
        m.id for m in db.scalars(select(Meal).where(Meal.deleted_at.is_(None)))
    ] == [before.id]


def test_a_voice_reply_takes_a_dish_out_of_that_meal(db, seen, reader, whisper, clock):
    meal = _meal_now(db, seen, minutes_ago=200, names=("суп", "хлеб"))
    _meal_now(db, seen, minutes_ago=10, names=("чай",))  # the last one is another
    whisper.answers("убери суп")
    send(_voice() | {"reply_to_message": _mapped(db, meal)})
    db.expire_all()
    assert _item_names(db, meal) == ["хлеб"]
    assert reader.text == []


def test_a_spoken_thanks_is_no_meal_and_costs_no_read(db, seen, reader, whisper, clock):
    whisper.answers("Спасибо")
    wire = send(_voice())
    assert reader.text == []
    assert _voice_meal(db).deleted_at is not None
    assert wire.edits()[-1]["text"].startswith(capture.M3A_NO_FOOD)


def test_a_spoken_question_gets_the_typed_answer(db, seen, reader, whisper, clock):
    whisper.answers("Сколько белка в твороге?")
    wire = send(_voice())
    assert reader.text == []
    assert _voice_meal(db).deleted_at is not None
    assert wire.edits()[-1]["text"] == capture.M3B_QUESTION


def test_spoken_food_is_read_as_ever(db, seen, reader, whisper, clock):
    wire = send(_voice())
    assert reader.text == [HEARD]
    assert wire.edits()[-1]["text"] == M4A
    assert _voice_meal(db).deleted_at is None


def test_a_typed_change_waits_while_a_voice_is_not_heard(db, seen, reader, clock):
    before = _meal_now(db, seen, minutes_ago=20, names=("суп",))
    came = capture.Incoming(
        telegram_id=OWNER,
        username="o",
        chat_id=CHAT,
        message_id=91001,
        kind="voice",
        text=None,
        file_id="v",
        file_unique_id="vu",
        file_size=1000,
        duration_s=3,
        media_group_id=None,
        reply_to=None,
        sent_at=NOW - timedelta(minutes=1),
        forward_date=None,
    )
    capture.born(db, came)
    voice_meal = _voice_meal(db)
    wire = send(text("ещё хлеб"))
    assert wire.sent()[-1]["text"] == capture.STILL_READING
    db.expire_all()
    assert _item_names(db, before) == ["суп"]
    assert _item_names(db, voice_meal) == []
    assert reader.text == []


def test_a_voice_is_routed_once_and_a_read_again_reads_food(
    db, seen, reader, whisper, clock, monkeypatch
):
    calls = []
    real = capture.route_heard

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(capture, "route_heard", counted)
    reader.error = ValueError("boom")
    send(_voice())
    assert calls == [1]
    reader.error = None
    wire = tap(f"ml:rr:{_voice_meal(db).id}:r")
    assert calls == [1]  # heard already: no second routing
    assert wire.edits()[-1]["text"] == M4A


def test_a_routed_voice_whose_line_never_went_out_answers_as_its_own_view(
    db, seen, reader, whisper, clock, refuse
):
    """The answer then goes as a new message; it is the «ещё» toast, not the meal's
    record line, so it must not be mapped as the meal's line."""
    from app.models import MealMessage
    from app.services import telegram as tg

    before = _meal_now(db, seen, minutes_ago=20, names=("суп",))
    whisper.answers("ещё хлеб")
    refuse.method, refuse.left = "sendMessage", tg.SHOW_TRIES
    wire = send(_voice())
    db.expire_all()
    assert sorted(_item_names(db, before)) == ["суп", "хлеб"]
    answer = wire.sent()[-1]
    assert "хлеб" in answer["text"]
    rows = db.scalars(select(MealMessage).where(MealMessage.meal_id == before.id))
    # The voice itself points at the meal it joined, as typed words do ("source").
    assert sorted(row.view for row in rows) == ["other", "source"]


def test_a_routed_answer_that_fails_says_so_and_the_voice_is_not_read_as_food(
    db, seen, reader, whisper, clock, monkeypatch
):
    """The words went somewhere already: the line may not hang on «читаю…» nor the
    voice be read as a meal on top of it."""
    from app.services import weight_screen

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(weight_screen, "typed", broken)
    whisper.answers("72,5 кг")
    wire = send(_voice())
    assert wire.edits()[-1]["text"] == capture.M7F_REFUSED
    assert reader.text == []
    assert _voice_meal(db).deleted_at is not None


def test_a_routed_voice_whose_line_mapping_fails_still_answers(
    db, seen, reader, whisper, clock, monkeypatch
):
    before = _meal_now(db, seen, minutes_ago=20, names=("суп",))

    real = capture.map_message

    def broken(db, chat_id, message_id, meal_id, view="line"):
        if view == "other":  # the line's own mapping, not the voice's "source"
            raise RuntimeError("boom")
        return real(db, chat_id, message_id, meal_id, view)

    monkeypatch.setattr(capture, "map_message", broken)
    whisper.answers("ещё хлеб")
    wire = send(_voice())
    db.expire_all()
    assert sorted(_item_names(db, before)) == ["суп", "хлеб"]
    assert "хлеб" in wire.edits()[-1]["text"]
    assert "ещё хлеб" not in reader.text  # «хлеб» alone is the new name's numbers
    assert _voice_meal(db).deleted_at is not None


def test_a_spoken_routine_with_an_ampersand_is_escaped_on_the_html_line(
    db, seen, reader, whisper, clock, monkeypatch
):
    from app.services import telegram as tg

    monkeypatch.setattr(
        tg.stats_screen, "tick_by_name", lambda db, tid, t: "Витамины & омега"
    )
    whisper.answers("витамины и омега")
    wire = send(_voice())
    assert wire.edits()[-1]["text"] == "✓ Витамины &amp; омега"
    assert reader.text == []


def test_a_spoken_change_behind_an_unheard_voice_is_read_not_lost(
    db, seen, reader, whisper, clock
):
    """Two voices, the second heard first: the typed guard would ask to send it again,
    which a voice cannot be. It is read as food, as before."""
    came = capture.Incoming(
        telegram_id=OWNER,
        username="o",
        chat_id=CHAT,
        message_id=91001,
        kind="voice",
        text=None,
        file_id="v",
        file_unique_id="vu",
        file_size=1000,
        duration_s=3,
        media_group_id=None,
        reply_to=None,
        sent_at=NOW - timedelta(minutes=1),
        forward_date=None,
    )
    capture.born(db, came)
    whisper.answers("ещё хлеб")
    wire = send(_voice())
    assert capture.STILL_READING not in str(wire.edits())
    assert "ещё хлеб" in reader.text
    db.expire_all()
    heard = db.scalars(
        select(MealSource).where(MealSource.kind == "voice").order_by(MealSource.id)
    ).all()[-1]
    assert db.get(Meal, heard.origin_meal_id).deleted_at is None
