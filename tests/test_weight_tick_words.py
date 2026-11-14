"""D2: weight and routine ticks as plain words, no model («вес 72,4», «отметь зарядку»).

Real in-memory database; the router and the voice line are called as the bot calls
them. «вчера» is read against the real clock, so every check is relative to it.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models import BodyMetric, Meal
from app.models.routine_tick import RoutineTick
from app.models.user import User
from app.services import capture, name_match, routines, stats_screen
from app.services import telegram as tg
from app.services import weight_screen
from app.services.settings_screen import user_zone

TG = 42
ZONE = "Asia/Tbilisi"


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(TG))
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", ZONE)
    with factory() as db:
        db.add(User(telegram_id=TG, username="u"))
        db.commit()
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db(Session):
    with Session() as session:
        yield session


def _routine(db, name: str, rid: str, **fields) -> dict:
    user = db.query(User).first()
    routine = {
        "id": rid,
        "name": name,
        "icon": "💊",
        "schedule": routines.DAILY,
        "days": [],
        **fields,
    }
    routines._save(db, TG, [*routines.stored(user), routine])
    return routine


def _today() -> date:
    return datetime.now(UTC).astimezone(user_zone(None)).date()


def _ticks(db) -> list[tuple]:
    db.expire_all()
    return [(t.routine_id, t.day) for t in db.scalars(select(RoutineTick)).all()]


def _came(text: str) -> capture.Incoming:
    return capture.Incoming(
        telegram_id=TG,
        username="u",
        chat_id=TG,
        message_id=1,
        kind=capture.TEXT,
        text=text,
        file_id=None,
        file_unique_id=None,
        file_size=None,
        duration_s=None,
        media_group_id=None,
        reply_to=None,
        sent_at=datetime.now(UTC),
        forward_date=None,
    )


def _route(db, text: str) -> capture.Route:
    return capture.route_text(db, _came(text))


def _voice(db, route, words: str) -> capture.Line:
    return capture._routed_line(db, SimpleNamespace(telegram_id=TG), None, route, words)


def _weights(db) -> list[BodyMetric]:
    db.expire_all()
    return list(db.scalars(select(BodyMetric)).all())


def _meals(db) -> list[Meal]:
    db.expire_all()
    return list(db.scalars(select(Meal)).all())


# --- weight -----------------------------------------------------------------------

TODAY_WEIGHTS = [
    "вес 72,4",
    "Вес 72.4 кг",
    "вешу 72,4",
    "вешу 72,4 кг.",
    "вес 72,4 кг!",
]
YESTERDAY_WEIGHTS = [
    "вчера вес 72,4",
    "вес вчера 72,4",
    "вчера вешу 72,4 кг",
    "Вес 72,4 вчера",
    "вешу вчера 72.4 кг",
]


@pytest.mark.parametrize("words", TODAY_WEIGHTS)
def test_a_weight_phrase_is_saved_now(db, words):
    assert _route(db, words).action == capture.WEIGHT
    screen = weight_screen.typed(db, TG, words)
    (metric,) = _weights(db)
    assert metric.weight == pytest.approx(72.4)
    assert abs(metric.timestamp.replace(tzinfo=UTC) - datetime.now(UTC)) < timedelta(
        minutes=1
    )
    assert screen.text == "✅ <b>Записано:</b> 72,4 кг"
    buttons = [b.callback_data for r in screen.markup.inline_keyboard for b in r]
    assert f"wt:u:{metric.id}" in buttons


@pytest.mark.parametrize("words", YESTERDAY_WEIGHTS)
def test_a_yesterday_weight_phrase_is_saved_a_day_back_and_says_so(db, words):
    assert _route(db, words).action == capture.WEIGHT
    screen = weight_screen.typed(db, TG, words)
    (metric,) = _weights(db)
    back = datetime.now(UTC) - metric.timestamp.replace(tzinfo=UTC)
    assert timedelta(hours=23, minutes=59) < back < timedelta(days=1, minutes=1)
    assert screen.text == "✅ <b>Записано:</b> 72,4 кг · вчера"


def test_a_spoken_weight_phrase_goes_the_same_way(db):
    route = _route(db, "вчера вес 72,4")
    line = _voice(db, route, "вчера вес 72,4")
    assert line.text == "✅ <b>Записано:</b> 72,4 кг · вчера"
    assert len(_weights(db)) == 1


@pytest.mark.parametrize(
    "words", ["вес 7,24", "вчера вес 301", "вешу 19,9 кг", "вес 1000", "вес 300,5"]
)
def test_an_out_of_range_weight_is_refused_with_the_persons_words(db, words):
    route = _route(db, words)
    assert route.action == capture.LINE
    assert route.line.text == f"✖ Не вышло: «{words}» — вес записываю от 20 до 300 кг."
    assert _voice(db, route, words).text == route.line.text
    assert _weights(db) == []
    assert _meals(db) == []


def test_the_refusal_escapes_the_words():
    assert weight_screen.refusal("вес 7 <i>&") == (
        "✖ Не вышло: «вес 7 &lt;i&gt;&amp;» — вес записываю от 20 до 300 кг."
    )


def test_the_phrase_range_is_300_and_a_bare_number_keeps_400():
    assert weight_screen.parse_words("вес 300") == (300.0, False)
    assert weight_screen.parse_words("вес 20") == (20.0, False)
    assert weight_screen.parse_words("вес 19")[0] is None
    assert weight_screen.parse_kg("350") == 350.0


@pytest.mark.parametrize(
    "words",
    [
        "вес",
        "вес картошки 200",
        "вес 72 г",
        "вес 72,4 кг сегодня",
        "вчера вчера вес 72",
    ],
)
def test_other_words_are_no_weight_phrase(words):
    assert weight_screen.parse_words(words) is None


BASELINE = [
    ("72,4", True, 72.4),
    ("72.4", True, 72.4),
    ("72", True, 72.0),
    ("72 кг", True, 72.0),
    ("72,4 кг", True, 72.4),
    ("72,4кг", True, 72.4),
    ("72,4 kg", True, 72.4),
    ("72.", True, 72.0),
    ("1", False, None),
    ("19", True, None),
    ("20", True, 20.0),
    ("20 кг", True, 20.0),
    ("300", True, 300.0),
    ("400", True, 400.0),
    ("401", True, None),
    ("1000", False, None),
    ("72,456", False, None),
    ("  72,4  ", True, 72.4),
    ("72,4 КГ", True, 72.4),
    ("072", True, 72.0),
    ("7,24", False, None),
    ("вес 72,4", False, None),
    ("вешу 72", False, None),
    ("яйца 2 шт", False, None),
    ("кефир 200 мл", False, None),
    ("200 мл", False, None),
    ("2 яйца", False, None),
    ("суп 300", False, None),
    ("300 г", False, None),
    ("100", True, 100.0),
    ("150 г", False, None),
    ("12:30", False, None),
    ("13.30", True, None),
    ("2026", False, None),
    ("99 кг", True, 99.0),
    ("99,9", True, 99.9),
    ("5", False, None),
    ("50 штук", False, None),
    ("овсянка 80", False, None),
    ("80 овсянка", False, None),
    ("вчера 72", False, None),
    ("72 вчера", False, None),
    ("72,4 кг.", True, 72.4),
    ("72,4.", True, 72.4),
    ("", False, None),
    ("-72", False, None),
    ("72,4,5", False, None),
    ("3 яйца 200", False, None),
]


@pytest.mark.parametrize("text,matches,kg", BASELINE)
def test_a_bare_number_and_food_with_numbers_answer_as_before(text, matches, kg):
    """Recorded from the code before D2: the guard in food_lists relies on it."""
    assert bool(weight_screen.KG_PATTERN.match(text)) is matches
    assert weight_screen.parse_kg(text) == kg


@pytest.mark.parametrize("text", ["72,4", "72,4 кг"])
def test_a_bare_weight_still_routes_and_saves_now(db, text):
    assert _route(db, text).action == capture.WEIGHT
    assert weight_screen.typed(db, TG, text).text == "✅ <b>Записано:</b> 72,4 кг"


@pytest.mark.parametrize("text", ["яйца 2 шт", "кефир 200 мл", "суп 300"])
def test_food_with_numbers_is_not_a_weight(db, text):
    assert _route(db, text).action != capture.WEIGHT


# --- ticks ------------------------------------------------------------------------

TICKS = [
    "отметь зарядку",
    "Отметь зарядка",
    "зарядка сделана",
    "зарядка сделан",
    "зарядка сделано",
    "сделал зарядку",
    "сделала зарядку",
    "сделали зарядку",
    "зарядку сделал",
    "зарядку сделала.",
    "зарядку сделали!",
]


@pytest.mark.parametrize("words", TICKS)
def test_a_tick_phrase_ticks_today(db, words):
    _routine(db, "Зарядка", "z1")
    route = _route(db, words)
    assert (route.action, route.tick) == (capture.TICK, "Зарядка")
    assert _ticks(db) == [("z1", _today())]


@pytest.mark.parametrize(
    "words",
    ["вчера " + t for t in TICKS] + [t.rstrip(".!") + " вчера" for t in TICKS],
)
def test_a_tick_phrase_with_yesterday_ticks_yesterday_and_says_so(db, words):
    _routine(db, "Зарядка", "z1")
    route = _route(db, words)
    assert (route.action, route.tick) == (capture.TICK, "Зарядка — вчера")
    assert _ticks(db) == [("z1", _today() - timedelta(days=1))]


def test_yesterday_may_stand_first_or_in_the_middle(db):
    _routine(db, "Зарядка", "z1")
    assert _route(db, "вчера отметь зарядку").tick == "Зарядка — вчера"
    assert _route(db, "отметь вчера зарядку").tick == "Зарядка — вчера"
    assert _ticks(db) == [("z1", _today() - timedelta(days=1))]


def test_a_spoken_tick_says_the_day_and_is_escaped(db):
    _routine(db, "Витамины & омега", "v1")
    route = _route(db, "витамины омега сделаны вчера")
    assert route.action == capture.TICK
    assert _voice(db, route, "x").text == "✓ Витамины &amp; омега — вчера"


def test_the_exact_name_on_its_own_still_ticks_as_before(db):
    _routine(db, "Зарядка", "z1")
    route = _route(db, " зарядка ")
    assert (route.action, route.tick) == (capture.TICK, "Зарядка")


def test_the_stem_matches_a_longer_routine_name(db):
    _routine(db, "Утренняя зарядка", "z1")
    assert _route(db, "зарядку сделала").tick == "Утренняя зарядка"


def test_exact_name_wins_over_a_stem_match(db):
    _routine(db, "Зарядка", "z1")
    _routine(db, "Зарядка для спины", "z2")
    assert _route(db, "отметь зарядка").tick == "Зарядка"
    assert _ticks(db) == [("z1", _today())]


def test_two_candidates_tick_nothing_and_name_both(db):
    _routine(db, "Зарядка утром", "z1")
    _routine(db, "Зарядка вечером", "z2")
    for words in ("отметь зарядку", "зарядку сделала"):
        route = _route(db, words)
        assert route.action == capture.LINE
        assert route.line.text == (
            "✖ Не вышло: «зарядку» — так называются несколько рутин: "
            "Зарядка утром, Зарядка вечером. Напиши точнее."
        )
    assert _ticks(db) == []


def test_off_the_card_after_the_verb_is_a_refusal_never_a_meal(db):
    _routine(db, "Зарядка", "z1")
    route = _route(db, "отметь медитация")
    assert route.action == capture.LINE
    assert route.line.text == (
        "✖ Не вышло: «медитация» — этой рутины сегодня нет на карточке."
    )
    assert _voice(db, route, "x").text == route.line.text
    yesterday = _route(db, "отметь медитация вчера")
    assert yesterday.line.text == (
        "✖ Не вышло: «медитация» — этой рутины вчера нет на карточке."
    )
    assert _ticks(db) == []
    assert _meals(db) == []


def test_a_routine_not_due_that_day_is_off_the_card(db):
    other = (_today() + timedelta(days=1)).weekday()
    _routine(db, "Бассейн", "p1", schedule=routines.DAYS, days=[other])
    route = _route(db, "отметь бассейн")
    assert route.action == capture.LINE
    assert "сегодня нет на карточке" in route.line.text
    assert _ticks(db) == []
    # ...and after «сделала» it is no refusal: it may be food.
    assert _route(db, "бассейн сделан").action not in (capture.LINE, capture.TICK)


@pytest.mark.parametrize(
    "words", ["сделала омлет", "омлет сделан", "сделали омлет вчера", "суп сделал"]
)
def test_made_food_is_never_refused_it_falls_through(db, words):
    _routine(db, "Зарядка", "z1")
    route = _route(db, words)
    assert route.action not in (capture.LINE, capture.TICK, capture.WEIGHT)
    assert _ticks(db) == []


def test_an_empty_card_never_refuses_food_but_the_verb_still_does(db):
    assert _route(db, "сделала омлет").action != capture.LINE
    assert _route(db, "отметь омлет").action == capture.LINE


@pytest.mark.parametrize("words", ["отметь", "сделала", "вчера отметь", "отметь вчера"])
def test_the_verb_alone_is_no_tick(db, words):
    _routine(db, "Зарядка", "z1")
    assert stats_screen.tick_by_words(db, TG, words) is None


# --- the stem rule ------------------------------------------------------------------


def test_the_stem_rule_moved_to_name_match_and_plan_service_still_uses_it():
    from app.services import plan_service

    assert plan_service._akin is name_match.akin
    assert name_match.akin("гречку", "гречка")
    assert not name_match.akin("чай", "чаю")
    assert name_match.words("Ёлка, 2 шт") == ["елка", "шт"]
