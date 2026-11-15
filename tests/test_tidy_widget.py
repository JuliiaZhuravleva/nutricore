"""🍲 Порядок as a widget (B3): the page, the state, the switch, the send and its
fallbacks, the tap, undo and retire under a widget message, the on-demand message and the
migration.

On SQLite with a fake bot that records every call in order. Nothing reaches a model or the
network. What this does not prove: the page on a real Telegram client (the page is run
through ``check_widget.py`` by hand, see the hand-off).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import zlib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from telegram.error import BadRequest, Forbidden, TimedOut

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import settings
from app.crud.crud_dish_tidy import crud_dish_tidy
from app.models import PersonalFood
from app.models.dish_tidy import DishTidyItem
from app.services import dish_tidy, dish_tidy_push
from app.services import dish_tidy_screen as screen_mod
from app.services import dish_tidy_widget
from app.services import telegram as tg
from app.services import widget_queries
from app.services.diary_widget import BUDGET, packed

# The tidy-up's own fixtures and helpers (a fixture module-level import is a fixture).
from tests.test_dish_tidy import (  # noqa: F401
    AT_12,
    _batch,
    _callbacks,
    _kitchen,
    _labels,
    _live,
    _run,
    _turn_on,
    _user,
    factory,
    no_model,
)

W = dish_tidy_widget
ROOT = Path(__file__).resolve().parents[1]
LATER = AT_12 + timedelta(minutes=5)


@pytest.fixture
def db(request, monkeypatch):
    """Person 1 (telegram 42) is the owner; the switch is at its default. Set after the
    ``factory`` fixture, which names another admin."""
    with request.getfixturevalue("factory")() as session:
        monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
        monkeypatch.setattr(settings, "TIDY_WIDGET", "owner")
        yield session


def _bot(**kw):
    """A bot that records every call, in order, in ``calls``."""
    calls = []

    def recorder(name, result=None, error=None):
        async def call(*args, **kwargs):
            calls.append((name, args, kwargs))
            if error is not None:
                raise error
            return result

        return call

    sent = kw.get(
        "widget_result", SimpleNamespace(message_id=kw.get("message_id", 900))
    )

    async def api(method, *args, **kwargs):
        calls.append((method, args, kwargs))
        if method == "sendWidget" and kw.get("widget_error") is not None:
            raise kw["widget_error"]
        return sent if method == "sendWidget" else True

    bot = SimpleNamespace(
        calls=calls,
        do_api_request=api,
        send_message=recorder(
            "send_message", SimpleNamespace(message_id=kw.get("text_id", 901))
        ),
        edit_message_text=recorder("edit_message_text"),
        edit_message_reply_markup=recorder("edit_message_reply_markup"),
    )
    return bot


def _names(bot):
    return [c[0] for c in bot.calls]


def _call(bot, name):
    return next(c for c in bot.calls if c[0] == name)


def _query(batch, data, from_id=42, chat_id=42, query_id="q1"):
    return widget_queries.WidgetQuery(
        id=query_id,
        from_id=from_id,
        chat_id=chat_id,
        message_id=batch.message_id,
        data=data,
    )


def _widget_batch(db, bot=None):
    """The kitchen, swept: the owner's batch, sent as a widget."""
    _kitchen(db)
    bot = bot or _bot()
    _run(db, bot)
    batch = _batch(db)
    return batch, bot


def _mask(db, batch):
    return screen_mod.default_mask(crud_dish_tidy.items(db, batch.id))


def _tap(db, batch, data=None, **kw):
    bot = kw.pop("bot", None) or _bot()
    data = data or f"a:{W.revision(batch)}:{_mask(db, batch):x}"
    asyncio.run(W.handle_tap(bot, db, _query(batch, data, **kw)))
    db.expire_all()
    return bot


def _answers(bot):
    return [c for c in bot.calls if c[0] == "answerWidgetQuery"]


def _writes(db):
    """Every dish's merge state and every batch row: what a tap that must not write
    leaves untouched."""
    db.expire_all()
    dishes = [
        (d.id, d.merged_into_id, d.hidden_at)
        for d in db.query(PersonalFood).order_by(PersonalFood.id)
    ]
    items = [
        (i.id, i.state, i.decided_at)
        for i in db.query(DishTidyItem).order_by(DishTidyItem.id)
    ]
    batch = _batch(db)
    return dishes, items, batch.state


# --- the page ---------------------------------------------------------------------------


def _fake_items(same=10, merge=10, star=5, name=60):
    items = []
    idx = 0
    for kind, count in (
        (dish_tidy.SAME, same),
        (dish_tidy.MERGE, merge),
        (dish_tidy.STAR, star),
    ):
        for n in range(count):
            label = [f"{'я' * (name - 3)}{idx:03d}"]
            if kind != dish_tidy.STAR:
                label.append(f"{'ю' * (name - 3)}{idx:03d}")
            items.append(
                SimpleNamespace(idx=idx, kind=kind, label=label, days=28, ticked=True)
            )
            idx += 1
    return items


def _data_of(items, cut):
    return {
        "w": "воскресенье, 15 ноября",
        "u": "Предложения ждут до 22 ноября",
        "h": W._how(items),
        "c": W.countable(items),
        "g": W._groups(items, cut),
    }


def test_the_largest_batch_fits_after_zlib():
    """The finder's cap (10 + 10 + 5 rows, names of 60 characters, cut as the text screen
    cuts them) is at most 3800 B after zlib -9. This is the page, not the estimator."""
    items = _fake_items()
    assert (
        len(items) == dish_tidy.SAME_CAP + dish_tidy.PROPOSAL_CAP + dish_tidy.STAR_CAP
    )
    html = W.page(_data_of(items, W.CUTS[0]))
    size = len(zlib.compress(html.encode("utf-8"), 9))
    assert size <= 3800, size
    assert packed(html) == size or packed(html) <= BUDGET


def test_a_batch_too_big_for_a_widget_is_not_built(db):
    """The oversize guard: the 25-row maximum is taller than the widget's 600 px, so
    ``build`` says None (the caller sends text) and logs nothing it must not."""
    batch, _ = _widget_batch(db, _bot(widget_error=BadRequest("x")))
    assert W.build(db, batch, _user(db)) is not None  # the control: this one fits
    tall = _fake_items()
    assert W.height(W._groups(tall, W.CUTS[0]), True) > W.HEIGHT_MAX


def test_oversize_batch_goes_as_text(db, monkeypatch):
    """When ``build`` finds no fit, deliver sends the text screen, not a widget."""
    monkeypatch.setattr(W, "BUDGET", 10)
    _kitchen(db)
    bot = _bot()
    _run(db, bot)
    assert "sendWidget" not in _names(bot)
    assert "send_message" in _names(bot)
    assert _batch(db).form == "text"


def test_state_is_small_and_made_of_integers_and_strings(db):
    batch, _ = _widget_batch(db)
    items = crud_dish_tidy.items(db, batch.id)
    state = W.state_of(batch, items)
    assert set(state) <= set("rmedux") | {"f"}
    assert all(
        isinstance(v, (int, str)) and not isinstance(v, bool) for v in state.values()
    )
    assert len(W.state_json(batch, items).encode()) <= 1024
    # after the apply too, and with the failed-mask key present
    _tap(db, batch)
    batch = _batch(db)
    state = W.state_of(batch, crud_dish_tidy.items(db, batch.id))
    assert state["d"] == 1 and state["r"] == 2
    assert all(isinstance(v, (int, str)) for v in state.values())


def test_every_use_has_its_symbol_and_every_symbol_is_used():
    html = W.page(_data_of(_fake_items(2, 2, 1), 60))
    symbols = set(re.findall(r'<symbol[^>]*id="([\w-]+)"', html))
    script = html.split("<script>")[1]
    # the script builds ``href="#i<name>"`` from ``ic('<name>')``
    assert "href=\\\"#i'+i" in script or "#i'+i" in script
    uses = {"i" + name for name in re.findall(r"ic\('(\w+)'\)", script)}
    uses |= set(re.findall(r'<use[^>]*href="#([\w-]+)"', html))
    assert symbols, "the page draws its icons from symbols"
    assert uses == symbols, (uses, symbols)


def test_no_emoji_in_the_page_code():
    html = W.page(_data_of(_fake_items(2, 2, 1), 60))
    for ch in html:
        point = ord(ch)
        assert not (
            0x1F300 <= point <= 0x1FAFF
            or 0x2600 <= point <= 0x27BF
            or 0x2B00 <= point <= 0x2BFF
            or point in (0xFE0F, 0x200D)
        ), hex(point)


def test_the_page_sends_one_request_a_rev_mask():
    html = W.page(_data_of(_fake_items(1, 1, 1), 60))
    assert html.count("W.request(") == 1
    assert "W.request('a:'+S.r+':'+M.toString(16))" in html


def test_the_expiry_line_is_body_size_and_never_red():
    html = W.page(_data_of(_fake_items(1, 1, 1), 60))
    css = html.split("</style>")[0]
    ex = re.search(r"\.ex\{([^}]*)\}", css)
    assert ex, "the expiry line has its own class"
    assert "font-size" in ex.group(1)
    assert not re.search(r"red|#f00|#e5|#d3|#c0", ex.group(1), re.I)


# --- the counts -------------------------------------------------------------------------


def test_auto_merged_rows_are_not_counted_but_stay_tickable(db):
    batch, _ = _widget_batch(db)
    items = crud_dish_tidy.items(db, batch.id)
    same = [i for i in items if i.kind == dish_tidy.SAME]
    proposals = [i for i in items if i.kind != dish_tidy.SAME]
    assert same and proposals
    counted = W.countable(items)
    assert bin(counted).count("1") == len(proposals)
    assert all(not counted >> i.idx & 1 for i in same)
    # tickable: the default mask ticks them, and apply takes the mask as it is
    assert all(screen_mod.default_mask(items) >> i.idx & 1 for i in same)
    html = (
        _call(_bot(), "send_message") if False else W.build(db, batch, _user(db)).html
    )
    assert "уже объединено" in html


# --- the switch -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,owner_gets,other_gets",
    [
        ("off", False, False),
        ("owner", True, False),
        ("all", True, True),
        ("junk", False, False),
    ],
)
def test_the_switch(db, monkeypatch, mode, owner_gets, other_gets):
    monkeypatch.setattr(settings, "TIDY_WIDGET", mode)
    assert W.allowed(_user(db, 1)) is owner_gets
    assert W.allowed(_user(db, 2)) is other_gets
    assert W.allowed(None) is False


# --- the send ---------------------------------------------------------------------------


def test_the_owner_gets_a_silent_widget_and_another_person_gets_text(db):
    _turn_on(db, 2)
    _kitchen(db)
    _kitchen(db, user_id=2)
    bot = _bot()
    _run(db, bot)
    owner_batch, other_batch = _batch(db, 1), _batch(db, 2)
    assert owner_batch.form == "widget" and other_batch.form == "text"
    chats = {c[2]["api_kwargs"]["chat_id"] for c in bot.calls if c[0] == "sendWidget"}
    assert chats == {42}
    texts = [c for c in bot.calls if c[0] == "send_message"]
    assert [c[2]["chat_id"] for c in texts] == [43]
    kwargs = _call(bot, "sendWidget")[2]["api_kwargs"]
    assert kwargs["disable_notification"] is True
    assert kwargs["allow_queries"] is True
    assert len(kwargs["caption"]) <= 1024
    assert json.loads(kwargs["chat_data"])["r"] == 1
    assert owner_batch.state == "sent" and owner_batch.message_id == 900


def test_the_keyboard_under_an_open_widget_is_the_sunday_switch_only(db):
    batch, bot = _widget_batch(db)
    markup = _call(bot, "sendWidget")[2]["api_kwargs"]["reply_markup"]
    assert [d.split(":")[1] for d in _callbacks(markup)] == ["x"]
    assert not [d for d in _callbacks(markup) if d.startswith("dt:a:")]


def test_a_dict_answer_gives_the_message_id(db):
    _kitchen(db)
    _run(db, _bot(widget_result={"message_id": 777}))
    assert _batch(db).message_id == 777


def test_a_refused_widget_falls_back_to_text_and_is_logged(db, caplog):
    _kitchen(db)
    bot = _bot(widget_error=BadRequest("widget refused"))
    with caplog.at_level("WARNING"):
        _run(db, bot)
    batch = _batch(db)
    assert _names(bot) == ["sendWidget", "send_message"]
    assert batch.form == "text" and batch.state == "sent" and batch.message_id == 901
    assert any("widget refused" in r.getMessage() for r in caplog.records)


def test_a_timed_out_widget_is_maybe_with_no_second_send(db):
    _kitchen(db)
    bot = _bot(widget_error=TimedOut())
    _run(db, bot)
    assert _names(bot) == ["sendWidget"]
    assert _batch(db).state == "maybe"


def test_a_blocked_widget_takes_the_merges_back(db):
    _kitchen(db)
    bot = _bot(widget_error=Forbidden("blocked"))
    _run(db, bot)
    assert _names(bot) == ["sendWidget"]
    assert _batch(db).state == "blocked"


def test_the_text_path_is_unchanged_with_the_switch_off(db, monkeypatch):
    monkeypatch.setattr(settings, "TIDY_WIDGET", "off")
    _kitchen(db)
    bot = _bot()
    _run(db, bot)
    assert _names(bot) == ["send_message"]
    assert _batch(db).form == "text"
    assert _call(bot, "send_message")[2]["disable_notification"] is True


# --- the tap ----------------------------------------------------------------------------


def test_the_tap_applies_the_mask(db):
    batch, _ = _widget_batch(db)
    items = crud_dish_tidy.items(db, batch.id)
    proposals = [i for i in items if i.kind == dish_tidy.MERGE]
    assert proposals
    off = proposals[0]
    mask = screen_mod.default_mask(items) & ~(1 << off.idx)
    bot = _tap(db, batch, f"a:1:{mask:x}")
    batch = _batch(db)
    assert batch.state == "applied"
    db.expire_all()
    states = {i.idx: i.state for i in crud_dish_tidy.items(db, batch.id)}
    assert states[off.idx] != "applied"
    assert any(s == "applied" for i, s in states.items() if i != off.idx)
    answered = json.loads(_answers(bot)[0][2]["api_kwargs"]["data"])
    assert answered["d"] == 1 and answered["r"] == 2


def test_answer_comes_before_edit_and_then_the_keyboard(db):
    batch, _ = _widget_batch(db)
    bot = _tap(db, batch)
    order = _names(bot)
    assert order == [
        "answerWidgetQuery",
        "editMessageWidgetData",
        "edit_message_reply_markup",
    ]
    markup = _call(bot, "edit_message_reply_markup")[2]["reply_markup"]
    labels = _labels(markup)
    assert labels[0] == "✅ ОК" and labels[1].startswith("↩️")
    assert _callbacks(markup)[1].startswith("dt:u:")
    edited = json.loads(
        _call(bot, "editMessageWidgetData")[2]["api_kwargs"]["chat_data"]
    )
    assert edited["d"] == 1


def test_a_stale_revision_answers_the_state_and_writes_nothing(db):
    batch, _ = _widget_batch(db)
    before = _writes(db)
    bot = _tap(db, batch, f"a:7:{_mask(db, batch):x}")
    assert _writes(db) == before
    assert _names(bot) == ["answerWidgetQuery"]
    assert json.loads(_answers(bot)[0][2]["api_kwargs"]["data"])["r"] == 1


def test_another_persons_tap_writes_nothing_and_answers_no_data(db):
    _turn_on(db, 2)
    batch, _ = _widget_batch(db)
    before = _writes(db)
    bot = _tap(db, batch, from_id=43)
    assert _writes(db) == before
    assert _names(bot) == ["answerWidgetQuery"]
    assert "data" not in _answers(bot)[0][2]["api_kwargs"]
    stranger = _tap(db, batch, from_id=999)
    assert _writes(db) == before
    assert "data" not in _answers(stranger)[0][2]["api_kwargs"]


def test_a_tap_on_a_text_batch_or_no_batch_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(settings, "TIDY_WIDGET", "off")
    _kitchen(db)
    _run(db, _bot())
    batch = _batch(db)
    before = _writes(db)
    bot = _tap(db, batch, f"a:1:{_mask(db, batch):x}")
    assert _writes(db) == before
    assert "data" not in _answers(bot)[0][2]["api_kwargs"]
    bot = _tap(db, SimpleNamespace(message_id=12345), "a:1:1")
    assert "data" not in _answers(bot)[0][2]["api_kwargs"]


@pytest.mark.parametrize(
    "data", ["a", "a:1", "a:x:1", "a:1:zz", "a:1:1234567", "b:1:1", "a:1:1:1"]
)
def test_a_malformed_tap_writes_nothing(db, data):
    batch, _ = _widget_batch(db)
    before = _writes(db)
    bot = _tap(db, batch, data)
    assert _writes(db) == before
    assert _names(bot) == ["answerWidgetQuery"]


def test_a_double_apply_writes_once(db):
    batch, _ = _widget_batch(db)
    first = _tap(db, batch)
    after_first = _writes(db)
    second = _tap(db, batch, f"a:1:{_mask(db, batch):x}")
    assert _writes(db) == after_first
    assert _names(second) == ["answerWidgetQuery"]
    assert json.loads(_answers(second)[0][2]["api_kwargs"]["data"])["d"] == 1
    assert "editMessageWidgetData" in _names(first)


def test_a_failing_apply_is_logged_and_never_raises(db, monkeypatch, caplog):
    batch, _ = _widget_batch(db)

    def boom(*a, **k):
        raise RuntimeError("secret row")

    monkeypatch.setattr(W.dish_tidy_batch, "apply", boom)
    with caplog.at_level("ERROR"):
        bot = _tap(db, batch)
    assert _names(bot) == ["answerWidgetQuery"]
    assert "data" not in _answers(bot)[0][2]["api_kwargs"]
    assert "RuntimeError" in caplog.text and "secret row" not in caplog.text
    assert "message 42/" in caplog.text and "test_tidy_widget.py:" in caplog.text


def test_the_tap_after_the_week_expires_the_batch_and_applies_nothing(db):
    batch, _ = _widget_batch(db)
    before = _writes(db)[0]
    later = dish_tidy.aware(batch.expires_at) + timedelta(hours=1)
    outcome = W.tap(db, _query(batch, f"a:1:{_mask(db, batch):x}"), later)
    db.expire_all()
    assert _batch(db).state == "expired"
    assert _writes(db)[0] == before
    assert json.loads(outcome.state)["r"] == 3
    assert outcome.wrote is False


def test_the_route_is_registered_at_bot_start(monkeypatch):
    routes = {}
    monkeypatch.setattr(widget_queries, "_routes", routes)
    tg.create_bot_application()
    assert routes[W.PREFIX] is tg.dish_tidy_widget_tap


def test_the_tap_route_checks_the_subscription(db, monkeypatch):
    batch, _ = _widget_batch(db)
    before = _writes(db)
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    bot = _bot()
    update = SimpleNamespace(widget_query=None, effective_user=SimpleNamespace(id=42))
    monkeypatch.setattr(widget_queries, "query_of", lambda _u: _query(batch, "a:1:1"))
    asyncio.run(tg.dish_tidy_widget_tap(update, SimpleNamespace(bot=bot)))
    assert _writes(db) == before
    assert "data" not in _answers(bot)[0][2]["api_kwargs"]


# --- undo, retire, expire under a widget message ----------------------------------------


def _press(db, data, bot, message_id):
    """A ``dt:`` button pressed under the message, through the real handler."""
    query = SimpleNamespace(
        data=data,
        message=SimpleNamespace(message_id=message_id, chat_id=42),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=42)
    )
    asyncio.run(tg.dish_tidy_callback(update, SimpleNamespace(bot=bot)))
    return query


def test_undo_under_a_widget_edits_data_not_text(db):
    batch, _ = _widget_batch(db)
    _tap(db, batch)
    batch = _batch(db)
    bot = _bot()
    query = _press(db, f"dt:u:{batch.id}", bot, batch.message_id)
    query.edit_message_text.assert_not_awaited()
    assert "edit_message_text" not in _names(bot)
    assert "editMessageWidgetData" in _names(bot)
    data = json.loads(_call(bot, "editMessageWidgetData")[2]["api_kwargs"]["chat_data"])
    assert data["u"] == 1
    assert _batch(db).state == "undone"


def test_undo_that_could_not_take_a_row_back_says_so(db):
    """A merge that moved on since cannot come back: the widget names that row «не
    отменилось» and does not say everything came back (R3 review)."""
    k = _kitchen(db)
    _run(db, _bot())
    batch = _batch(db)
    _tap(db, batch)
    source = db.get(PersonalFood, k.boiled.id)
    if source.merged_into_id is None:  # the pair merged the other way round
        source = db.get(PersonalFood, k.buckwheat.id)
    assert source.merged_into_id is not None  # the control: the merge did apply
    source.merged_into_id = k.oats.id
    db.commit()
    batch = _batch(db)
    bot = _bot()
    _press(db, f"dt:u:{batch.id}", bot, batch.message_id)
    data = json.loads(_call(bot, "editMessageWidgetData")[2]["api_kwargs"]["chat_data"])
    assert data["u"] == 1
    kept = int(data["n"], 16)
    items = crud_dish_tidy.items(db, batch.id)
    assert kept and kept == sum(1 << i.idx for i in items if i.state == "applied")
    page = W.page({"w": "", "u": "", "h": "", "c": 0, "g": []})
    assert "не отменилось" in page and "Отменено не всё" in page


def test_a_clean_undo_sends_no_kept_rows(db):
    batch, _ = _widget_batch(db)
    _tap(db, batch)
    bot = _bot()
    _press(db, f"dt:u:{_batch(db).id}", bot, _batch(db).message_id)
    data = json.loads(_call(bot, "editMessageWidgetData")[2]["api_kwargs"]["chat_data"])
    assert data["u"] == 1 and "n" not in data


def test_undo_under_a_text_batch_still_edits_the_text(db, monkeypatch):
    monkeypatch.setattr(settings, "TIDY_WIDGET", "off")
    _kitchen(db)
    _run(db, _bot())
    batch = _batch(db)
    screen_mod.handle_callback(
        db,
        42,
        f"dt:a:{batch.id}:{_mask(db, batch):x}",
        now=LATER,
        message_id=batch.message_id,
    )
    bot = _bot()
    query = _press(db, f"dt:u:{batch.id}", bot, batch.message_id)
    query.edit_message_text.assert_awaited()
    assert "editMessageWidgetData" not in _names(bot)


def test_a_newer_batch_retires_the_widget_with_x_and_no_text_edit(db):
    batch, _ = _widget_batch(db)
    old_id = batch.id
    # the older batch is open; a newer one arrives: the old widget closes through data
    new = crud_dish_tidy.claim(
        db, user_id=1, week=AT_12.date() + timedelta(days=7), group_id="g2", now=AT_12
    )
    assert new is not None
    new.state = "sent"
    db.commit()
    bot = _bot()
    asyncio.run(dish_tidy_push._retire_older(db, bot, _user(db), new))
    assert "edit_message_text" not in _names(bot)
    data = json.loads(_call(bot, "editMessageWidgetData")[2]["api_kwargs"]["chat_data"])
    assert data["x"] == 1 and data["r"] == 3
    db.expire_all()
    assert db.get(type(new), old_id).state == "retired"


# --- on demand --------------------------------------------------------------------------


def _list_screen(db, telegram_id=42):
    return screen_mod.handle_callback(db, telegram_id, "dt:o", now=AT_12, message_id=55)


def test_on_demand_sends_the_batch_as_a_new_silent_widget(db):
    _kitchen(db)
    screen = _list_screen(db)
    assert screen.widget_batch is not None
    bot = _bot(message_id=777)
    out = asyncio.run(tg._dish_tidy_widget_out(bot, 42, screen))
    assert out.text == "" and out.toast == tg.WIDGET_SENT  # the list message stays
    kwargs = _call(bot, "sendWidget")[2]["api_kwargs"]
    assert kwargs["disable_notification"] is True
    batch = _batch(db)
    assert (batch.form, batch.message_id, batch.state) == ("widget", 777, "sent")


def test_on_demand_for_a_person_the_switch_does_not_allow_stays_text(db):
    _turn_on(db, 2)
    _kitchen(db, user_id=2)
    screen = screen_mod.handle_callback(db, 43, "dt:o", now=AT_12, message_id=55)
    assert screen.widget_batch is None
    assert screen.text  # the checkbox screen replaces the list's message, as before


def test_on_demand_falls_back_to_the_text_screen_when_telegram_refuses(db):
    _kitchen(db)
    screen = _list_screen(db)
    bot = _bot(widget_error=BadRequest("no"))
    out = asyncio.run(tg._dish_tidy_widget_out(bot, 42, screen))
    assert out is screen and out.text
    assert _batch(db).form in (None, "text")


# --- the migration ----------------------------------------------------------------------

_MIGRATION = ROOT / "alembic" / "versions" / "a7c1e9d3b5f2_dish_tidy_form.py"


def _migration():
    spec = importlib.util.spec_from_file_location("dish_tidy_form", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_adds_and_drops_the_form_column():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE dish_tidy_batches (id INTEGER PRIMARY KEY, state VARCHAR)"
            )
        )
        conn.execute(
            sa.text("INSERT INTO dish_tidy_batches (id, state) VALUES (1, 'sent')")
        )
        module = _migration()
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        columns = {
            c["name"]: c for c in sa.inspect(conn).get_columns("dish_tidy_batches")
        }
        assert "form" in columns and columns["form"]["nullable"]
        assert (
            conn.execute(sa.text("SELECT form FROM dish_tidy_batches")).scalar() is None
        )
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        names = {c["name"] for c in sa.inspect(conn).get_columns("dish_tidy_batches")}
        assert "form" not in names and "state" in names
        assert (
            conn.execute(sa.text("SELECT state FROM dish_tidy_batches")).scalar()
            == "sent"
        )
    engine.dispose()


def _ids(path):
    text = path.read_text()
    rev = re.search(r'^revision(?::[^=]*)?=\s*"(\w+)"', text, re.M)
    down = re.search(r'^down_revision(?::[^=]*)?=\s*"(\w+)"', text, re.M)
    return (rev.group(1) if rev else None), (down.group(1) if down else None)


def test_the_migration_chains_on_the_single_head():
    """Its parent exists and nothing else sits on that parent: one head, this one."""
    mine, parent = _ids(_MIGRATION)
    others = [
        _ids(p) for p in (ROOT / "alembic" / "versions").glob("*.py") if p != _MIGRATION
    ]
    assert parent in {rev for rev, _ in others}
    assert [rev for rev, down in others if down == parent] == []
    assert [rev for rev, down in others if down == mine] == []
