"""/invite and /users: letting a person in, seeing who uses the bot, closing the door.

    /invite → the reply keyboard's [👤 Выбрать человека] [✖️ Отмена]
    a person picked → <i>✓ Саша: доступ открыт.</i> with the main keyboard back, and
      Telegram's rule, [📤 Отправить приглашение], [⬅️ Пользователи]
    /users → <b>👥 Пользователи</b>: thirty days' money, and a line a person;
      the people as buttons, ‹ 2/3 ›, [💬 Фидбэк: 2 новых], [➕ Пригласить]
    a person → <b>👤 Саша · @sasha</b>: how often and what it cost;
      [🗒 Фидбэк] [⛔ Закрыть доступ] or [↩️ Вернуть], [⬅️ Пользователи]
    ⛔ tap → <i>✓ Саша: доступ закрыт. Записи сохранены.</i> and ↩️ on the same screen

Usage and money only: nothing here reads a meal, a weight or an assessment, and the
journal it counts holds no food. For the owner only: ``handle`` refuses anyone else before
it reads a row. Days and hours are the ledger's. Telegram-free apart from the keyboards.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
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
from app.crud.crud_action_event import crud_action_event
from app.crud.crud_feedback import crud_feedback
from app.crud.crud_usage_ledger import Totals, crud_usage_ledger
from app.crud.crud_user import crud_user
from app.models import action_event
from app.models.usage_ledger import NO_PERSON
from app.services import feedback_inbox, invites, usage_ledger
from app.services.access_control import in_the_settings
from app.services.day_card import local_time
from app.services.favorites import Screen
from app.services.feedback_inbox import NOT_SENT, Who, who, zone  # noqa: F401
from app.services.owner_buttons import (
    CALLBACK_PREFIX,
    CARD,
    CARD_HERE,
    LIST,
    NAME_CHARS,
    back_to_list,
    button,
    pager,
)

logger = logging.getLogger(__name__)

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

STALE = "<i>Этой кнопки больше нет, вот список.</i>"
OWNER_LINE = "Владелец бота: доступ есть всегда."
LISTED_LINE = "Доступ задан в настройках сервера, кнопкой его не закрыть."
UNOPENED = "⏳ бот ещё не открыт"
CLOSED = "⛔ доступ закрыт"

INVITE = CALLBACK_PREFIX + "inv"
_CLOSE = CALLBACK_PREFIX + "x:"  # + the telegram id
_REOPEN = CALLBACK_PREFIX + "u:"
PAGE = 6  # two rows of three
DAYS = 7
WINDOW_DAYS = 30

_CLOSED_MODE = (
    "⚠️ Бот сейчас закрыт для всех, кроме владельца. Приглашение его не откроет: "
    "доступ заработает, когда BOT_ACCESS_MODE сменится с closed."
)
_ONLY_THEM = "Ссылка работает только у этого человека: у других бот молчит."
_OPEN_MODE = (
    "Бот сейчас открыт для всех: приглашение ничего не меняет, а закрыть доступ можно."
)


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
        return Screen(_CLOSED_MODE, InlineKeyboardMarkup([back_to_list()]))
    if delivered:
        words = "Приглашение бот отправил сам: этот человек уже открывал бота."
        return Screen(words, InlineKeyboardMarkup([back_to_list()]))
    words = (
        "Telegram не даёт боту написать первым, поэтому приглашение отправляешь ты. "
        + (_OPEN_MODE if mode == "open" else _ONLY_THEM)
    )
    share = InlineKeyboardButton(
        "📤 Отправить приглашение", url=share_url(bot_username)
    )
    return Screen(words, InlineKeyboardMarkup([[share], back_to_list()]))


def _mode_line() -> str:
    mode = settings.access_mode
    return {"closed": f"\n\n{_CLOSED_MODE}", "open": f"\n\n{_OPEN_MODE}"}.get(mode, "")


def money(usd: Decimal) -> str:
    """Ready for HTML. Under a cent is not nothing, and says so."""
    cents = usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if cents == 0 and usd != 0:
        return html.escape("<$0.01")
    return f"${cents}"


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def _requests(count: int) -> str:
    return f"{count} {_plural(count, 'запрос', 'запроса', 'запросов')}"


def _new_label(count: int) -> str:
    return f"💬 Фидбэк: {count} {_plural(count, 'новый', 'новых', 'новых')}"


def _window_start():
    """The ledger's window: its first day, today included in the thirty."""
    return usage_ledger.today() - timedelta(days=WINDOW_DAYS - 1)


def _strip(flags: list[bool]) -> str:
    drawn = "".join("▰" if flag else "▱" for flag in flags)
    return f"{drawn} {_days(flags)}"


def _days(flags: list[bool]) -> str:
    return f"{sum(flags)} из {DAYS} дней"


def _day_word(moment: datetime, at_zone) -> str:
    local = local_time(moment, at_zone).date()
    today = datetime.now(at_zone).date()
    if local == today:
        return "сегодня"
    if local == today - timedelta(days=1):
        return "вчера"
    return f"{local:%d.%m}"


def _seen_short(moment: datetime, at_zone) -> str:
    return f"{_day_word(moment, at_zone)}, {local_time(moment, at_zone):%H:%M}"


def _seen_long(moment: datetime, at_zone) -> str:
    return f"{_day_word(moment, at_zone)} в {local_time(moment, at_zone):%H:%M}"


def _unopened(grant, seen) -> bool:
    """A grant nobody has used. ``first_opened_at`` is set on /start alone, so someone who
    just wrote, with no /start, has opened the bot all the same."""
    return grant is not None and grant.first_opened_at is None and seen is None


def _line(person: Who, grant, seen, flags: list[bool], usd: Decimal, at_zone) -> str:
    parts = [html.escape(person.name)]
    closed = grant is not None and grant.revoked_at is not None
    if closed:
        parts.append(CLOSED)
    if _unopened(grant, seen):
        if not closed:
            parts.append(UNOPENED)
        if usd:
            parts.append(money(usd))
    else:
        parts.append(_seen_short(seen, at_zone) if seen else "нет действий")
        parts += [_days(flags), money(usd)]
    return " · ".join(parts)


def _money_notes(month: dict[int, Totals], people: set[int]) -> list[str]:
    """What the sum holds beyond the lines above it, so that the lines add up to it."""
    notes = []
    nobody = month.get(NO_PERSON)
    if nobody is not None and nobody.calls:
        notes.append(f"Без человека: {money(nobody.cost_usd)}")
    off_list = sum(
        (
            totals.cost_usd
            for person, totals in month.items()
            if person != NO_PERSON and person not in people
        ),
        Decimal(0),
    )
    if off_list:
        notes.append(f"Вне списка: {money(off_list)}")
    unpriced = sum(totals.unpriced_calls for totals in month.values())
    if unpriced:
        notes.append(f"⚠️ Запросов без известной цены: {unpriced}. В суммах их нет.")
    return notes


def hub(db, page: int = 0, notice: str | None = None) -> Screen:
    """A set number of statements, however many people or actions there are: the numbers
    come grouped, and the days and names only for the page shown."""
    at_zone = zone()
    month = crud_usage_ledger.by_person(db, since=_window_start())
    ever = crud_usage_ledger.by_person(db)
    seen = crud_action_event.last_seen_all(db)
    grants = {grant.telegram_id: grant for grant in crud_access_grant.everyone(db)}
    admins = set(settings.admin_ids)
    used = {
        person for person in settings.allowed_ids if person in ever or person in seen
    }
    people = (set(grants) | admins | used) - {NO_PERSON}

    def order(person: int):
        at = seen.get(person)
        return (
            at is None,
            -at.timestamp() if at else 0,
            person in admins,
            person,
        )

    ordered = sorted(people, key=order)
    pages = max(1, (len(ordered) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    shown = ordered[page * PAGE : (page + 1) * PAGE]
    days = crud_action_event.active_days_all(db, shown, zone=at_zone, days=DAYS)
    names = crud_user.usernames(db, [p for p in shown if p not in grants])

    def known(person: int) -> Who:
        grant = grants.get(person)
        if grant is not None:
            return Who(person, grant.display_name, grant.username)
        return Who(person, names.get(person) or str(person))

    lines = []
    buttons = []
    for person in shown:
        named = known(person)
        lines.append(
            _line(
                named,
                grants.get(person),
                seen.get(person),
                days.get(person, [False] * DAYS),
                month.get(person, Totals()).cost_usd,
                at_zone,
            )
        )
        buttons.append(button(named.name[:NAME_CHARS], f"{CARD}{person}"))

    total = sum((totals.cost_usd for totals in month.values()), Decimal(0))
    head = [f"<b>👥 Пользователи</b>\nЗа {WINDOW_DAYS} дней: 💸 {money(total)}"]
    blocks = [*head, "\n".join(lines)]
    notes = _money_notes(month, people)
    undelivered = crud_feedback.count_undelivered(db)
    if undelivered:
        notes.append(f"⚠️ Фидбэк не доставлен: {undelivered}.")
    if notes:
        blocks.append("\n".join(notes))
    top = f"{notice}\n\n" if notice else ""
    text = top + "\n\n".join(blocks) + _mode_line()

    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    if pages > 1:
        rows.append(pager(LIST, page, pages))
    if crud_feedback.count_for_owner(db):
        new = crud_feedback.count_new(db)
        label = _new_label(new) if new else "💬 Фидбэк"
        rows.append([button(label, f"{feedback_inbox.ALL}0")])
    rows.append([button("➕ Пригласить", INVITE)])
    return Screen(text, InlineKeyboardMarkup(rows))


def _stale(db, data: str) -> Screen:
    logger.warning("people: %r: unknown or foreign", data)
    return hub(db, notice=STALE)


def _when_line(person: int, grant, user, last, at_zone) -> list[str]:
    if _unopened(grant, last):
        return [
            f"Доступ открыт {local_time(grant.granted_at, at_zone):%d.%m}",
            UNOPENED,
        ]
    joined = grant.first_opened_at if grant is not None else None
    joined = joined or (grant.granted_at if grant is not None else None)
    joined = joined or (user.created_at if user is not None else None)
    seen = f"последний раз {_seen_long(last, at_zone)}" if last else "действий нет"
    if joined is None:
        return [seen.capitalize()]
    return [f"В боте с {local_time(joined, at_zone):%d.%m}, {seen}"]


def _card(db, telegram_id: int, notice: str | None = None) -> Screen | None:
    """None for someone nothing is known about."""
    if telegram_id == NO_PERSON:
        return None
    at_zone = zone()
    grant = crud_access_grant.get(db, telegram_id)
    month = crud_usage_ledger.of_person(db, telegram_id, since=_window_start())
    ever = crud_usage_ledger.of_person(db, telegram_id)
    last = crud_action_event.last_seen(db, telegram_id)
    written = crud_feedback.count_for_owner(db, telegram_id)
    is_owner = telegram_id in settings.admin_ids
    listed = in_the_settings(telegram_id)
    if not (grant or listed or ever.calls or last or written):
        return None

    named = who(db, telegram_id, grant)
    title = html.escape(named.name)
    if named.username:
        title += f" · @{html.escape(named.username)}"
    user = crud_user.get_by_telegram_id(db, telegram_id)
    flags = crud_action_event.active_days(db, telegram_id, zone=at_zone, days=DAYS)
    start = datetime.combine(_window_start(), time.min, tzinfo=at_zone).astimezone(UTC)
    counts = crud_action_event.counts_by_kind(db, telegram_id, since=start)

    def count(kind: str) -> int:
        return counts.get(kind, 0)

    spent = (
        f"💸 За {WINDOW_DAYS} дней: {money(month.cost_usd)} · {_requests(month.calls)}"
    )
    if month.unpriced_calls:
        spent += f" · {month.unpriced_calls} без цены"
    lines = [
        f"<b>👤 {title}</b>",
        *_when_line(telegram_id, grant, user, last, at_zone),
    ]
    if grant is not None and grant.revoked_at is not None:
        lines.append(CLOSED)
    lines += [
        _strip(flags),
        f"За {WINDOW_DAYS} дней: 📷 {count(action_event.PHOTO)} · "
        f"✍️ {count(action_event.TEXT)} · 🎙 {count(action_event.VOICE)}",
        f"✏️ Исправлений: {count(action_event.CORRECTED)} · "
        f"⚠️ Сбоев: {count(action_event.FAILED)}",
        spent,
        f"💸 За всё время: {money(ever.cost_usd)}",
        f"💬 Фидбэк: {written}",
    ]
    action = []
    if written:
        action.append(button("🗒 Фидбэк", f"{feedback_inbox.ONES}{telegram_id}:0"))
    if is_owner:
        lines.append(OWNER_LINE)
    elif listed:
        lines.append(LISTED_LINE)
    elif grant is not None and grant.revoked_at is not None:
        action.append(button("↩️ Вернуть", f"{_REOPEN}{telegram_id}"))
    elif grant is not None:
        action.append(button("⛔ Закрыть доступ", f"{_CLOSE}{telegram_id}"))
    rows = [action] if action else []
    rows.append(back_to_list())
    top = f"{notice}\n\n" if notice else ""
    return Screen(top + "\n".join(lines), InlineKeyboardMarkup(rows))


def _card_or_stale(
    db, data: str, telegram_id: int, notice: str | None = None
) -> Screen:
    return _card(db, telegram_id, notice) or _stale(db, data)


def _v_list(db, _by, page: str) -> Screen:
    return hub(db, int(page))


def _v_open(db, _by, telegram_id: str) -> Screen:
    return _card_or_stale(db, f"{CARD}{telegram_id}", int(telegram_id))


def _v_open_below(db, _by, telegram_id: str) -> Screen:
    return _card_or_stale(db, f"{CARD_HERE}{telegram_id}", int(telegram_id))


def _v_close(db, _by, telegram_id: str) -> Screen:
    person, data = int(telegram_id), f"{_CLOSE}{telegram_id}"
    if person in settings.admin_ids:
        return _card_or_stale(db, data, person)
    closed = invites.close(db, person)
    if closed is invites.Closed.IN_SETTINGS:
        notice = "<i>Этот доступ задан в настройках сервера.</i>"
        return _card_or_stale(db, data, person, notice)
    if closed is invites.Closed.UNKNOWN:
        return _stale(db, data)
    if closed is invites.Closed.ALREADY:
        return _card_or_stale(db, data, person)
    name = html.escape(who(db, person).name)
    notice = f"<i>✓ {name}: доступ закрыт. Записи сохранены.</i>"
    return _card_or_stale(db, data, person, notice)


def _v_reopen(db, by, telegram_id: str) -> Screen:
    person, data = int(telegram_id), f"{_REOPEN}{telegram_id}"
    if crud_access_grant.get(db, person) is None:
        return _stale(db, data)
    reopened = invites.reopen(db, person, by)
    if reopened is invites.Reopened.ALREADY:
        return _card_or_stale(db, data, person)
    name = html.escape(who(db, person).name)
    return _card_or_stale(db, data, person, f"<i>✓ {name}: доступ снова открыт.</i>")


def _v_inbox(db, _by, page: str) -> Screen:
    return feedback_inbox.view(db, int(page))


def _v_inbox_of(db, _by, telegram_id: str, page: str) -> Screen:
    person = int(telegram_id)
    if _card(db, person) is None:
        return _stale(db, f"{feedback_inbox.ONES}{telegram_id}:{page}")
    return feedback_inbox.view(db, int(page), person)


def _v_resend(db, _by, row: str, person: str, page: str):
    sending = feedback_inbox.resend(db, int(row), int(person), int(page))
    return sending or _stale(db, f"{feedback_inbox.RESEND}{row}")


def _v_file(db, _by, row: str, person: str, page: str):
    sending = feedback_inbox.file_alone(db, int(row), int(person), int(page))
    return sending or _stale(db, f"{feedback_inbox.FILE}{row}")


_VERBS = (
    (re.compile(r"usr:l:(\d{1,4})"), _v_list),
    (re.compile(r"usr:p:(\d{1,18})"), _v_open),
    (re.compile(r"usr:c:(\d{1,18})"), _v_open_below),
    (re.compile(r"usr:x:(\d{1,18})"), _v_close),
    (re.compile(r"usr:u:(\d{1,18})"), _v_reopen),
    (re.compile(r"usr:f:(\d{1,4})"), _v_inbox),
    (re.compile(r"usr:fp:(\d{1,18}):(\d{1,4})"), _v_inbox_of),
    (re.compile(r"usr:fr:(\d{1,18}):(\d{1,18}):(\d{1,4})"), _v_resend),
    (re.compile(r"usr:ff:(\d{1,18}):(\d{1,18}):(\d{1,4})"), _v_file),
)


def handle(db, telegram_id: int, data: str):
    """The next screen, or a ``Resend`` for the handler. A tap by anyone but the owner
    reads nothing, whatever it names; a stale or malformed one leads to the list."""
    if telegram_id not in settings.admin_ids:
        logger.warning("people: %r by %s: unknown or foreign", data, telegram_id)
        return Screen("", None, toast=NOT_YOURS)
    for pattern, verb in _VERBS:
        match = pattern.fullmatch(data)
        if match is not None:
            return verb(db, telegram_id, *match.groups())
    logger.warning("people: unknown callback %r", data)
    return hub(db, notice=STALE)
