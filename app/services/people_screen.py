"""/invite and /users: letting a person in, seeing who was let in, closing the door.

    /invite → the reply keyboard's [👤 Выбрать человека] [✖️ Отмена]
    a person picked → <i>✓ Саша: доступ открыт.</i> with the main keyboard back, and
      Telegram's rule, [📤 Отправить приглашение], [⬅️ Пользователи]
    /users → <b>👥 Пользователи</b>: who, and whether they opened the bot;
      the people as buttons, [◀️] [▶️], [➕ Пригласить]
    a person → <b>👤 Саша · @sasha</b>: [⛔ Закрыть доступ] or [↩️ Вернуть], [⬅️ Пользователи]
    ⛔ tap → <i>✓ Саша: доступ закрыт. Записи сохранены.</i> and ↩️ on the same screen

For the owner only: ``handle`` refuses anyone else before it reads a row. Activity and
costs join this screen in a later slice. Telegram-free apart from the keyboards.
"""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlencode

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    ReplyKeyboardMarkup,
)

from app.core.config import settings
from app.crud.crud_access_grant import crud_access_grant
from app.services import invites
from app.services.access_control import in_the_settings
from app.services.favorites import Screen

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "us:"
REQUEST_ID = 1  # only one picker here; another request's answer is not ours
PICK = "👤 Выбрать человека"
CANCEL = "✖️ Отмена"
PROMPT = "Нажми кнопку внизу и выбери человека. Отменить — ✖️ Отмена."
FALLBACK = (
    "Если выбрать не получается, перешли сюда сообщение от этого человека "
    "или пришли его контакт. Отменить — ✖️ Отмена."
)
HIDDEN_ORIGIN = (
    "Этот человек скрыл свой аккаунт в пересылаемых сообщениях, поэтому бот его не "
    "узнает. Пришли его контакт или выбери кнопкой внизу."
)
A_BOT = "Это бот, а приглашать можно только людей."
YOURSELF = "Это ты: у владельца доступ есть всегда."
CANCELLED = "Приглашение отменено."
EXPIRED = "Приглашение отменено: время вышло. Начать заново — /invite."
FAILED = "Не получилось открыть доступ. Попробуй ещё раз: /invite."
NOT_YOURS = "Это только для владельца бота."
# To someone with a chat here already. Their name would be no news to them.
INVITATION = "👋 Тебя пригласили в дневник питания. Нажми /start, чтобы начать."

INVITE = CALLBACK_PREFIX + "inv"
_LIST = CALLBACK_PREFIX + "l:"  # + the page
_OPEN = CALLBACK_PREFIX + "p:"  # + the telegram id, and so on
_CLOSE = CALLBACK_PREFIX + "x:"
_REOPEN = CALLBACK_PREFIX + "u:"
_NAME_CHARS = 12  # what fits a button at three in a row
PAGE = 6  # two rows of three

_CLOSED_MODE = (
    "⚠️ Бот сейчас закрыт для всех, кроме владельца. Приглашение его не откроет: "
    "доступ заработает, когда BOT_ACCESS_MODE сменится с closed."
)
_ONLY_THEM = "Ссылка работает только у этого человека: у других бот молчит."
_OPEN_MODE = "Бот сейчас открыт для всех, поэтому доступ здесь ничего не меняет."


def picker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(
                    PICK,
                    request_users=KeyboardButtonRequestUsers(
                        request_id=REQUEST_ID,
                        user_is_bot=False,
                        max_quantity=1,
                        request_name=True,
                        request_username=True,
                    ),
                )
            ],
            [KeyboardButton(CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _back() -> list:
    return [InlineKeyboardButton("⬅️ Пользователи", callback_data=_LIST + "0")]


def _button(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)


def card_button(name: str, telegram_id: int) -> InlineKeyboardButton:
    """«👤 Саша»: their card on the owner's screens, from anywhere the owner reads them."""
    return _button(f"👤 {name[:_NAME_CHARS]}", f"{_OPEN}{telegram_id}")


def share_url(bot_username: str) -> str:
    """Telegram's own «кому отправить». The link names the bot and holds no token: the
    door is the person's id, so a forwarded link lets no one else in. ``url`` is required
    by that address, and the words are the owner's to edit before sending."""
    link = f"t.me/{bot_username}"
    query = urlencode(
        {
            "url": f"https://{link}",
            "text": f"Попробуй мой дневник питания: {link}",
        }
    )
    return f"https://t.me/share/url?{query}"


def notice_opened(name: str) -> str:
    return f"<i>✓ {html.escape(name)}: доступ открыт.</i>"


def invited(bot_username: str, delivered: bool = False) -> Screen:
    """What follows the notice, whichever way the invitation goes."""
    mode = settings.access_mode
    if mode == "closed":
        return Screen(_CLOSED_MODE, InlineKeyboardMarkup([_back()]))
    if delivered:
        words = "Приглашение бот отправил сам: этот человек уже открывал бота."
        return Screen(words, InlineKeyboardMarkup([_back()]))
    words = (
        "Telegram не даёт боту написать первым, поэтому приглашение отправляешь ты. "
        + (_OPEN_MODE if mode == "open" else _ONLY_THEM)
    )
    share = InlineKeyboardButton(
        "📤 Отправить приглашение", url=share_url(bot_username)
    )
    return Screen(words, InlineKeyboardMarkup([[share], _back()]))


def _status(grant) -> str:
    if grant.revoked_at is not None:
        return "⛔ доступ закрыт"
    if grant.first_opened_at is None:
        return "⏳ бот ещё не открыт"
    return "в боте"


def _mode_line() -> str:
    mode = settings.access_mode
    return {"closed": f"\n\n{_CLOSED_MODE}", "open": f"\n\n{_OPEN_MODE}"}.get(mode, "")


def hub(db, page: int = 0, notice: str | None = None) -> Screen:
    people = crud_access_grant.everyone(db)
    pages = max(1, (len(people) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    shown = people[page * PAGE : (page + 1) * PAGE]
    lines = [f"{html.escape(g.display_name)} · {_status(g)}" for g in shown]
    body = "\n".join(lines) or "Пока никого. Приглашённые появятся здесь."
    top = f"{notice}\n\n" if notice else ""
    text = f"{top}<b>👥 Пользователи</b>\n\n{body}{_mode_line()}"
    buttons = [
        _button(g.display_name[:_NAME_CHARS], f"{_OPEN}{g.telegram_id}") for g in shown
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    pager = []
    if page > 0:
        pager.append(_button("◀️", f"{_LIST}{page - 1}"))
    if page + 1 < pages:
        pager.append(_button("▶️", f"{_LIST}{page + 1}"))
    if pager:
        rows.append(pager)
    rows.append([_button("➕ Пригласить", INVITE)])
    return Screen(text, InlineKeyboardMarkup(rows))


def _card(grant, notice: str | None = None) -> Screen:
    who = html.escape(grant.display_name)
    if grant.username:
        who += f" · @{html.escape(grant.username)}"
    top = f"{notice}\n\n" if notice else ""
    text = f"{top}<b>👤 {who}</b>\n\n{_status(grant).capitalize()}"
    rows = []
    if in_the_settings(grant.telegram_id):
        text += "\n\nДоступ задан в настройках сервера, кнопкой его не закрыть."
    elif grant.revoked_at is not None:
        rows.append([_button("↩️ Вернуть", f"{_REOPEN}{grant.telegram_id}")])
    else:
        rows.append([_button("⛔ Закрыть доступ", f"{_CLOSE}{grant.telegram_id}")])
    rows.append(_back())
    return Screen(text, InlineKeyboardMarkup(rows))


def _v_list(db, _by, page: str) -> Screen:
    return hub(db, int(page))


def _found(db, data: str, telegram_id: str):
    grant = crud_access_grant.get(db, int(telegram_id))
    if grant is None:
        logger.warning("people: %r: unknown or foreign", data)
    return grant


def _v_open(db, _by, telegram_id: str) -> Screen:
    grant = _found(db, f"{_OPEN}{telegram_id}", telegram_id)
    return hub(db) if grant is None else _card(grant)


def _v_close(db, _by, telegram_id: str) -> Screen:
    grant = _found(db, f"{_CLOSE}{telegram_id}", telegram_id)
    if grant is None:
        return hub(db)
    name = html.escape(grant.display_name)
    closed = invites.close(db, grant.telegram_id)
    db.refresh(grant)
    if closed is invites.Closed.IN_SETTINGS:
        return _card(grant, "<i>Этот доступ задан в настройках сервера.</i>")
    if closed is invites.Closed.ALREADY:
        return _card(grant)
    return _card(grant, f"<i>✓ {name}: доступ закрыт. Записи сохранены.</i>")


def _v_reopen(db, by, telegram_id: str) -> Screen:
    grant = _found(db, f"{_REOPEN}{telegram_id}", telegram_id)
    if grant is None:
        return hub(db)
    reopened = invites.reopen(db, grant.telegram_id, by)
    db.refresh(grant)
    if reopened is invites.Reopened.ALREADY:
        return _card(grant)
    name = html.escape(grant.display_name)
    return _card(grant, f"<i>✓ {name}: доступ снова открыт.</i>")


_VERBS = (
    (re.compile(r"us:l:(\d{1,4})"), _v_list),
    (re.compile(r"us:p:(\d{1,20})"), _v_open),
    (re.compile(r"us:x:(\d{1,20})"), _v_close),
    (re.compile(r"us:u:(\d{1,20})"), _v_reopen),
)


def handle(db, telegram_id: int, data: str) -> Screen:
    """The next screen. A tap by anyone but the owner reads nothing, whatever it names."""
    if telegram_id not in settings.admin_ids:
        logger.warning("people: %r by %s: unknown or foreign", data, telegram_id)
        return Screen("", None, toast=NOT_YOURS)
    for pattern, verb in _VERBS:
        match = pattern.fullmatch(data)
        if match is not None:
            return verb(db, telegram_id, *match.groups())
    logger.warning("people: unknown callback %r", data)
    return hub(db)
