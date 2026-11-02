"""What a person hears when the access ends, and the two lines of their first day.

Through the real application, as in test_invite: Telegram is a fake wire and the model a
fake reader.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.crud.crud_subscription import crud_subscription
from app.models import Meal
from app.services import invites, settings_screen, welcome_screen
from app.services.access_control import DOOR_CLOSED, NO_ACCESS, TERM_ENDED
from tests.test_capture_router import reader  # noqa: F401
from tests.test_invite import (  # noqa: F401
    OWNER,
    SASHA,
    ZHENYA,
    Session,
    _gives_main_keyboard,
    _invite,
    _labels,
    _markup,
    _nothing_in_the_diary,
    db,
    play,
    say,
    tap,
)

OLD_WORDS = ("🔒", "подписк", "💎", "руб", "Привет! Я NutriCore")
OLD_LABELS = ["❓ О боте", "💎 Получить подписку", "📱 Связаться с админом"]


def _term_ends(db) -> None:  # noqa: F811
    subscription = crud_subscription.get_by_telegram_id(db, SASHA)
    subscription.end_date = datetime.utcnow() - timedelta(days=1)
    db.commit()


def _came(db) -> None:  # noqa: F811
    """Sasha's first run is over and something is in the diary."""
    play(say(SASHA, "/start"), tap(SASHA, "wlc:later"), say(SASHA, "суп"))
    assert db.scalars(select(Meal.id)).all()


def _texts(wire, person=SASHA) -> list:
    return [p["text"] for p in wire.to(person)]


# --- 1. the term ended ------------------------------------------------------------------


@pytest.mark.parametrize("sent", ["/start", "борщ", "⚙️ Настройки", "⭐ Избранное"])
def test_when_the_term_ended_everything_gets_the_one_line(
    db, reader, sent  # noqa: F811
):
    _invite(db)
    _came(db)
    written = db.scalars(select(Meal.id)).all()
    _term_ends(db)
    reader.text.clear()

    wire = play(say(SASHA, sent))

    assert _texts(wire) == [TERM_ENDED]
    assert TERM_ENDED == (
        "Срок доступа закончился. Записи на месте. Продлить может тот, кто пригласил."
    )
    db.expire_all()
    assert db.scalars(select(Meal.id)).all() == written  # kept, and nothing added
    assert reader.text == []


def test_a_button_on_an_old_message_gives_the_same_line(db, reader):  # noqa: F811
    _invite(db)
    _came(db)
    _term_ends(db)
    wire = play(tap(SASHA, "set:open"))
    answer = [p for n, p in wire.calls if n == "answerCallbackQuery"][0]
    assert answer["text"] == TERM_ENDED
    assert answer["show_alert"] is True
    assert wire.edits() == []


@pytest.mark.parametrize("label", OLD_LABELS)
def test_a_label_still_on_a_phone_gets_the_line_and_loses_the_old_keyboard(
    db, reader, label  # noqa: F811
):
    _invite(db)
    _came(db)
    _term_ends(db)
    wire = play(say(SASHA, label))
    said = wire.to(SASHA)
    assert [p["text"] for p in said] == [TERM_ENDED]
    assert _markup(said[0]) == {"remove_keyboard": True}


@pytest.mark.parametrize("label", OLD_LABELS)
def test_after_a_renewal_that_label_brings_the_menu_back(
    db, reader, label  # noqa: F811
):
    _invite(db)
    _came(db)
    wire = play(say(SASHA, label))
    said = wire.to(SASHA)
    assert len(said) == 1
    assert _gives_main_keyboard(said[0])
    assert not any(word in said[0]["text"] for word in OLD_WORDS)


def test_a_renewal_opens_the_diary_again(db, reader):  # noqa: F811
    _invite(db)
    _came(db)
    written = len(db.scalars(select(Meal.id)).all())
    _term_ends(db)
    play(say(SASHA, "борщ"))
    crud_subscription.create_subscription(db, SASHA, 12)
    wire = play(say(SASHA, "борщ"))
    assert TERM_ENDED not in _texts(wire)
    db.expire_all()
    assert len(db.scalars(select(Meal.id)).all()) == written + 1


@pytest.mark.parametrize("sent", ["/start", "суп", *OLD_LABELS])
def test_someone_who_never_had_a_term_is_not_told_it_ended(
    db, reader, monkeypatch, sent  # noqa: F811
):
    """Only the open mode brings them here. The line must not claim a past they lack."""
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    wire = play(say(ZHENYA, sent))
    assert _texts(wire, ZHENYA) == [NO_ACCESS]
    _nothing_in_the_diary(db)


def test_control_the_word_lists_above_catch_the_old_screen():
    assert any(word in "🔒 Эта функция доступна с подпиской" for word in OLD_WORDS)


# --- 2. closed by the owner -------------------------------------------------------------


def test_closed_person_hears_it_once_and_then_nothing(db, reader):  # noqa: F811
    _invite(db)
    _came(db)
    invites.close(db, SASHA)
    reader.text.clear()

    wire = play(say(SASHA, "борщ"), say(SASHA, "/start"), say(SASHA, "суп"))

    assert _texts(wire) == [DOOR_CLOSED]
    assert DOOR_CLOSED == "Доступ закрыт владельцем бота. Записи сохранены."
    assert reader.text == []
    # And across a restart: the once is in the database, not in the process.
    assert play(say(SASHA, "ещё")).to(SASHA) == []


def test_a_closed_person_who_tapped_first_hears_it_too(db, reader):  # noqa: F811
    _invite(db)
    invites.close(db, SASHA)
    wire = play(tap(SASHA, "set:open"), say(SASHA, "суп"))
    assert _texts(wire) == [DOOR_CLOSED]


def test_reopened_and_closed_again_is_told_again(db, reader):  # noqa: F811
    _invite(db)
    invites.close(db, SASHA)
    play(say(SASHA, "суп"))
    invites.reopen(db, SASHA, OWNER)
    invites.close(db, SASHA)
    assert _texts(play(say(SASHA, "суп"))) == [DOOR_CLOSED]


def test_a_second_invitation_counts_as_a_new_door(db, reader):  # noqa: F811
    _invite(db)
    invites.close(db, SASHA)
    play(say(SASHA, "суп"))
    _invite(db)
    invites.close(db, SASHA)
    assert _texts(play(say(SASHA, "суп"))) == [DOOR_CLOSED]


def test_a_stranger_still_hears_nothing(db, reader):  # noqa: F811
    wire = play(say(ZHENYA, "/start"), say(ZHENYA, "суп"))
    assert wire.to(ZHENYA) == []


def test_closed_for_everyone_is_not_this_persons_door(
    db, reader, monkeypatch  # noqa: F811
):
    """The whole bot closed by the mode: their grant stands, and no line says otherwise."""
    _invite(db)
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "closed")
    assert play(say(SASHA, "суп")).to(SASHA) == []


def test_a_group_message_spends_nothing(db, reader):  # noqa: F811
    _invite(db)
    invites.close(db, SASHA)
    in_group = say(SASHA, "суп")
    in_group["message"]["chat"] = {"id": -500, "type": "group", "title": "g"}
    wire = play(in_group)
    assert wire.sent() == []
    assert _texts(play(say(SASHA, "суп"))) == [DOOR_CLOSED]


def test_a_chat_status_change_spends_nothing(db, reader):  # noqa: F811
    """Blocking the bot is an update too, and it has nobody to tell."""
    _invite(db)
    invites.close(db, SASHA)
    who = {"id": SASHA, "is_bot": False, "first_name": "S"}
    bot = {"id": 1, "is_bot": True, "first_name": "b"}
    blocked = {
        "update_id": 9000,
        "my_chat_member": {
            "chat": {"id": SASHA, "type": "private"},
            "from": who,
            "date": 1,
            "old_chat_member": {"status": "member", "user": bot},
            "new_chat_member": {"status": "kicked", "user": bot, "until_date": 0},
        },
    }
    assert play(blocked).sent() == []
    assert _texts(play(say(SASHA, "суп"))) == [DOOR_CLOSED]


# --- 3. the welcome says who sees what --------------------------------------------------


def test_the_welcome_says_who_can_read_what(db, reader):  # noqa: F811
    _invite(db)
    text = play(say(SASHA, "/start")).to(SASHA)[0]["text"]
    assert (
        "Ты видишь только свои записи. Владелец бота в своих экранах видит, как часто "
        "ты пишешь и сколько это стоит. Текст и фото читает внешний ИИ-сервис, а "
        "технически сообщения доступны тому, кто запускает бота." in text
    )
    assert "другим людям они не видны" not in text


# --- 4. the time zone is not asked, it is shown -----------------------------------------


def _zone_lines(wire) -> list:
    return [p for p in wire.to(SASHA) if p["text"].startswith("Время записи")]


def test_the_welcome_does_not_ask_for_a_zone(db, reader):  # noqa: F811
    _invite(db)
    said = play(say(SASHA, "/start")).to(SASHA)
    assert not any("пояс" in p["text"] for p in said)
    assert not any("пояс" in label for row in _labels(said[0]) for label in row)


def test_after_the_first_record_one_quiet_line_opens_the_zone(
    db, reader, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    _invite(db)
    wire = play(say(SASHA, "/start"), tap(SASHA, "wlc:later"), say(SASHA, "суп"))
    lines = _zone_lines(wire)
    assert len(lines) == 1
    line = lines[0]
    assert line["text"].endswith(". Если у тебя другое, поменяй пояс.")
    clock = line["text"].removeprefix("Время записи ")[:5]
    there = datetime.now(settings_screen.ZoneInfo("Asia/Tbilisi"))
    shown = there.replace(hour=int(clock[:2]), minute=int(clock[3:]))
    assert abs((there - shown).total_seconds()) < 120
    assert line["disable_notification"] is True
    assert _markup(line)["inline_keyboard"][0][0]["callback_data"] == "set:tz"

    # The tap opens the real picker, on that same message.
    opened = play(tap(SASHA, "set:tz")).edits()[0]
    assert opened["text"].startswith("<b>🕐 Часовой пояс</b>")


def test_the_line_comes_once(db, reader):  # noqa: F811
    _invite(db)
    wire = play(
        say(SASHA, "/start"),
        tap(SASHA, "wlc:later"),
        say(SASHA, "суп"),
        say(SASHA, "борщ"),
    )
    assert len(_zone_lines(wire)) == 1


def test_a_person_who_chose_a_zone_is_not_told_about_it(db, reader):  # noqa: F811
    _invite(db)
    play(say(SASHA, "/start"), tap(SASHA, "wlc:later"))
    settings_screen._save(db, SASHA, **{settings_screen.TIMEZONE: "Europe/Moscow"})
    assert _zone_lines(play(say(SASHA, "суп"))) == []


def test_someone_who_has_been_here_gets_no_line(db, reader):  # noqa: F811
    """No welcome behind them, as for everyone before this change: no first day."""
    _invite(db)
    settings_screen._save(db, SASHA, **{welcome_screen.KEY: welcome_screen.DONE})
    assert _zone_lines(play(say(SASHA, "суп"))) == []
