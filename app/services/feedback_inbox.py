"""💬 Фидбэк on the owner's side: what people deliberately sent, one message a page.

    usr:f:<page> → <b>💬 Фидбэк</b>, everyone's, newest first
    usr:fp:<person>:<page> → <b>🗒 Фидбэк · Саша</b>, theirs, and back to their card
    📤 Отправить ещё раз, 📋 Весь текст → the whole copy, as it would have come
    🎙 Голосовое, 📷 Фото → the file alone, by the id Telegram holds

A message is read when it is shown. One that turned out to be food is a record now: its
words stay off these screens, and only a count says it was there. Telegram-free apart
from the keyboards; what goes out goes out in ``app.services.telegram``.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup

from app.core.config import settings
from app.crud.crud_access_grant import crud_access_grant
from app.crud.crud_feedback import crud_feedback
from app.crud.crud_user import crud_user
from app.models.feedback import PHOTO, VOICE
from app.services import feedback_screen, invites
from app.services.admin_notify import File, Part
from app.services.day_card import local_time
from app.services.favorites import Screen
from app.services.owner_buttons import (
    CALLBACK_PREFIX,
    NAME_CHARS,
    back_to_list,
    button,
    card_button,
    pager,
)

ALL = CALLBACK_PREFIX + "f:"  # + the page
ONES = CALLBACK_PREFIX + "fp:"  # + the person : the page
RESEND = CALLBACK_PREFIX + "fr:"  # + the row : the person or 0 : the page
FILE = CALLBACK_PREFIX + "ff:"  # the same, for the file alone

SENT_AGAIN = "<i>✓ Отправлено отдельным сообщением.</i>"
NOT_SENT = "<i>Не отправилось, сообщение на месте. Попробуй ещё раз.</i>"
_EMPTY = "Пока ничего."
_NOT_DELIVERED = "⚠️ Не доставлено тебе."
_NOT_HEARD = "Без расшифровки."
_KIND_LINES = {VOICE: "🎙 голосовое", PHOTO: "📷 фото"}
_FILE_LABELS = {VOICE: "🎙 Голосовое", PHOTO: "📷 Фото"}
_CUT_MARK = "…»"
_ROOM_FOR_TOP = 800  # the title, the person twice at 255 each, the counts


def zone() -> ZoneInfo:
    """The ledger's: these screens count in the ledger's days and read in its hours."""
    return ZoneInfo(settings.DEFAULT_TIMEZONE)


@dataclass(frozen=True)
class Who:
    telegram_id: int
    name: str  # never empty: the id only when nothing else is known
    username: Optional[str] = None


def who(db, telegram_id: int, grant=None) -> Who:
    """From what is known. A name on a grant is the owner's own word for the person."""
    grant = grant or crud_access_grant.get(db, telegram_id)
    user = crud_user.get_by_telegram_id(db, telegram_id)
    username = (grant.username if grant else None) or (user.username if user else None)
    name = (grant.display_name if grant else None) or username or str(telegram_id)
    return Who(telegram_id, name, username if username != name else None)


def when(moment: datetime, at_zone: ZoneInfo) -> str:
    return f"{local_time(moment, at_zone):%d.%m в %H:%M}"


def _file_of(row) -> Optional[File]:
    if not row.file_id:
        return None
    if row.kind == VOICE:
        return File("voice", row.file_id)
    # A screenshot sent as a file: Telegram refuses that id for a photo.
    as_document = "document" in (row.original or {})
    return File("document" if as_document else "photo", row.file_id)


def _words(row, room: int) -> tuple[str, bool]:
    """Escaped and quoted, and whether the whole of it fitted. Cut by what the words
    weigh once escaped, and counted Telegram's way: entities are parsed by then, but this
    errs to the short side and a button gives the rest."""
    said = "\n".join(w for w in (row.text, row.transcript) if w)
    if not said:
        return (_NOT_HEARD if row.kind == VOICE else ""), True
    shown = f"«{html.escape(said, quote=False)}»"
    if feedback_screen.units(shown) <= room:
        return shown, True
    room -= feedback_screen.units("«" + _CUT_MARK)
    kept, _rest = feedback_screen._cut(said, room)
    while feedback_screen.units(html.escape(kept, quote=False)) > room:
        kept = kept[: int(len(kept) * 0.9)]
    return f"«{html.escape(kept, quote=False)}{_CUT_MARK}", False


def _entry(db, row, at_zone: ZoneInfo, room: int) -> tuple[str, bool]:
    person = who(db, row.telegram_id)
    mark = "Новое · " if row.read_at is None else ""
    lines = [f"{mark}{html.escape(person.name)} · {when(row.created_at, at_zone)}"]
    if row.kind in _KIND_LINES:
        lines.append(_KIND_LINES[row.kind])
    words, whole = _words(row, room)
    if words:
        lines.append(words)
    if row.sent_at is None:
        lines.append(_NOT_DELIVERED)
    return "\n".join(lines), whole


def _actions(row, person: int, page: int, whole: bool) -> list:
    """A copy that did not arrive offers the copy. One that did offers what is not on the
    screen: the file, and the end of a long message."""
    address = f"{row.id}:{person}:{page}"
    if row.sent_at is None:
        return [button("📤 Отправить ещё раз", f"{RESEND}{address}")]
    actions = []
    if row.file_id:
        actions.append(button(_FILE_LABELS[row.kind], f"{FILE}{address}"))
    if not whole:
        actions.append(button("📋 Весь текст", f"{RESEND}{address}"))
    return actions


def view(
    db, page: int = 0, person: Optional[int] = None, notice: Optional[str] = None
) -> Screen:
    """One message a page. Showing it reads it."""
    total = crud_feedback.count_for_owner(db, person)
    pages = max(1, total)
    page = min(max(page, 0), pages - 1)
    named = who(db, person) if person else None
    title = f"🗒 Фидбэк · {html.escape(named.name)}" if named else "💬 Фидбэк"
    top = [f"<b>{title}</b>"]
    undelivered = crud_feedback.count_undelivered(db, person)
    if undelivered:
        top.append(f"⚠️ Не доставлено: {undelivered}")
    was_food = crud_feedback.count_was_food(db, person)
    if was_food:
        top.append(f"↩️ Оказалось едой и скрыто: {was_food}")

    prefix = f"{ONES}{person}:" if person else ALL
    rows = []
    if pages > 1:
        rows.append(pager(prefix, page, pages))
    row = crud_feedback.nth_for_owner(db, page, person) if total else None
    head = f"{notice}\n\n" if notice else ""
    if row is None:
        body = _EMPTY
    else:
        room = feedback_screen.LIMIT - feedback_screen.units(head) - _ROOM_FOR_TOP
        body, whole = _entry(db, row, zone(), room)
        actions = _actions(row, person or 0, page, whole)
        if actions:
            rows.append(actions)
        if not person:
            rows.append([card_button(who(db, row.telegram_id).name, row.telegram_id)])
        crud_feedback.mark_read(db, row.id)
    if named:
        rows.append(
            [button(f"⬅️ {named.name[:NAME_CHARS]}", f"{CALLBACK_PREFIX}p:{person}")]
        )
    else:
        rows.append(back_to_list())
    text = head + "\n".join(top) + f"\n\n{body}"
    return Screen(text, InlineKeyboardMarkup(rows))


@dataclass(frozen=True)
class Resend:
    """A tap that holds up: send this to the admin who tapped, then show the page."""

    feedback_id: int
    parts: tuple[Part, ...]
    file: Optional[File]
    person: int
    page: int


def _visible(db, feedback_id: int):
    row = crud_feedback.get(db, feedback_id)
    return None if row is None or row.was_food_at is not None else row


def resend(db, feedback_id: int, person: int, page: int) -> Optional[Resend]:
    """The whole copy, as it would have come. None: it is not there to send."""
    row = _visible(db, feedback_id)
    if row is None:
        return None
    named = who(db, row.telegram_id)
    sender = invites.Person(row.telegram_id, named.name, None, named.username)
    parts = feedback_screen.owner_parts(row, sender, row.created_at, zone())
    return Resend(row.id, tuple(parts), _file_of(row), person, page)


def file_alone(db, feedback_id: int, person: int, page: int) -> Optional[Resend]:
    row = _visible(db, feedback_id)
    if row is None or _file_of(row) is None:
        return None
    return Resend(row.id, (), _file_of(row), person, page)


def after_send(db, sent: Resend, whole: bool) -> Screen:
    """A whole copy with its words is a delivered message; a file alone says nothing new."""
    if whole and sent.parts:
        crud_feedback.mark_sent(db, sent.feedback_id)
    notice = SENT_AGAIN if whole else NOT_SENT
    return view(db, sent.page, sent.person or None, notice)
