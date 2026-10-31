"""Two people, one bot: nothing of one reaches the other, by any way in (slice O).

Every id-bearing verb of ``ml:``, ``dh:``, ``fav:`` and ``wt:`` is tapped by B with A's
ids through the real application. B sees no word of A's, no row moves, and the guard says
it was the guard. Then A taps the same data: that tap must get past the guard and do its
work, or the probe would only be showing that the id was wrong.
``test_every_id_bearing_verb_is_probed`` reads the verb tables, so a new verb with no
probe here fails.

SQLite by default. ``TWO_PEOPLE_DB_URL`` runs the same file on a migrated Postgres, whose
tables it empties: never point it at anything but a throwaway database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy import text as sql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session, sessionmaker
from sqlalchemy.pool import StaticPool
from telegram import Update

import app.services.ai_call_log_service as log_service
import app.services.inbound_message_service as inbound_service
import app.services.model_selection as model_selection
import app.services.telegram as tg
from app.core.config import settings
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_subscription import crud_subscription
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    BodyMetric,
    FavoriteMeal,
    Meal,
    MealMessage,
    MealReading,
    MealSource,
    PersonalFood,
    PersonalFoodEmbedding,
    RecordEvent,
    User,
)
from app.services import dishes, favorites, meal_card, meal_items, weight_screen
from app.services.product_lookup_service import ImageSignals, SavedFoodRAGStrategy
from tests.test_capture_router import NOW_TS, NUTRITION, Wire

A, B = 1001, 2002
MARK = "зюзя"  # in every name of A's, and nowhere else
A_FILE = "zyuzya-file"
A_KCAL = 613
TZ = {"timezone": "Asia/Tbilisi"}
LINE, CHOOSER = 7700, 7701
_ids = iter(range(100, 10_000_000))
# Written by the bot about what the tap itself did, for anyone: not a row of a person.
_LOGS = {"inbound_messages", "ai_call_logs"}
PG_URL = os.environ.get("TWO_PEOPLE_DB_URL")


class Chats(Wire):
    """Answers in the chat it was asked about, so a mapped message is mapped right."""

    def _result(self, name, params):
        result = super()._result(name, params)
        if isinstance(result, dict) and "chat" in result and "chat_id" in params:
            result["chat"]["id"] = int(params["chat_id"])
        return result


def _engine():
    if PG_URL:
        engine = create_engine(PG_URL)
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        with engine.begin() as connection:
            connection.execute(sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        return engine
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def Session(monkeypatch):
    engine = _engine()
    factory = sessionmaker(bind=engine)
    for module in (tg, inbound_service, log_service, model_selection):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(A))
    # A person the gate lets in. Dropped there, B would pass every probe below unasked.
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", str(B))
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(tg, "_schedule_personal_food_save", lambda **_: None)
    monkeypatch.setattr(tg._card_delivery, "_screens", {})
    yield factory
    engine.dispose()


@pytest.fixture
def model(monkeypatch):
    """No model is reached: what it was asked stays here, to be looked at."""
    asked = SimpleNamespace(text=[], image=[], answer=dict(NUTRITION))

    async def analyze(*, kind, payload, input_ref, telegram_id):
        asked.text.append(payload)
        return dict(asked.answer)

    async def resolve(payload, *, telegram_id, input_ref, caption=None, more_images=()):
        asked.image.append(payload)
        raise RuntimeError("the model is not up in this test")

    monkeypatch.setattr(tg, "_analyze_and_parse", analyze)
    monkeypatch.setattr(tg, "resolve_meal_nutrition", resolve)
    return asked


def _person(db, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, username=f"u{telegram_id}", settings=dict(TZ))
    db.add(user)
    db.commit()
    return user


def _dish(db, user, name, **columns) -> PersonalFood:
    dish = PersonalFood(
        user_id=user.id,
        canonical_name=name,
        per_100g_calories=A_KCAL,
        per_100g_proteins=12,
        per_100g_fats=14,
        per_100g_carbs=1,
        numbers_basis="per_100",
        **columns,
    )
    db.add(dish)
    db.commit()
    return dish


def _meal(db, user, dish, when, *names, **columns) -> Meal:
    meal = Meal(
        user_id=user.id,
        description=", ".join(names or (dish.canonical_name,)),
        meal_time=when,
        sent_at=when,
        calories=A_KCAL,
        nutrients={"foods": list(names or (dish.canonical_name,))},
        **columns,
    )
    db.add(meal)
    db.flush()
    for name in names or (dish.canonical_name,):
        crud_meal_item.add(
            db,
            meal_id=meal.id,
            name=name,
            personal_food_id=dish.id,
            base_calories=A_KCAL,
        )
    db.commit()
    return meal


def _source(db, user, chat, message_id, meal=None, **columns) -> MealSource:
    columns.setdefault("kind", "text")
    source = MealSource(
        user_id=user.id,
        meal_id=meal.id if meal else None,
        origin_meal_id=meal.id if meal else None,
        chat_id=chat,
        message_id=message_id,
        sent_at=datetime.now(UTC),
        **columns,
    )
    db.add(source)
    db.commit()
    return source


def _world(db) -> SimpleNamespace:
    """A's life in rows, one for each thing a verb can want. B has a little of their own,
    so that B's fallback screens are not empty ones."""
    a, b = _person(db, A), _person(db, B)
    now = datetime.now(UTC)
    ago = lambda **kw: now - timedelta(**kw)  # noqa: E731
    soup = _dish(db, a, f"{MARK} суп", barcode="4600000000001")
    porridge = _dish(db, a, f"{MARK} каша")
    hidden = _dish(db, a, f"{MARK} скрытое", hidden_at=ago(days=1))
    meal_items.save_exact(db, a.id, [soup], meal_time=ago(days=9))

    lunch = _meal(db, a, soup, ago(minutes=50), f"{MARK} суп", f"{MARK} каша")
    item = crud_meal_item.live(db, lunch.id)[0]
    db.add(MealMessage(chat_id=A, message_id=LINE, meal_id=lunch.id, view="line"))
    _source(db, a, A, 7600, lunch, text=f"{MARK} суп, {MARK} каша")
    db.commit()

    # Near enough to be joined with ``lunch``, and joined with its own second.
    near = _meal(db, a, porridge, ago(minutes=40), f"{MARK} каша рядом")
    near_item = crud_meal_item.live(db, near.id)[0]
    _source(db, a, A, 7601, near, text=f"{MARK} каша рядом")
    db.commit()
    host = _meal(db, a, soup, ago(hours=8), f"{MARK} ужин")
    _source(db, a, A, 7602, host, text=f"{MARK} ужин")
    guest = _meal(db, a, porridge, ago(hours=8, minutes=5), f"{MARK} закуска")
    guest_source = _source(db, a, A, 7603, guest, text=f"{MARK} закуска")
    assert meal_items.join(db, a.id, host.id, guest.id).status == meal_items.DONE

    album = _meal(db, a, soup, ago(hours=30), f"{MARK} альбом")
    for number in (7604, 7605):
        _source(
            db, a, A, number, album, kind="photo", media_group_id="g", file_id=A_FILE
        )
    voice = _meal(db, a, soup, ago(hours=32), f"{MARK} голос")
    _source(db, a, A, 7606, voice, kind="voice", file_id=A_FILE)
    db.add(
        MealReading(
            meal_id=voice.id,
            kind="transcription",
            status=meal_card.HEARD,
            output={"transcript": f"{MARK} голос"},
        )
    )
    draft = _meal(db, a, soup, ago(hours=34), f"{MARK} черновик", is_draft=True)
    unread = Meal(
        user_id=a.id,
        description="",
        meal_time=ago(hours=36),
        sent_at=ago(hours=36),
        reading_status="failed",
    )
    db.add(unread)
    db.commit()
    _source(db, a, A, 7607, unread, kind="photo", file_id=A_FILE)
    no_food = _source(db, a, A, 7608, text=f"{MARK} размышления")
    repeat = _source(db, a, A, 7609, text="добавка")
    remove = _source(db, a, A, 7610, text=f"убери {MARK} суп")
    db.add(MealMessage(chat_id=A, message_id=CHOOSER, meal_id=lunch.id, view="other"))
    db.commit()

    undoable = _meal(db, a, soup, ago(hours=38), f"{MARK} порция")
    undo_item = crud_meal_item.live(db, undoable.id)[0]
    event = meal_items.set_grams(db, a.id, undo_item.id, 321).event
    logged = meal_items.save_exact(db, a.id, [porridge], meal_time=ago(hours=40))
    logged.resolution_source = favorites.FAVORITE_SOURCE

    star = FavoriteMeal(
        user_id=a.id,
        name=f"{MARK} суп",
        personal_food_id=soup.id,
        calories=A_KCAL,
        nutrients={"foods": [f"{MARK} суп"]},
        use_count=2,
    )
    gone_star = FavoriteMeal(
        user_id=a.id,
        name=f"{MARK} каша",
        personal_food_id=porridge.id,
        calories=A_KCAL,
        nutrients={"foods": [f"{MARK} каша"]},
        removed_at=ago(days=1),
    )
    weight = BodyMetric(
        user_id=a.id,
        weight=61.3,
        timestamp=ago(hours=2),
        metrics={"source": weight_screen.SOURCE},
    )
    db.add_all([star, gone_star, weight])
    db.commit()
    hidden.hidden_at = None
    db.commit()
    dishes.handle_callback(db, A, f"dh:x:{hidden.id}")
    hide_event = db.scalar(
        select(RecordEvent.id)
        .where(RecordEvent.user_id == a.id)
        .order_by(RecordEvent.id.desc())
    )

    # B's own. B holds a real grant: a locked person would pass every probe here.
    crud_subscription.create_subscription(db, telegram_id=B, granted_by_telegram_id=A)
    borscht = _dish(db, b, "борщ")
    b_meal = _meal(db, b, borscht, ago(minutes=30), "борщ")
    b_item = crud_meal_item.live(db, b_meal.id)[0]
    b_source = _source(db, b, B, 7600, text="добавка")
    db.add(MealMessage(chat_id=B, message_id=LINE + 2, meal_id=b_meal.id, view="line"))
    db.commit()
    return SimpleNamespace(
        a=a,
        b=b,
        lunch=lunch.id,
        item=item.id,
        near=near.id,
        near_item=near_item.id,
        host=host.id,
        guest_source=guest_source.id,
        album=album.id,
        voice=voice.id,
        draft=draft.id,
        unread=unread.id,
        no_food=no_food.id,
        repeat=repeat.id,
        remove=remove.id,
        event=event.id,
        logged=logged.id,
        soup=soup.id,
        porridge=porridge.id,
        hidden=hidden.id,
        hide_event=hide_event,
        star=star.id,
        gone_star=gone_star.id,
        weight=weight.id,
        at=int((now - timedelta(minutes=70)).timestamp()),
        borscht=borscht.id,
        b_meal=b_meal.id,
        b_item=b_item.id,
        b_source=b_source.id,
    )


@pytest.fixture
def db(Session):
    session = Session()
    yield session
    session.close()


@pytest.fixture
def world(db, model):
    return _world(db)


def _as(person: int, **fields) -> dict:
    return {
        "message_id": next(_ids),
        "date": NOW_TS,
        "chat": {"id": person, "type": "private"},
        "from": {"id": person, "is_bot": False, "first_name": "x", "username": "x"},
        **fields,
    }


def _run(person_updates) -> Chats:
    """Each dict one update through the real application; returns the wire."""
    tg._card_delivery._screens.clear()
    box = {}

    async def steps():
        application = tg.create_bot_application()
        wire = Chats()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.start()
        box["wire"] = wire
        try:
            for update in person_updates:
                await application.process_update(
                    Update.de_json(update, application.bot)
                )
                await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)
        finally:
            await application.stop()
            await application.shutdown()

    asyncio.run(steps())
    return box["wire"]


def _tap(person: int, data: str, message_id: int = 4900) -> Chats:
    message = _as(person)
    return _run(
        [
            {
                "update_id": message["message_id"],
                "callback_query": {
                    "id": "1",
                    "from": message["from"],
                    "chat_instance": "c",
                    "data": data,
                    "message": {
                        "message_id": message_id,
                        "date": NOW_TS,
                        "chat": message["chat"],
                        "text": "x",
                    },
                },
            }
        ]
    )


def _say(person: int, body: str, **fields) -> Chats:
    message = _as(person, text=body, **fields)
    return _run([{"update_id": message["message_id"], "message": message}])


def _rows(db) -> dict:
    """Every row of every table, as it stands."""
    db.expire_all()
    rows = {}
    for table in Base.metadata.sorted_tables:
        if table.name in _LOGS:
            continue
        keys = [c.name for c in table.primary_key.columns]
        for row in db.execute(select(table)).mappings():
            rows[(table.name, *[row[k] for k in keys])] = {
                k: repr(v) for k, v in row.items()
            }
    return rows


def _moved(before: dict, after: dict, b_id: int | None = None) -> dict:
    """What changed. B's settings hold the slot a tap closes, and are B's to change."""
    moved = {}
    for key in before.keys() | after.keys():
        was, now = before.get(key), after.get(key)
        if was == now:
            continue
        if b_id is not None and key[:2] == ("users", b_id) and was and now:
            ignored = ("settings", "updated_at")
            if {k: v for k, v in was.items() if k not in ignored} == {
                k: v for k, v in now.items() if k not in ignored
            }:
                continue
        moved[key] = (was, now)
    return moved


def _shown_to(wire: Chats) -> list:
    """What came down the wire, in order: every word, label and file of it."""
    seen = []
    for name, params in wire.calls:
        if name in ("getMe", "getFile"):
            continue
        shown = {k: v for k, v in params.items() if k not in ("message_id", "chat_id")}
        seen.append((name, json.dumps(shown, ensure_ascii=False, default=str)))
    return seen


def _assert_nothing_of_a(wire: Chats, person: int = B) -> None:
    assert _shown_to(wire), "B got no answer at all: stopped at a gate, not at the row"
    words = " ".join(body for _name, body in _shown_to(wire)).casefold()
    assert MARK not in words, words
    assert A_FILE not in words, words
    for name, params in wire.calls:
        if "chat_id" in params:
            assert int(params["chat_id"]) == person, (name, params)


# What each verb is given: ``w`` holds A's ids.
PROBES = {
    "ml:r": lambda w: f"ml:r:{w.lunch}",
    "ml:o": lambda w: f"ml:o:{w.lunch}:r",
    "ml:op": lambda w: f"ml:op:{w.lunch}:0:r",
    "ml:mo": lambda w: f"ml:mo:{w.lunch}:r",
    "ml:gr": lambda w: f"ml:gr:{w.lunch}:r",
    "ml:gt": lambda w: f"ml:gt:{w.lunch}:veg:r",
    "ml:n": lambda w: f"ml:n:{w.lunch}:1:r",
    "ml:i": lambda w: f"ml:i:{w.item}:r",
    "ml:ni": lambda w: f"ml:ni:{w.item}:1:r",
    "ml:q": lambda w: f"ml:q:{w.item}:2:r",
    "ml:g": lambda w: f"ml:g:{w.item}:r",
    "ml:x": lambda w: f"ml:x:{w.item}:r",
    "ml:d": lambda w: f"ml:d:{w.lunch}:r",
    "ml:u": lambda w: f"ml:u:{w.event}:r",
    "ml:t": lambda w: f"ml:t:{w.lunch}:r",
    "ml:ts": lambda w: f"ml:ts:{w.lunch}:{w.at}:e:r",
    "ml:pk": lambda w: f"ml:pk:{w.lunch}:a:r:0",
    "ml:pa": lambda w: f"ml:pa:{w.lunch}:{w.soup}:r",
    "ml:pr": lambda w: f"ml:pr:{w.item}:{w.porridge}:r",
    "ml:fi": lambda w: f"ml:fi:{w.near_item}:1:r",
    "ml:fm": lambda w: f"ml:fm:{w.lunch}:1:r",
    "ml:ka": lambda w: f"ml:ka:{w.remove}:{w.item}",
    "ml:kw": lambda w: f"ml:kw:{w.repeat}",
    "ml:kn": lambda w: f"ml:kn:{w.repeat}",
    "ml:ga": lambda w: f"ml:ga:{w.item}:200:r",
    "ml:ok": lambda w: f"ml:ok:{w.draft}",
    "ml:rr": lambda w: f"ml:rr:{w.unread}:r",
    "ml:w": lambda w: f"ml:w:{w.lunch}:r",
    "ml:nf": lambda w: f"ml:nf:{w.no_food}",
    "ml:vt": lambda w: f"ml:vt:{w.voice}:1",
    "ml:zq": lambda w: f"ml:zq:{w.album}:r",
    "ml:za": lambda w: f"ml:za:{w.album}",
    "ml:j": lambda w: f"ml:j:{w.lunch}:{w.near}:r",
    "ml:jl": lambda w: f"ml:jl:{w.lunch}:r",
    "ml:z": lambda w: f"ml:z:{w.guest_source}:r",
    "ml:src": lambda w: f"ml:src:{w.lunch}:r",
    "ml:sf": lambda w: f"ml:sf:{w.album}",
    "dh:o": lambda w: f"dh:o:{w.soup}:l0",
    "dh:mo": lambda w: f"dh:mo:{w.soup}:l0",
    "dh:rn": lambda w: f"dh:rn:{w.soup}",
    "dh:nm": lambda w: f"dh:nm:{w.soup}",
    "dh:pg": lambda w: f"dh:pg:{w.soup}",
    "dh:gr": lambda w: f"dh:gr:{w.soup}",
    "dh:gt": lambda w: f"dh:gt:{w.soup}:veg:1",
    "dh:n": lambda w: f"dh:n:{w.soup}:1",
    "dh:h": lambda w: f"dh:h:{w.soup}:0",
    "dh:fv": lambda w: f"dh:fv:{w.porridge}:1",
    "dh:log": lambda w: f"dh:log:{w.soup}",
    "dh:x": lambda w: f"dh:x:{w.soup}",
    "dh:u": lambda w: f"dh:u:{w.hide_event}",
    "dh:jl": lambda w: f"dh:jl:{w.soup}:0",
    "dh:jf": lambda w: f"dh:jf:{w.soup}",
    "dh:j": lambda w: f"dh:j:{w.porridge}:{w.soup}",
    "fav:o": lambda w: f"fav:o:{w.star}:0",
    "fav:l": lambda w: f"fav:l:{w.star}:n",
    "fav:u": lambda w: f"fav:u:{w.logged}",
    "fav:g": lambda w: f"fav:g:{w.star}",
    "fav:r": lambda w: f"fav:r:{w.star}:0",
    "fav:b": lambda w: f"fav:b:{w.gone_star}:0",
    "wt:u": lambda w: f"wt:u:{w.weight}",
}
# The tap's own message matters to these: the chooser looks at what it answers.
ON_MESSAGE = {"ml:kw": CHOOSER, "ml:kn": CHOOSER}
# B's id beside A's: the owner of one must not let the other through.
MIXED = {
    "ml:pa": lambda w: f"ml:pa:{w.b_meal}:{w.soup}:r",
    "ml:pr": lambda w: f"ml:pr:{w.b_item}:{w.soup}:r",
    "ml:j": lambda w: f"ml:j:{w.b_meal}:{w.near}:r",
    "ml:j reversed": lambda w: f"ml:j:{w.near}:{w.b_meal}:r",
    "ml:ka": lambda w: f"ml:ka:{w.b_source}:{w.item}",
    "dh:j": lambda w: f"dh:j:{w.borscht}:{w.soup}",
    "dh:j reversed": lambda w: f"dh:j:{w.soup}:{w.borscht}",
}
# No id in them, or only a date: B's own screens, which must not hold A's day.
NO_ID = {
    "ml:dl": lambda w: f"ml:dl:{datetime.now(UTC):%Y%m%d}:t",
    "ml:da": lambda w: f"ml:da:{datetime.now(UTC):%Y%m%d}:t",
    "dh:l": lambda w: "dh:l:0",
    "dh:f": lambda w: "dh:f",
    "fav:p": lambda w: "fav:p:0",
}


def _verbs() -> set:
    """The verbs the tables hold, as ``ml:o``, read from the patterns themselves."""
    names = set()
    for module in (meal_card, dishes, favorites):
        for pattern, _verb in module._VERBS:
            names.add(re.match(r"[a-z]+:[a-z]*", pattern.pattern).group(0))
    return names | {"wt:u"}


def test_b_is_through_the_gate_and_works_with_their_own(db, world, model):
    """What the rest of the file stands on: B is refused for the row, not for B."""
    assert "борщ" in " ".join(b for _n, b in _shown_to(_tap(B, f"ml:r:{world.b_meal}")))
    assert "Борщ" in " ".join(
        b for _n, b in _shown_to(_tap(B, f"dh:o:{world.borscht}:l0"))
    )
    meals = db.scalars(select(Meal.id).where(Meal.user_id == world.b.id)).all()
    _say(B, "гречка")
    db.expire_all()
    assert len(db.scalars(select(Meal.id).where(Meal.user_id == world.b.id)).all()) == (
        len(meals) + 1
    )


def test_every_id_bearing_verb_is_probed():
    verbs = _verbs()
    assert "ml:src" in verbs and "dh:j" in verbs and "fav:b" in verbs  # the tables read
    assert verbs == set(PROBES) | set(NO_ID)


@pytest.mark.parametrize("verb", sorted(PROBES))
def test_a_tap_with_anothers_id_finds_nothing_and_moves_nothing(
    db, world, caplog, verb
):
    data = PROBES[verb](world)
    on = ON_MESSAGE.get(verb, 4900)
    before = _rows(db)

    with caplog.at_level(logging.WARNING):
        wire = _tap(B, data, on)

    _assert_nothing_of_a(wire)
    assert _moved(before, _rows(db), world.b.id) == {}
    if verb != "wt:u":  # that guard answers «уже отменена», and writes nothing down
        assert "unknown or foreign" in caplog.text, data  # the guard is what said no

    # The known positive: the same tap, from its owner, gets through and does its work.
    before = _rows(db)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        wire = _tap(A, data, on)
    assert "unknown or foreign" not in caplog.text, data
    assert "unknown callback" not in caplog.text, data
    words = " ".join(body for _name, body in _shown_to(wire)).casefold()
    moved = _moved(before, _rows(db))
    assert MARK in words or A_FILE in words or moved, (data, words)


@pytest.mark.parametrize("verb", sorted(MIXED))
def test_a_tap_mixing_ones_own_id_with_anothers_moves_nothing(db, world, verb):
    before = _rows(db)
    wire = _tap(B, MIXED[verb](world))
    _assert_nothing_of_a(wire)
    assert _moved(before, _rows(db), world.b.id) == {}


@pytest.mark.parametrize("verb", sorted(NO_ID))
def test_a_screen_with_no_id_is_ones_own(db, world, verb):
    _assert_nothing_of_a(_tap(B, NO_ID[verb](world)))
    # The known positive: the same screen holds A's, for A.
    if verb not in ("dh:f", "ml:da"):
        words = " ".join(b for _n, b in _shown_to(_tap(A, NO_ID[verb](world))))
        assert MARK in words.casefold(), words


# --- the reply ------------------------------------------------------------------------


def _reply(person: int, message_id: int) -> dict:
    return {
        "reply_to_message": {
            "message_id": message_id,
            "date": NOW_TS,
            "chat": {"id": person, "type": "private"},
            "text": "x",
        }
    }


def _foreign_new(moved: dict, world) -> list:
    """What moved and is not B's: B may write their own rows, and nobody else's."""
    db = object_session(world.b)
    db.expire_all()

    def owners(row: dict) -> set:
        found = set()
        if "user_id" in row:
            found.add(int(row["user_id"]))
        if row.get("meal_id") not in (None, "None"):
            found.add(db.get(Meal, int(row["meal_id"])).user_id)
        return found

    wrong = []
    for key, (was, now) in moved.items():
        rows = [row for row in (was, now) if row is not None]
        if any(owners(row) != {world.b.id} for row in rows):
            wrong.append(key)
        elif any(
            row.get("personal_food_id") in {repr(world.soup), repr(world.porridge)}
            for row in rows
        ):
            wrong.append(key)
    return wrong


REPLIES = ["200", "12:10", f"убери {MARK} суп", "добавка", f"замени на {MARK} каша"]


@pytest.mark.parametrize("words", REPLIES)
@pytest.mark.parametrize("forged", [False, True], ids=["same-number", "forged-map"])
def test_a_reply_to_anothers_message_changes_nothing_of_theirs(
    db, world, words, forged
):
    """Message numbers repeat across chats, and a row that maps B's chat to A's meal is
    what a bug in the mapping would write: neither gets through."""
    if forged:
        db.add(
            MealMessage(chat_id=B, message_id=LINE, meal_id=world.lunch, view="line")
        )
        db.commit()
    before = _rows(db)

    wire = _say(B, words, **_reply(B, LINE))

    assert not _foreign_new(_moved(before, _rows(db), world.b.id), world)
    words_shown = " ".join(body for _n, body in _shown_to(wire)).casefold()
    assert f"{A_KCAL}" not in words_shown
    if MARK not in words:  # B's own words may come back to B
        _assert_nothing_of_a(wire)


def test_the_same_reply_from_its_owner_lands(db, world):
    """The known positive of the reply path."""
    before = _rows(db)
    wire = _say(A, "200", **_reply(A, LINE))
    assert _moved(before, _rows(db)), wire.calls


def test_an_edit_of_ones_own_message_with_anothers_number_stays_ones_own(db, world):
    before = _rows(db)
    message = _as(B, text="борщ", message_id=7600, edit_date=NOW_TS)
    wire = _run([{"update_id": next(_ids), "edited_message": message}])
    _assert_nothing_of_a(wire)
    assert not _foreign_new(_moved(before, _rows(db), world.b.id), world)


# --- the typed slot -------------------------------------------------------------------


def _slot(db, user, kind: str, **fields) -> None:
    db.expire_all()
    fresh = db.get(User, user.id)
    fresh.settings = {
        **(fresh.settings or {}),
        meal_card.ASK: {
            "kind": kind,
            "message_id": None,
            "at": datetime.now(UTC).isoformat(),
            "back": fields.pop("back", "r"),
            **fields,
        },
    }
    db.commit()


SLOTS = {
    meal_card.GRAMS: (lambda w: {"meal_id": w.lunch, "item_id": w.item}, "200"),
    meal_card.TIME: (lambda w: {"meal_id": w.lunch}, "12:10"),
    meal_card.DISH_ADD: (lambda w: {"meal_id": w.lunch}, "банан"),
    meal_card.DISH_REPLACE: (
        lambda w: {"meal_id": w.lunch, "item_id": w.item},
        "банан",
    ),
    meal_card.MEAL_TEXT: (lambda w: {"meal_id": w.lunch}, "банан"),
    dishes.DISH_NAME: (lambda w: {"food_id": w.soup, "back": "l0"}, "банан"),
    dishes.DISH_NUMBERS: (lambda w: {"food_id": w.soup, "back": "l0"}, "100 10 10 10"),
    dishes.DISH_PORTION: (lambda w: {"food_id": w.soup, "back": "l0"}, "200"),
    dishes.MERGE_FIND: (lambda w: {"food_id": w.soup, "back": "l0"}, "каша"),
    favorites.PORTION: (lambda w: {"food_id": w.soup, "back": str(w.star)}, "200"),
}


def test_every_slot_that_holds_an_id_is_probed():
    id_free = {dishes.DISH_FIND}
    assert set(meal_card._TYPED) == set(SLOTS) | id_free


@pytest.mark.parametrize("kind", sorted(SLOTS))
def test_a_slot_holding_anothers_id_changes_nothing_of_theirs(db, world, kind, caplog):
    fields, words = SLOTS[kind]
    _slot(db, world.b, kind, **fields(world))
    before = _rows(db)

    wire = _say(B, words)

    _assert_nothing_of_a(wire)
    assert not _foreign_new(_moved(before, _rows(db), world.b.id), world)

    # The known positive: the same slot, for its owner, gets through.
    _slot(db, world.a, kind, **fields(world))
    before = _rows(db)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        wire = _say(A, words)
    assert "unknown or foreign" not in caplog.text
    shown = " ".join(body for _n, body in _shown_to(wire)).casefold()
    assert MARK in shown or _moved(before, _rows(db)), shown


def test_a_day_slot_writes_ones_own_meal(db, world):
    _slot(db, world.b, meal_card.DAY_ADD, day=f"{datetime.now(UTC):%Y-%m-%d}")
    before = _rows(db)
    _assert_nothing_of_a(_say(B, "банан"))
    moved = _moved(before, _rows(db), world.b.id)
    assert moved and not _foreign_new(moved, world)  # the known positive: it is written


def test_the_find_slot_finds_only_ones_own(db, world):
    _slot(db, world.b, dishes.DISH_FIND, back="l0")
    assert dishes.DISH_FIND in (db.get(User, world.b.id).settings or {})["ask"]["kind"]
    wire = _say(B, "суп")
    _assert_nothing_of_a(wire)
    # The known positive.
    _slot(db, world.a, dishes.DISH_FIND, back="l0")
    assert MARK in " ".join(b for _n, b in _shown_to(_say(A, "суп"))).casefold()


# --- finding a dish -------------------------------------------------------------------


def test_the_name_and_barcode_lookups_stay_with_their_person(db, world):
    name = f"{MARK} суп"
    assert crud_personal_food.find_by_name(db, world.a.id, name).id == world.soup
    assert crud_personal_food.find_by_name(db, world.b.id, name) is None
    barcode = "4600000000001"
    found = crud_personal_food.get_by_barcode(db, barcode=barcode, user_id=world.a.id)
    assert found.id == world.soup
    assert (
        crud_personal_food.get_by_barcode(db, barcode=barcode, user_id=world.b.id)
        is None
    )
    live = crud_personal_food.list_live(db, world.b.id)
    assert [dish.id for dish in live] == [world.borscht]


def test_the_free_match_never_gives_b_a_dish_of_as(db, world):
    name = f"{MARK} суп"
    assert [d.id for d in meal_items.exact_match(db, world.a.id, name)] == [world.soup]
    assert meal_items.exact_match(db, world.b.id, name) == []
    both = f"{name}, {MARK} каша"
    assert len(meal_items.exact_match(db, world.a.id, both)) == 2
    assert meal_items.exact_match(db, world.b.id, both) == []


def test_typing_as_dish_goes_to_bs_own_row_with_none_of_as_numbers(db, world, model):
    """Through the bot: A's name costs A no model and B a read of B's own."""
    wire = _say(B, f"{MARK} суп")
    assert model.text, "known positive: B was read, not matched"
    db.expire_all()
    meals = db.scalars(
        select(Meal).where(Meal.user_id == world.b.id, Meal.id != world.b_meal)
    ).all()
    assert len(meals) == 1
    assert f"{A_KCAL}" not in " ".join(b for _n, b in _shown_to(wire))
    for item in crud_meal_item.live(db, meals[0].id):
        assert item.personal_food_id not in (world.soup, world.porridge)
        food = db.get(PersonalFood, item.personal_food_id)
        assert food is None or food.user_id == world.b.id

    model.text.clear()
    _say(A, f"{MARK} суп")
    assert not model.text  # the known positive: for A it is the free match


async def _fake_embedding(_text):
    return [0.25] * 1536


def _rag(db, telegram_id: int, monkeypatch):
    service = SimpleNamespace(embed_text=_fake_embedding)
    monkeypatch.setattr(
        "app.services.product_lookup_service.get_openai_service", lambda: service
    )
    signals = ImageSignals(
        image_data_url="",
        barcode=None,
        vision_result={"foods": [f"{MARK} суп"]},
        portion_grams=100.0,
        telegram_id=telegram_id,
    )
    return asyncio.run(SavedFoodRAGStrategy().resolve(signals, db))


def test_the_rag_of_b_never_reaches_as_dishes(db, world, monkeypatch):
    """The real ``<=>`` on Postgres. SQLite has no such operator, so there the seam is
    patched and the person handed to it is what is asserted, beside the compiled SQL in
    test_crud_personal_food."""
    soup = db.get(PersonalFood, world.soup)
    if not PG_URL:
        people = []

        def similar(_db, *, embedding, threshold, user_id):
            people.append(user_id)
            return None

        monkeypatch.setattr(crud_personal_food, "find_similar", similar)
        assert _rag(db, B, monkeypatch) is None
        assert people == [world.b.id]
        return
    db.add(
        PersonalFoodEmbedding(
            personal_food_id=soup.id,
            text_embedded=soup.canonical_name,
            embedding=[0.25] * 1536,
        )
    )
    db.commit()
    vector = [0.25] * 1536
    found = crud_personal_food.find_similar(
        db, embedding=vector, threshold=0.15, user_id=world.a.id
    )
    assert found and found[0].id == soup.id  # the known positive
    assert (
        crud_personal_food.find_similar(
            db, embedding=vector, threshold=0.15, user_id=world.b.id
        )
        is None
    )
    assert _rag(db, A, monkeypatch) is not None
    assert _rag(db, B, monkeypatch) is None


# --- the export -----------------------------------------------------------------------


def test_the_export_with_both_people_holds_only_the_admins(db, world, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", "t-api")
    monkeypatch.setattr(settings, "EXPORT_API_TOKEN", "t-export")
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app, base_url="http://localhost")
        answer = client.get(
            "/api/v1/meals/export",
            headers={"X-API-Token": "t-api", "X-Export-Token": "t-export"},
        )
    finally:
        app.dependency_overrides.clear()
    assert answer.status_code == 200, answer.text
    ids = {row["id"] for row in answer.json()["meals"]}
    assert world.lunch in ids  # the known positive: the admin's are there
    assert world.b_meal not in ids
    owners = set(db.scalars(select(Meal.user_id).where(Meal.id.in_(ids))))
    assert owners == {world.a.id}


# --- one person's unique name, on the key itself (10.3, H7) ---------------------------

# Past Cyrillic, Python and Postgres lower some letters apart: only Postgres folds those.
FOLDED = [("омлет", "ОМЛЕТ")] + ([("ασ", "ΑΣ"), ("i", "İ")] if PG_URL else [])


@pytest.mark.parametrize("state", ["live", "hidden"])
@pytest.mark.parametrize("held, typed", FOLDED)
def test_a_taken_name_offers_a_merge_where_the_key_would_refuse_it(
    db, world, held, typed, state
):
    """SQLite has no unique index on the lowered name, so only a run on Postgres means
    anything here, and the insert below shows the index is what the answer avoids."""
    columns = {"hidden_at": datetime.now(UTC)} if state == "hidden" else {}
    holder = _dish(db, world.a, held, **columns)
    name = db.get(PersonalFood, world.soup).canonical_name
    if PG_URL:
        with pytest.raises(IntegrityError):
            _dish(db, world.a, typed)
        db.rollback()
    _tap(A, f"dh:rn:{world.soup}")
    shown = " ".join(body for _n, body in _shown_to(_say(A, typed)))
    assert "Такое блюдо уже есть" in shown, shown
    db.expire_all()
    assert db.get(PersonalFood, world.soup).canonical_name == name


@pytest.mark.parametrize("held, typed", FOLDED)
def test_a_name_the_key_folds_together_is_one_dish_by_every_way_in(
    db, world, model, held, typed
):
    holder = _dish(db, world.a, held)
    assert crud_personal_food.find_by_name(db, world.a.id, typed).id == holder.id
    meals = db.scalars(select(Meal.id).where(Meal.user_id == world.a.id)).all()
    shown = " ".join(body for _n, body in _shown_to(_say(A, typed)))
    assert "Не получилось" not in shown, shown
    db.expire_all()
    now = db.scalars(select(Meal.id).where(Meal.user_id == world.a.id)).all()
    assert len(now) == len(meals) + 1  # the known positive: the meal did land
    names = db.scalars(
        select(PersonalFood.canonical_name).where(PersonalFood.user_id == world.a.id)
    ).all()
    assert held in names and typed not in names
