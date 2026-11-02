"""The owner's screens: /users, a person's card, 💬 Фидбэк. Through the real application.

Telegram is a fake wire and the model a fake client under the real service, so the sums
below are the ones a paid call leaves behind. What a real phone does with a re-sent file
no test here can see.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.access_control as access_control
import app.services.action_journal as action_journal
import app.services.ai_call_log_service as log_service
import app.services.inbound_message_service as inbound_service
import app.services.model_selection as model_selection
import app.services.telegram as tg
import app.services.usage_ledger as usage_ledger
from app.core.config import settings
from app.crud.crud_access_grant import crud_access_grant
from app.crud.crud_action_event import crud_action_event
from app.crud.crud_feedback import crud_feedback
from app.crud.crud_subscription import crud_subscription
from app.crud.crud_usage_ledger import crud_usage_ledger
from app.db.base import Base
from app.models import BodyMetric, Meal, User
from app.models.usage_ledger import NO_PERSON
from app.services import people_screen
from app.services.attribution import attribute_to
from tests.test_invite import (
    LISTED,
    MIKA,
    OWNER,
    SASHA,
    ZHENYA,
    Phone,
    _fits,
    _invite,
    _labels,
    _markup,
    play,
    say,
    tap,
)
from tests.test_usage_ledger import fake  # noqa: F401 (the fixture)

MARK = "зюзя"  # a food name: in a meal and a was-food message, never on these screens
TODAY = date.today


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (
        tg,
        inbound_service,
        log_service,
        model_selection,
        access_control,
        usage_ledger,
        action_journal,
    ):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(OWNER))
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", str(LISTED))
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    monkeypatch.setattr(tg, "_schedule_personal_food_save", lambda **_: None)
    return factory


@pytest.fixture
def db(Session):
    session = Session()
    yield session
    session.close()


def zone() -> ZoneInfo:
    return ZoneInfo(settings.DEFAULT_TIMEZONE)


def now() -> datetime:
    return datetime.now(UTC)


def local_day(days_ago: int = 0) -> date:
    return now().astimezone(zone()).date() - timedelta(days=days_ago)


def noon(days_ago: int = 0) -> datetime:
    """Noon in the zone, so that a day ago is that day at whatever hour this runs."""
    moment = datetime.combine(local_day(days_ago), datetime.min.time(), tzinfo=zone())
    return (moment + timedelta(hours=12)).astimezone(UTC)


def ledger(db, person: int, usd: float | None, days_ago: int = 0, calls: int = 1):
    for _ in range(calls):
        crud_usage_ledger.add(
            db, telegram_id=person, day=local_day(days_ago), cost_usd=usd
        )


def act(db, person: int, kind: str, at: datetime, label: str | None = None) -> None:
    crud_action_event.add(db, telegram_id=person, kind=kind, label=label, at=at)
    db.commit()


def opened(db, person: int) -> None:
    crud_access_grant.mark_opened(db, person)


def feedback(db, person: int = SASHA, words: str | None = "Где итог?", **fields):
    extra = {"kind": "text", "original": {"message_id": 1}, **fields}
    return crud_feedback.create(db, telegram_id=person, text=words, **extra)


def sent(db, row) -> None:
    crud_feedback.mark_sent(db, row.id)


def screen_after(*updates, refuses=()) -> dict:
    """The last screen shown: an edit, or the answer to /users."""
    wire = play(*updates, refuses=refuses)
    edits = wire.edits()
    return edits[-1] if edits else wire.to(OWNER)[-1]


def hub(db=None) -> dict:
    return screen_after(say(OWNER, "/users"))


def card(person: int) -> dict:
    return screen_after(tap(OWNER, f"usr:p:{person}"))


def _datas(params: dict) -> list:
    return [
        [b["callback_data"] for b in row] for row in _markup(params)["inline_keyboard"]
    ]


# --- who may see this --------------------------------------------------------------------


def test_the_command_is_in_each_admins_menu_and_nobody_elses(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1001,1002")
    wire = Phone()

    async def go():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await tg._set_commands(application)
        await application.shutdown()

    import asyncio

    asyncio.run(go())
    sent_menus = [p for n, p in wire.calls if n == "setMyCommands"]

    def names(params):
        raw = params["commands"]
        commands = json.loads(raw) if isinstance(raw, str) else raw
        return [c["command"] if isinstance(c, dict) else c.command for c in commands]

    def scope(params):
        raw = params.get("scope")
        return json.loads(raw) if isinstance(raw, str) else raw

    assert names(sent_menus[0]) == ["start", "feedback"]
    assert scope(sent_menus[0]) is None
    assert [names(p) for p in sent_menus[1:]] == [
        ["start", "feedback", "invite", "users"]
    ] * 2
    assert [scope(p) for p in sent_menus[1:]] == [
        {"type": "chat", "chat_id": 1001},
        {"type": "chat", "chat_id": 1002},
    ]


def test_one_admins_refused_menu_leaves_the_other_and_says_so(monkeypatch, caplog):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1001,1002")
    calls = []

    class Bot:
        async def set_my_commands(self, commands, scope=None):
            calls.append(scope)
            if scope is not None and scope.chat_id == 1001:
                raise RuntimeError("no")

    class App:
        bot = Bot()

    import asyncio

    asyncio.run(tg._set_commands(App()))
    assert [s.chat_id for s in calls[1:]] == [1001, 1002]
    assert "Owner's command menu not set" in caplog.text


def test_someone_else_gets_no_answer_to_the_command(db):
    _invite(db)
    crud_subscription.create_subscription(db, LISTED, OWNER, months=1)
    wire = play(say(LISTED, "/users"), say(SASHA, "/users"))
    assert wire.to(LISTED) == [] and wire.to(SASHA) == []
    assert db.scalars(select(Meal.id)).all() == []  # and it did not become food
    # The known positive: the same command, from the owner.
    assert "<b>👥 Пользователи</b>" in wire_to_owner(say(OWNER, "/users"))


def wire_to_owner(*updates) -> str:
    return play(*updates).to(OWNER)[-1]["text"]


@pytest.mark.parametrize(
    "data",
    [
        "usr:l:0",
        f"usr:p:{SASHA}",
        f"usr:c:{SASHA}",
        f"usr:x:{SASHA}",
        f"usr:u:{SASHA}",
        "usr:f:0",
        f"usr:fp:{SASHA}:0",
        "usr:fr:1:0:0",
        "usr:ff:1:0:0",
        "usr:inv",
        "usr:zzz",
    ],
)
def test_a_tap_by_someone_else_does_nothing_and_shows_nothing(db, caplog, data):
    _invite(db)
    row = feedback(db, words="секретные слова", kind="voice", file_id="sasha-file")
    assert row.id == 1
    crud_subscription.create_subscription(db, LISTED, OWNER, months=1)
    wire = play(tap(LISTED, data))
    names = [n for n, _p in wire.calls if n != "getMe"]
    assert names == ["answerCallbackQuery"]
    told = [p for n, p in wire.calls if n == "answerCallbackQuery"][0]
    assert told.get("text") == people_screen.NOT_YOURS
    assert "unknown or foreign" in caplog.text
    db.expire_all()
    assert crud_access_grant.is_active(db, SASHA) is True
    row = crud_feedback.get(db, row.id)
    assert row.read_at is None and row.sent_at is None
    # The known positive, for the screens that do show something: the owner's same tap.
    if data != "usr:inv":
        assert play(tap(OWNER, data)).calls


# --- the list ----------------------------------------------------------------------------


def test_the_list_says_who_how_often_and_what_it_cost(db):
    _invite(db, SASHA, "Саша")
    _invite(db, ZHENYA, "Женя")
    _invite(db, MIKA, "Мика")
    opened(db, SASHA), opened(db, ZHENYA)
    today_at = noon().replace(hour=10, minute=6)
    act(db, SASHA, "photo", today_at)
    for ago in (1, 2, 4, 5):
        act(db, SASHA, "text", noon(ago))
    act(db, ZHENYA, "text", noon(1))
    act(db, ZHENYA, "voice", noon(3))
    ledger(db, SASHA, 0.38)
    ledger(db, ZHENYA, 0.09, days_ago=2)
    feedback(db), feedback(db, ZHENYA)

    shown = hub()
    lines = shown["text"].split("\n")
    assert lines[0] == "<b>👥 Пользователи</b>"
    assert lines[1] == "За 30 дней: 💸 $0.47"
    at = today_at.astimezone(zone())
    yesterday = noon(1).astimezone(zone())
    assert f"Саша · сегодня, {at:%H:%M} · 5 из 7 дней · $0.38" in lines
    assert f"Женя · вчера, {yesterday:%H:%M} · 2 из 7 дней · $0.09" in lines
    assert "Мика · ⏳ бот ещё не открыт" in lines
    # The owner's own /users is an action too, so the owner's place moves with the clock.
    labels = _labels(shown)
    assert sorted(labels[0] + labels[1]) == sorted(["Саша", "Женя", "Мика", str(OWNER)])
    assert labels[2:] == [["💬 Фидбэк: 2 новых"], ["➕ Пригласить"]]
    _fits(shown)
    assert _datas(shown)[-1] == ["usr:inv"]


def test_the_latest_comes_first_and_those_who_never_came_last(db):
    _invite(db, MIKA, "Мика")
    _invite(db, SASHA, "Саша")
    _invite(db, ZHENYA, "Женя")
    act(db, ZHENYA, "text", noon(1))
    act(db, SASHA, "text", noon())
    built = people_screen.hub(db)  # no /users of the owner's in the journal this way
    labels = [b.text for row in built.markup.inline_keyboard for b in row]
    assert labels[:4] == ["Саша", "Женя", "Мика", str(OWNER)]


def test_people_are_the_grants_the_admins_and_listed_people_with_usage(db, monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", f"{LISTED},9001,9002,9003")
    db.add(User(telegram_id=9001, username="listed_one"))
    db.commit()
    act(db, 9001, "text", noon())  # by the journal
    ledger(db, 9002, 0.01)  # by the ledger, with no name anywhere
    # 9003 listed, and has done nothing: not a line here.
    shown = hub()
    assert sorted(_labels(shown)[0]) == sorted(["listed_one", "9002", str(OWNER)])
    text = shown["text"]
    assert "listed_one · сегодня, " in text
    assert "9002 · нет действий · 0 из 7 дней · $0.01" in text
    assert "9001" not in text  # a name exists: the id stays out
    assert "9003" not in text


def test_a_name_on_a_grant_beats_a_username_and_the_id_stays_out(db):
    db.add(User(telegram_id=SASHA, username="sasha_k"))
    db.commit()
    _invite(db, SASHA, "Саша")
    text = hub()["text"]
    assert "Саша · ⏳" in text
    assert str(SASHA) not in text and "sasha_k" not in text


def test_a_name_goes_through_escaping_on_each_screen(db):
    _invite(db, SASHA, "<b>Саша & Ко</b>")
    db.expire_all()
    row = feedback(db, words="а <i>это</i>")
    for shown in (hub(), card(SASHA), screen_after(tap(OWNER, "usr:f:0"))):
        assert "&lt;b&gt;Саша &amp; Ко&lt;/b&gt;" in shown["text"], shown["text"]
    assert "а &lt;i&gt;это&lt;/i&gt;" in screen_after(tap(OWNER, "usr:f:0"))["text"]
    assert row.id


def test_a_person_who_wrote_with_no_start_is_not_called_unopened(db):
    """first_opened_at is set on /start only, and this person did use the bot."""
    _invite(db)
    act(db, SASHA, "text", noon())
    shown = hub()
    assert "бот ещё не открыт" not in shown["text"]
    assert "Саша · сегодня, " in shown["text"]


def test_someone_closed_is_marked_and_keeps_their_numbers(db):
    _invite(db, SASHA, "Саша")
    _invite(db, MIKA, "Мика")
    ledger(db, SASHA, 0.12)
    opened(db, SASHA)
    play(tap(OWNER, f"usr:x:{SASHA}"), tap(OWNER, f"usr:x:{MIKA}"))
    text = hub()["text"]
    assert "Саша · ⛔ доступ закрыт · нет действий · 0 из 7 дней · $0.12" in text
    assert "Мика · ⛔ доступ закрыт\n" in text + "\n"


def test_the_sum_with_no_person_has_its_line_only_when_there_is_one(db):
    _invite(db)
    assert "Без человека" not in hub()["text"]
    ledger(db, NO_PERSON, 0.05)
    ledger(db, SASHA, 0.10)
    text = hub()["text"]
    assert "За 30 дней: 💸 $0.15" in text
    assert "Без человека: $0.05" in text
    # Not a person: no line of its own and no button.
    assert not any(line.startswith("0 · ") for line in text.split("\n"))
    assert ["0"] not in _labels(hub())


def test_no_person_with_only_unpriced_calls_still_shows(db):
    ledger(db, NO_PERSON, None)
    assert "Без человека: $0.00" in hub()["text"]


def test_money_from_people_off_the_list_is_said_so_the_sum_adds_up(db):
    _invite(db)
    ledger(db, SASHA, 0.10)
    ledger(db, 9999, 0.20)  # out of every list now
    text = hub()["text"]
    assert "За 30 дней: 💸 $0.30" in text
    assert "Вне списка: $0.20" in text
    assert "9999" not in text


def test_unpriced_calls_are_named_and_only_when_there_are_some(db):
    _invite(db)
    ledger(db, SASHA, 0.10)
    assert "без известной цены" not in hub()["text"]
    ledger(db, SASHA, None, calls=3)
    assert "⚠️ Запросов без известной цены: 3. В суммах их нет." in hub()["text"]


def test_thirty_days_means_the_ledgers_thirty_days(db):
    _invite(db)
    ledger(db, SASHA, 0.10, days_ago=29)
    ledger(db, SASHA, 7.00, days_ago=30)
    text = hub()["text"]
    assert "За 30 дней: 💸 $0.10" in text
    assert "💸 За всё время: $7.10" in card(SASHA)["text"]


@pytest.mark.parametrize(
    "usd, words",
    [
        (0, "$0.00"),
        (0.004, "&lt;$0.01"),
        (0.005, "$0.01"),
        (0.38, "$0.38"),
        (12.345, "$12.35"),
    ],
)
def test_money_looks_like_the_design(usd, words):
    assert people_screen.money(Decimal(str(usd))) == words


def test_the_list_pages_within_the_limits(db):
    for n in range(14):
        _invite(db, 5000 + n, f"Человек с длинным именем {n}")
    first = hub()
    wire = play(tap(OWNER, "usr:l:1"), tap(OWNER, "usr:l:99"))
    second, clamped = wire.edits()
    for shown in (first, second, clamped):
        _fits(shown)
    assert _labels(first)[2] == ["‹", "1/3", "›"]
    assert _labels(second)[2] == ["‹", "2/3", "›"]
    assert _datas(second)[2] == ["usr:l:0", "usr:l:1", "usr:l:2"]
    assert _labels(clamped)[1] == [
        "‹",
        "3/3",
        "›",
    ]  # three people left: one row of them
    assert _labels(first)[-1] == ["➕ Пригласить"]
    assert all(len(label) <= 12 for row in _labels(first)[:2] for label in row)
    assert "Человек с длинным именем 0 " in first["text"]
    assert "Человек с длинным именем 0 " not in second["text"]


def test_no_feedback_no_row_and_one_new_is_singular(db):
    _invite(db)
    assert not any("Фидбэк" in row[0] for row in _labels(hub()))
    row = feedback(db)
    assert ["💬 Фидбэк: 1 новый"] in _labels(hub())
    play(tap(OWNER, "usr:f:0"))
    assert ["💬 Фидбэк"] in _labels(hub())
    assert row.id


def test_the_list_says_when_feedback_did_not_reach_the_owner(db):
    _invite(db)
    sent(db, feedback(db))
    assert "не доставлен" not in hub()["text"]
    feedback(db)
    assert "⚠️ Фидбэк не доставлен: 1." in hub()["text"]


def test_invite_starts_the_same_flow(db):
    wire = play(tap(OWNER, "usr:inv"))
    assert wire.to(OWNER)[0]["text"] == people_screen.PROMPT


@pytest.mark.parametrize(
    "data",
    [
        "usr:zzz",
        "usr:",
        "usr:p:",
        "usr:p:abc",
        "usr:p:" + "9" * 30,  # past what a column holds
        "usr:p:424242",  # nobody
        "usr:x:424242",
        "usr:u:424242",
        "usr:fp:424242:0",
        "usr:fr:424242:0:0",
        "usr:ff:424242:0:0",
        "usr:fr:" + "9" * 30 + ":0:0",
        "usr:l:abc",
    ],
)
def test_stale_or_malformed_data_leads_to_the_list_with_a_notice(db, data):
    shown = play(tap(OWNER, data)).edits()[0]
    assert shown["text"].startswith(f"{people_screen.STALE}\n\n<b>👥 Пользователи</b>")


# --- the card ----------------------------------------------------------------------------


def _sub_end(db):
    return crud_subscription.get_by_telegram_id(db, SASHA).end_date


def test_the_card_as_in_the_design(db):
    db.add(User(telegram_id=SASHA, username="old_name"))
    db.commit()
    crud_subscription.create_subscription(db, SASHA, OWNER, months=1)
    crud_access_grant.grant(db, SASHA, "Саша", "sasha_k", granted_by=OWNER)
    opened(db, SASHA)
    # Five of the seven days: not 2 days ago, and not 6.
    for ago in (0, 1, 3, 4, 5):
        act(db, SASHA, "photo", noon(ago))
    for _ in range(3):
        act(db, SASHA, "text", noon(10))
    act(db, SASHA, "voice", noon(10))
    act(db, SASHA, "corrected", noon(10), "grams")
    act(db, SASHA, "corrected", noon(9), "qty")
    act(db, SASHA, "failed", noon(9), "voice")
    act(db, SASHA, "saved", noon(9), "image")  # what came of it: no column of its own
    act(db, SASHA, "photo", noon(45))  # out of the thirty days
    ledger(db, SASHA, 0.19, calls=2)
    ledger(db, SASHA, 1.00, days_ago=40)
    sent(db, feedback(db))

    shown = card(SASHA)
    assert shown["text"].split("\n") == [
        "<b>👤 Саша · @sasha_k</b>",
        f"В боте с {now().astimezone(zone()):%d.%m}, последний раз сегодня "
        f"в {noon().astimezone(zone()):%H:%M}",
        f"🗓 Подписка до {_sub_end(db):%d.%m.%Y}. Новый срок, считая от сегодня:",
        "▱▰▰▰▱▰▰ 5 из 7 дней",
        "За 30 дней: 📷 5 · ✍️ 3 · 🎙 1",
        "✏️ Исправлений: 2 · ⚠️ Сбоев: 1",
        "💸 За 30 дней: $0.38 · 2 запроса",
        "💸 За всё время: $1.38",
        "💬 Фидбэк: 1",
    ]
    assert _labels(shown) == [
        ["🗒 Фидбэк", "⛔ Закрыть доступ"],
        ["3 мес", "6 мес", "12 мес"],
        ["⬅️ Пользователи"],
    ]
    assert _datas(shown) == [
        [f"usr:fp:{SASHA}:0", f"usr:x:{SASHA}"],
        [f"usr:tc:{SASHA}:{months}" for months in (3, 6, 12)],
        ["usr:l:0"],
    ]
    _fits(shown)


def test_a_paid_call_through_the_attribution_reaches_the_card_and_the_list(
    db, fake, monkeypatch  # noqa: F811
):
    """End to end: the fake stands where the SDK's client stands, under the real service."""
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", str(LISTED))
    _invite(db, SASHA, "Саша")
    # gpt-6-luna at 0.10 and 0.50 per 1M: $0.15 a call.
    fake.chat_usd = Decimal("0.15")
    fake.usage = SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=100_000)
    play(say(SASHA, "овсянка"), say(SASHA, "ещё овсянка"))
    assert fake.reached == ["chat", "chat"]
    # A call nobody can be named for, and one with no known price.
    usage_ledger.charge(0.05)
    with attribute_to(SASHA):
        usage_ledger.charge(None)

    text = card(SASHA)["text"]
    assert "💸 За 30 дней: $0.30 · 3 запроса · 1 без цены" in text
    assert "💸 За всё время: $0.30" in text
    assert "За 30 дней: 📷 0 · ✍️ 2 · 🎙 0" in text
    listed = hub()["text"]
    assert "За 30 дней: 💸 $0.35" in listed
    assert "Без человека: $0.05" in listed
    assert "⚠️ Запросов без известной цены: 1. В суммах их нет." in listed
    assert "Саша · сегодня, " in listed and " · 1 из 7 дней · $0.30" in listed


def test_the_strip_uses_the_ledgers_day_not_utc(db):
    """21:30 UTC yesterday is already today in UTC+4; the ledger would book it today too."""
    _invite(db)
    utc_today = now().replace(tzinfo=UTC)
    midnight = datetime.combine(
        local_day(), datetime.min.time(), tzinfo=zone()
    ).astimezone(UTC)
    act(db, SASHA, "text", midnight + timedelta(minutes=30))  # first minutes of today
    act(db, SASHA, "text", midnight - timedelta(days=1, minutes=30))  # 2 days ago, late
    flags = crud_action_event.active_days(db, SASHA, zone=zone(), now=utc_today)
    assert flags == [False, False, False, False, True, False, True]
    assert crud_usage_ledger  # the same day: usage_ledger.today() is in this zone too
    assert usage_ledger.today() == local_day()
    assert "▱▱▱▱▰▱▰ 2 из 7 дней" in card(SASHA)["text"]


def test_a_day_is_cut_at_a_summer_time_edge_by_the_zone(db):
    """A 23-hour day: the edges are the zone's, not 24 hours apart."""
    berlin = ZoneInfo("Europe/Berlin")
    moment = datetime(2026, 3, 30, 10, 0, tzinfo=UTC)  # the day after the clocks moved
    # 23:30 on the 28th in Berlin (UTC+1) and 00:30 on the 29th (UTC+1).
    act(db, SASHA, "text", datetime(2026, 3, 28, 22, 30, tzinfo=UTC))
    flags = crud_action_event.active_days(
        db, SASHA, zone=berlin, days=3, now=moment
    )  # 28, 29, 30
    assert flags == [True, False, False]
    act(
        db, SASHA, "text", datetime(2026, 3, 29, 22, 30, tzinfo=UTC)
    )  # 00:30 on the 30th
    flags = crud_action_event.active_days(db, SASHA, zone=berlin, days=3, now=moment)
    assert flags == [True, False, True]


def test_results_that_came_by_themselves_do_not_count_as_a_day_there(db):
    _invite(db)
    act(db, SASHA, "saved", noon(), "image")
    act(db, SASHA, "failed", noon(1), "voice")
    assert "0 из 7 дней" in card(SASHA)["text"]


def test_someone_in_the_settings_shows_why_there_is_no_close_button(db):
    act(db, LISTED, "text", noon())
    wire = play(tap(OWNER, f"usr:p:{LISTED}"), tap(OWNER, f"usr:x:{LISTED}"))
    shown, refused = wire.edits()
    assert "Доступ задан в настройках сервера, кнопкой его не закрыть." in shown["text"]
    assert _labels(shown) == [["⬅️ Пользователи"]]
    assert "Этот доступ задан в настройках сервера." in refused["text"]
    assert access_control.is_user_allowed(LISTED) is True


def test_an_admin_has_no_close_button_and_cannot_be_closed(db):
    shown = card(OWNER)
    assert _labels(shown) == [["⬅️ Пользователи"]]
    assert "Владелец бота" in shown["text"]
    refused = play(tap(OWNER, f"usr:x:{OWNER}")).edits()[0]
    assert _labels(refused) == [["⬅️ Пользователи"]]
    assert access_control.is_user_allowed(OWNER) is True


def test_closing_applies_at_once_and_offers_undo_and_undo_works(db):
    _invite(db)
    opened(db, SASHA)
    wire = play(tap(OWNER, f"usr:x:{SASHA}"), tap(OWNER, f"usr:u:{SASHA}"))
    closed, back = wire.edits()
    assert closed["text"].startswith("<i>✓ Саша: доступ закрыт. Записи сохранены.</i>")
    assert "⛔ доступ закрыт" in closed["text"]
    assert _labels(closed) == [["↩️ Вернуть"], ["⬅️ Пользователи"]]
    assert back["text"].startswith("<i>✓ Саша: доступ снова открыт.</i>")
    assert _labels(back) == [
        ["⛔ Закрыть доступ"],
        ["3 мес", "6 мес", "12 мес"],
        ["⬅️ Пользователи"],
    ]
    assert access_control.is_user_allowed(SASHA) is True


def test_a_card_not_opened_yet_says_so(db):
    _invite(db)
    text = card(SASHA)["text"]
    assert f"Доступ открыт {now().astimezone(zone()):%d.%m}" in text
    assert "⏳ бот ещё не открыт" in text
    assert "В боте с" not in text


def test_a_person_with_no_grant_is_shown_from_what_is_known(db, monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", f"{LISTED},9002")
    ledger(db, 9002, 0.25)
    shown = card(9002)
    assert shown["text"].startswith("<b>👤 9002</b>")
    assert "💸 За всё время: $0.25" in shown["text"]
    assert _labels(shown) == [["⬅️ Пользователи"]]


def test_a_username_is_not_said_twice(db, monkeypatch):
    db.add(User(telegram_id=LISTED, username="zhenya"))
    db.commit()
    assert card(LISTED)["text"].startswith("<b>👤 zhenya</b>")


def test_someone_off_every_list_with_money_is_shown_and_has_no_buttons_to_close(db):
    ledger(db, 9999, 0.20)
    shown = card(9999)
    assert shown["text"].startswith("<b>👤 9999</b>")
    assert _labels(shown) == [["⬅️ Пользователи"]]


def test_the_card_under_the_owners_copy_opens_as_a_new_message(db):
    """The copy holds the person's words: the card must not write over them."""
    _invite(db)
    wire = play(tap(OWNER, f"usr:c:{SASHA}"))
    assert wire.edits() == []
    assert wire.to(OWNER)[0]["text"].startswith("<b>👤 Саша</b>")
    # Stale there too: the list, in a new message as well.
    wire = play(tap(OWNER, "usr:c:424242"))
    assert wire.edits() == []
    assert people_screen.STALE in wire.to(OWNER)[0]["text"]


# --- 💬 Фидбэк ---------------------------------------------------------------------------


def inbox(page: int = 0) -> dict:
    return screen_after(tap(OWNER, f"usr:f:{page}"))


def test_the_inbox_is_newest_first_paged_and_marks_read_when_opened(db):
    _invite(db, SASHA, "Саша")
    first = feedback(db, words="старое")
    second = feedback(db, words="новое")
    for row in (first, second):
        sent(db, row)
    shown = inbox()
    assert shown["text"].startswith("<b>💬 Фидбэк</b>")
    assert "«новое»" in shown["text"] and "старое" not in shown["text"]
    assert "Новое · Саша · " in shown["text"]
    assert _labels(shown) == [
        ["‹", "1/2", "›"],
        ["👤 Саша"],
        ["⬅️ Пользователи"],
    ]
    _fits(shown)
    db.expire_all()
    assert crud_feedback.get(db, second.id).read_at is not None
    assert crud_feedback.get(db, first.id).read_at is None
    assert crud_feedback.count_new(db) == 1

    other = inbox(1)
    assert "«старое»" in other["text"]
    db.expire_all()
    assert crud_feedback.count_new(db) == 0
    again = inbox(1)
    assert "Новое" not in again["text"]


def test_an_empty_inbox_says_so(db):
    shown = inbox()
    assert "Пока ничего." in shown["text"]
    assert _labels(shown) == [["⬅️ Пользователи"]]


def test_the_transcript_and_the_words_are_both_there(db):
    _invite(db)
    row = feedback(db, words=None, kind="voice", file_id="vf")
    crud_feedback.set_transcript(db, row.id, "слова голосом")
    text = inbox()["text"]
    assert "🎙 голосовое" in text and "«слова голосом»" in text
    photo = feedback(db, words="подпись", kind="photo", file_id="pf")
    text = inbox()["text"]
    assert "📷 фото" in text and "«подпись»" in text
    assert photo.id
    mute = feedback(db, words=None, kind="voice", file_id="vf2")
    assert "Без расшифровки." in inbox()["text"]
    assert mute.id


def test_undelivered_is_visible_and_offers_a_resend_and_delivered_does_not(db):
    _invite(db)
    delivered = feedback(db, words="дошло")
    sent(db, delivered)
    missing = feedback(db, words="не дошло")
    shown = inbox()
    assert "⚠️ Не доставлено: 1" in shown["text"]
    assert "⚠️ Не доставлено тебе." in shown["text"]
    assert ["📤 Отправить ещё раз"] in _labels(shown)
    assert _datas(shown)[1] == [f"usr:fr:{missing.id}:0:0"]
    other = inbox(1)
    assert "⚠️ Не доставлено тебе." not in other["text"]
    assert ["📤 Отправить ещё раз"] not in _labels(other)


def test_a_resend_gives_the_owner_the_whole_copy_and_stamps_it(db):
    _invite(db)
    row = feedback(db, words="не дошло", journal="10:00 ✍️ текст")
    wire = play(tap(OWNER, f"usr:fr:{row.id}:0:0"))
    copy = wire.to(OWNER)[0]
    assert copy["text"].startswith("<b>💬 Фидбэк · Саша · ")
    assert "«не дошло»" in copy["text"] and "10:00 ✍️ текст" in copy["text"]
    shown = wire.edits()[0]
    assert shown["text"].startswith("<i>✓ Отправлено отдельным сообщением.</i>")
    assert "Не доставлено" not in shown["text"]
    assert ["📤 Отправить ещё раз"] not in _labels(shown)
    db.expire_all()
    assert crud_feedback.get(db, row.id).sent_at is not None


class Refusing(Phone):
    def __init__(self, refuse):
        super().__init__()
        self.refuse = refuse

    async def do_request(self, url, method, request_data=None, **kwargs):
        if url.rsplit("/", 1)[-1] in self.refuse:
            self.calls.append(("refused", {}))
            body = {"ok": False, "error_code": 400, "description": "Bad Request: no"}
            return 400, json.dumps(body).encode()
        return await super().do_request(url, method, request_data, **kwargs)


def _play_on(wire, *updates):
    import asyncio

    from telegram import Update

    async def steps():
        application = tg.create_bot_application()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.start()
        try:
            for update in updates:
                await application.process_update(
                    Update.de_json(update, application.bot)
                )
        finally:
            await application.stop()
            await application.shutdown()

    asyncio.run(steps())
    return wire


def test_a_refused_resend_keeps_it_undelivered_and_says_nothing_is_lost(db):
    _invite(db)
    row = feedback(db, words="не дошло", kind="voice", file_id="vf")
    wire = _play_on(Refusing({"sendVoice"}), tap(OWNER, f"usr:fr:{row.id}:0:0"))
    shown = wire.edits()[0]
    assert shown["text"].startswith(f"{people_screen.NOT_SENT}\n\n<b>💬 Фидбэк</b>")
    assert ["📤 Отправить ещё раз"] in _labels(shown)
    db.expire_all()
    assert crud_feedback.get(db, row.id).sent_at is None


@pytest.mark.parametrize(
    "kind, original, label, method, key",
    [
        ("voice", {"voice": {"file_id": "x"}}, "🎙 Голосовое", "sendVoice", "voice"),
        ("photo", {"photo": [{"file_id": "x"}]}, "📷 Фото", "sendPhoto", "photo"),
        (
            "photo",
            {"document": {"file_id": "x"}},
            "📷 Фото",
            "sendDocument",
            "document",
        ),
    ],
)
def test_a_file_is_sent_again_by_its_id_on_a_tap(
    db, kind, original, label, method, key
):
    _invite(db)
    row = feedback(db, words=None, kind=kind, file_id="the-file", original=original)
    sent(db, row)
    shown = inbox()
    assert [label] in _labels(shown)
    wire = play(tap(OWNER, f"usr:ff:{row.id}:0:0"))
    sends = [p for n, p in wire.calls if n == method]
    assert [p[key] for p in sends] == ["the-file"]
    assert int(sends[0]["chat_id"]) == OWNER
    assert wire.to(OWNER) == []  # the file only: the words are already in the chat
    assert wire.edits()[0]["text"].startswith(
        "<i>✓ Отправлено отдельным сообщением.</i>"
    )


def test_an_undelivered_file_goes_with_the_words_and_is_then_delivered(db):
    _invite(db)
    row = feedback(db, words=None, kind="voice", file_id="the-file")
    shown = inbox()
    assert [["📤 Отправить ещё раз"]] == [
        r for r in _labels(shown) if "Отправить" in r[0] or "Голосовое" in r[0]
    ]
    wire = play(tap(OWNER, f"usr:fr:{row.id}:0:0"))
    assert [p["voice"] for n, p in wire.calls if n == "sendVoice"] == ["the-file"]
    assert wire.to(OWNER)[0]["text"].startswith("<b>💬 Фидбэк · Саша · ")
    db.expire_all()
    assert crud_feedback.get(db, row.id).sent_at is not None


def test_a_long_message_is_cut_on_the_screen_and_whole_on_a_tap(db):
    _invite(db)
    row = feedback(db, words="слово " * 2000)
    sent(db, row)
    shown = inbox()
    assert len(shown["text"]) <= 4096
    assert "…»" in shown["text"]
    assert ["📋 Весь текст"] in _labels(shown)
    wire = play(tap(OWNER, f"usr:fr:{row.id}:0:0"))
    words = "".join(p["text"] for p in wire.to(OWNER))
    assert words.replace("«", "").replace("»", "").count("слово") == 2000


def test_a_message_that_was_food_is_hidden_counted_and_not_resent(db):
    _invite(db, SASHA, "Саша")
    keeper = feedback(db, words="Где итог?")
    sent(db, keeper)
    food = feedback(db, words=f"{MARK} и рис", kind="voice", file_id="food-file")
    crud_feedback.mark_food(db, food.id)

    shown = inbox()
    assert MARK not in shown["text"] and "food-file" not in json.dumps(shown)
    assert "«Где итог?»" in shown["text"]
    assert "↩️ Оказалось едой и скрыто: 1" in shown["text"]
    assert "1/2" not in json.dumps(_labels(shown), ensure_ascii=False)
    assert "💬 Фидбэк: 1" in card(SASHA)["text"]  # the one left
    assert crud_feedback.count_undelivered(db) == 0

    for data in (f"usr:fr:{food.id}:0:0", f"usr:ff:{food.id}:0:0"):
        wire = play(tap(OWNER, data))
        assert wire.to(OWNER) == []
        assert not [n for n, _p in wire.calls if n == "sendVoice"]
        assert wire.edits()[0]["text"].startswith(people_screen.STALE)


def test_a_card_with_no_feedback_has_no_feedback_button(db):
    _invite(db)
    shown = card(SASHA)
    assert "💬 Фидбэк: 0" in shown["text"]
    assert _labels(shown) == [
        ["⛔ Закрыть доступ"],
        ["3 мес", "6 мес", "12 мес"],
        ["⬅️ Пользователи"],
    ]


def test_a_persons_feedback_is_theirs_and_goes_back_to_their_card(db):
    _invite(db, SASHA, "Саша")
    _invite(db, ZHENYA, "Женя")
    sent(db, feedback(db, ZHENYA, words="чужое"))
    mine = feedback(db, words="своё")
    sent(db, mine)
    wire = play(tap(OWNER, f"usr:p:{SASHA}"), tap(OWNER, f"usr:fp:{SASHA}:0"))
    shown = wire.edits()[1]
    assert shown["text"].startswith("<b>🗒 Фидбэк · Саша</b>")
    assert "«своё»" in shown["text"] and "чужое" not in shown["text"]
    assert _labels(shown) == [["⬅️ Саша"]]
    assert _datas(shown) == [[f"usr:p:{SASHA}"]]
    db.expire_all()
    assert crud_feedback.get(db, mine.id).read_at is not None
    assert crud_feedback.count_new(db) == 1


def test_a_resend_returns_to_the_same_page_of_the_same_view(db):
    _invite(db)
    target = feedback(db, words="цель")
    sent(db, feedback(db, words="новее"))
    wire = play(tap(OWNER, f"usr:fr:{target.id}:{SASHA}:1"))
    shown = wire.edits()[0]
    assert "<b>🗒 Фидбэк · Саша</b>" in shown["text"] and "«цель»" in shown["text"]
    assert _labels(shown)[0] == ["‹", "2/2", "›"]
    assert _datas(shown)[0] == [
        f"usr:fp:{SASHA}:0",
        f"usr:fp:{SASHA}:1",
        f"usr:fp:{SASHA}:1",
    ]


# --- no records on these screens ---------------------------------------------------------


def test_no_food_kcal_weight_or_assessment_on_any_of_these_screens(db):
    _invite(db, SASHA, "Саша")
    opened(db, SASHA)
    user = db.scalar(select(User).where(User.telegram_id == SASHA))
    db.add(
        Meal(
            user_id=user.id,
            meal_type="snack",
            description=f"{MARK} с рисом",
            calories=613,
            proteins=41,
            fats=17,
            carbohydrates=59,
            meal_time=now(),
        )
    )
    db.add(BodyMetric(user_id=user.id, weight=77.7, timestamp=now()))
    db.commit()
    play(say(SASHA, f"{MARK} с рисом"))  # journal rows from the real path, too
    act(db, SASHA, "saved", noon(), "image")
    act(db, SASHA, "corrected", noon(), "grams")
    sent(db, feedback(db, words="Где итог?"))
    food = feedback(db, words=f"{MARK} и суп")
    crud_feedback.mark_food(db, food.id)

    wire = play(
        say(OWNER, "/users"),
        tap(OWNER, "usr:l:0"),
        tap(OWNER, f"usr:p:{SASHA}"),
        tap(OWNER, f"usr:x:{SASHA}"),
        tap(OWNER, f"usr:u:{SASHA}"),
        tap(OWNER, "usr:f:0"),
        tap(OWNER, f"usr:fp:{SASHA}:0"),
    )
    words = json.dumps(
        [p for n, p in wire.calls if n in ("sendMessage", "editMessageText")],
        ensure_ascii=False,
        default=str,
    ).casefold()
    assert "саша" in words and "где итог" in words  # the known positive
    assert MARK not in words
    for number in ("613", "77.7", "ккал", "кал ", "вес", "белк", "оцен"):
        assert number not in words, number


# --- bounded ---------------------------------------------------------------------------


class Statements:
    def __init__(self, session):
        self.engine = session.get_bind()
        self.count = 0

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._one)
        return self

    def __exit__(self, *_):
        event.remove(self.engine, "before_cursor_execute", self._one)

    def _one(self, *_):
        self.count += 1


def test_the_list_costs_the_same_statements_for_three_people_and_for_forty(db):
    from app.services.people_screen import hub as build

    for n in range(3):
        _invite(db, 5000 + n, f"Человек {n}")
        act(db, 5000 + n, "text", noon())
    db.expire_all()
    with Statements(db) as few:
        build(db)
    for n in range(3, 40):
        _invite(db, 5000 + n, f"Человек {n}")
        for ago in range(5):
            act(db, 5000 + n, "text", noon(ago))
    db.expire_all()
    with Statements(db) as many:
        build(db)
    assert few.count == many.count
    assert few.count < 15


def test_the_card_costs_the_same_with_a_long_journal(db):
    _invite(db)
    db.expire_all()
    with Statements(db) as short:
        people_screen.handle(db, OWNER, f"usr:p:{SASHA}")
    for ago in range(30):
        act(db, SASHA, "text", noon(ago))
    db.expire_all()
    with Statements(db) as long:
        people_screen.handle(db, OWNER, f"usr:p:{SASHA}")
    assert short.count == long.count


def test_the_days_query_returns_days_and_not_rows(db):
    """At most people × days rows come back, however many actions a day has."""
    for _ in range(50):
        act(db, SASHA, "text", noon())
    flags = crud_action_event.active_days_all(db, [SASHA, ZHENYA], zone=zone())
    assert flags == {SASHA: [False] * 6 + [True]}
    seen = []

    def spy(_conn, _cursor, _statement, _params, context, _many):
        seen.append(context)

    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", spy)
    crud_action_event.active_days_all(db, [SASHA], zone=zone())
    event.remove(engine, "after_cursor_execute", spy)
    assert "DISTINCT" in seen[0].statement


def test_the_labels_say_the_journal_knows_this_button():
    assert "usr" in action_journal.BUTTON_NAMES
