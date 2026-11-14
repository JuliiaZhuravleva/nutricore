"""🍲 Порядок в блюдах: the batch on the chat's message, and what its buttons do.

    the batch → 🍲 <b>Порядок в блюдах</b> · 15 ноября, the rows in three parts, one
      button a row ([✓ 1. томатный соус → соус томатный]), [✅ Применить отмеченное (3)]
      and [✖ Не присылать по воскресеньям]
    a row's tap → the same message, that row's ✓ flipped; nothing is written
    ✅ → the ticks become the dishes: done, declined or «не вышло», on the same message,
      [✅ ОК] / [↩️ Отменить] when something was done
    ↩️ → every change of the batch taken back, as far as the dishes allow

The ticks travel in the buttons' data (a hex mask of the rows), so a tick costs no
write and «Применить» applies exactly what was shown. The near-identical dishes
(``same``) are merged before the message goes out: unticked, they come apart again.
A row unticked at «Применить», or taken back with ↩️, is not offered for 90 days
(``dish_tidy.DECLINE_DAYS``); a batch left alone declines nothing.

Every id in the data is checked against the tapping person through the batch. Nothing
here calls a model.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Optional

from telegram import InlineKeyboardMarkup

from app.crud.crud_dish_tidy import OPEN, crud_dish_tidy
from app.models.dish_tidy import DishTidyBatch, DishTidyItem
from app.models.record_event import RecordEvent
from app.models.user import User
from app.services import (
    dish_ops,
    dish_tidy,
    dish_tidy_batch,
    meal_card,
    settings_screen,
)
from app.services.close_row import ok_row
from app.services.day_card import local_time
from app.services.favorites import Screen
from app.services.meal_card import NO_MEAL, _Answer, _button, _cut, _Tap, _user
from app.services.meal_items import SAME_TOAST

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "dt:"
SWITCH = dish_tidy_batch.SWITCH
ROW_LABEL_MAX = 34
LINE_NAME_MAX = 60  # 20 lines of two names stay well under Telegram's 4096
_INT_MAX = 2**31 - 1

ENTRY_LABEL = "🍲 Порядок"  # third in the list's pager row: 13 characters at most
TITLE = "🍲 <b>Порядок в блюдах</b>"
HEADS = {
    dish_tidy.SAME: "Объединено само — одинаковые названия:",
    dish_tidy.MERGE: "Похоже на одно блюдо:",
    dish_tidy.STAR: "В ⭐ Избранное — часто в записях:",
}
HOW = "Нажатие на строку ставит или снимает ✓."
HOW_SAME = " Без ✓ объединение отменится, а предложение не применится."
HOW_PLAIN = " Без ✓ предложение не применится."
UNTIL = " Предложения действуют до {day}."
APPLY = "✅ Применить отмеченное ({count})"
DONE_LABEL = "✅ Готово"
OFF_LABEL = "✖ Не присылать по воскресеньям"
ON_LABEL = "🍲 Присылать по воскресеньям"
OFF_TOAST = "По воскресеньям больше не присылаю"
ON_TOAST = "По воскресеньям присылаю"
UNDO_ALL = "↩️ Отменить"
BACK = "⬅️ 🍲 Блюда"
NOTHING = "Сейчас предлагать нечего."
APPLIED = "✓ Применено."
NOTHING_APPLIED = "✓ Готово: ничего не изменено."
EXPIRED = "Предложения устарели: им больше недели."
EXPIRED_SAME = " Объединённое само не менялось."
RETIRED = "Эта подборка закрыта: есть новая."
CLOSED = "Эта подборка закрыта."
UNDONE_HEAD = "↩️ Отменено."
UNDONE = "↩️ Отменено: {done} из {total}."
UNDONE_NAMED = "↩️ Отменено: {what}."
NOT_BACK = " Не отменилось: {names} — {them} меняли после."
STALE_LINE = " — не вышло: блюда изменились после подборки"
NOT_SENT = (
    "Сообщение с подборкой не дошло, и её объединения отменены. Кроме отмеченных ✓: "
    "эти блюда меняли после, они остались объединены."
)
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


# --- the switch ---------------------------------------------------------------------


def _set_on(db, user: User, on: bool) -> None:
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), SWITCH: on}


# --- words --------------------------------------------------------------------------


def _days_word(count: int) -> str:
    if count % 100 in (11, 12, 13, 14):
        return "дней"
    if count % 10 == 1:
        return "день"
    return "дня" if count % 10 in (2, 3, 4) else "дней"


def _day(moment: datetime, user: User) -> str:
    local = local_time(dish_tidy.aware(moment), settings_screen.user_zone(user)).date()
    return f"{local.day} {_MONTHS[local.month - 1]}"


def _pair_text(item: DishTidyItem) -> str:
    names = item.label or []
    if item.kind == dish_tidy.STAR:
        return names[0] if names else "?"
    return f"{names[0]} → {names[1]}" if len(names) > 1 else "?"


def _line(n: int, item: DishTidyItem) -> str:
    names = [html.escape(_cut(name, LINE_NAME_MAX)) for name in item.label or []]
    if item.kind == dish_tidy.STAR:
        days = item.days
        return f"{n}. {names[0]} — {days} {_days_word(days)} из {dish_tidy.WINDOW_DAYS}"
    return f"{n}. «{names[0]}» → «{names[1]}»"


# --- the mask -----------------------------------------------------------------------


def default_mask(items: list) -> int:
    return sum(1 << item.idx for item in items if item.ticked)


def _hex(mask: int) -> str:
    return f"{mask:x}"


# --- the views ----------------------------------------------------------------------


def _btn(label: str, data: str):
    return _button(label, CALLBACK_PREFIX + data)


def open_view(db, batch: DishTidyBatch, user: User, mask: int) -> Screen:
    items = crud_dish_tidy.items(db, batch.id)
    lines = [f"{TITLE} · {_day(batch.claimed_at, user)}"]
    kinds = []
    for item in items:
        if item.kind not in kinds:
            kinds.append(item.kind)
            lines.append("")
            lines.append(HEADS[item.kind])
        lines.append(_line(item.idx + 1, item))
    foot = HOW + (HOW_SAME if dish_tidy.SAME in kinds else HOW_PLAIN)
    if any(item.kind != dish_tidy.SAME for item in items) and batch.expires_at:
        foot += UNTIL.format(day=_day(batch.expires_at, user))
    lines += ["", foot]
    rows = []
    for item in items:
        mark = "✓ " if dish_tidy_batch.ticked(mask, item) else ""
        lead = f"{mark}{item.idx + 1}. " + (
            "⭐ " if item.kind == dish_tidy.STAR else ""
        )
        label = lead + _cut(_pair_text(item), ROW_LABEL_MAX - len(lead))
        flipped = mask ^ (1 << item.idx)
        rows.append([_btn(label, f"k:{batch.id}:{item.idx}:{_hex(flipped)}")])
    count = sum(1 for item in items if dish_tidy_batch.ticked(mask, item))
    rows.append(
        [
            _btn(
                APPLY.format(count=count) if count else DONE_LABEL,
                f"a:{batch.id}:{_hex(mask)}",
            )
        ]
    )
    if dish_tidy_batch.tidy_on(user):
        rows.append([_btn(OFF_LABEL, f"x:{batch.id}:{_hex(mask)}")])
    else:
        rows.append([_btn(ON_LABEL, f"s:{batch.id}:{_hex(mask)}")])
    if batch.week is None:  # opened from the list: the way back, ticks unapplied
        rows.append([_button(BACK, "dh:l:0")])
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows), view=NO_MEAL)


_MARKS = {
    "applied": "✓",
    "declined": "—",
    "stale": "✖",
    "undone": "↩️",
    "proposed": "·",
    "rolled_back": "↩️",
}


def _undoable(db, batch: DishTidyBatch) -> bool:
    """Whether ↩️ still has something of this batch to take back."""
    if batch.state in ("undone", "blocked", "failed"):
        return False
    for item in crud_dish_tidy.items(db, batch.id):
        if item.state != "applied" or item.event_id is None:
            continue
        event = db.get(RecordEvent, item.event_id)
        if event is not None and not dish_ops.is_undone(db, event):
            return True
    return False


def ended_view(db, batch: DishTidyBatch, user: User, head: str) -> Screen:
    items = crud_dish_tidy.items(db, batch.id)
    lines = [f"{TITLE} · {_day(batch.claimed_at, user)}", head, ""]
    for item in items:
        if item.state == "rolled_back" and item.event_id is None:
            continue  # a proposal the person never saw
        line = f"{_MARKS.get(item.state, '·')} {_line(item.idx + 1, item)}"
        if item.state == "stale":
            line += STALE_LINE
        lines.append(line)
    rows = []
    if _undoable(db, batch):
        rows = [ok_row(), [_btn(UNDO_ALL, f"u:{batch.id}")]]
    if batch.week is None:
        rows.append([_button(BACK, "dh:l:0")])
    markup = InlineKeyboardMarkup(rows) if rows else None
    return Screen("\n".join(lines).rstrip(), markup, view=NO_MEAL)


def nothing_view(user: Optional[User] = None) -> Screen:
    """Nothing to offer now. With the Sunday batch off, the way to turn it back on:
    without a batch, its buttons are not there to do it."""
    rows = []
    if user is not None and not dish_tidy_batch.tidy_on(user):
        rows.append([_btn(ON_LABEL, "on")])
    rows.append([_button(BACK, "dh:l:0")])
    text = f"{TITLE}\n{NOTHING}"
    return Screen(text, InlineKeyboardMarkup(rows), view=NO_MEAL)


# --- the verbs ----------------------------------------------------------------------


def _unknown(t: _Tap, data: str) -> Screen:
    logger.warning("dish tidy: unknown or foreign data %r", data)
    return meal_card._today(t)


def _own_batch(t: _Tap, data: str, bid: str) -> DishTidyBatch:
    batch = crud_dish_tidy.get(t.db, int(bid)) if int(bid) <= _INT_MAX else None
    if batch is None or t.user is None or batch.user_id != t.user.id:
        raise _Answer(_unknown(t, data))
    return batch


def _mask(t: _Tap, data: str, batch: DishTidyBatch, raw: str) -> int:
    mask = int(raw, 16)
    if mask >> len(crud_dish_tidy.items(t.db, batch.id)):
        raise _Answer(_unknown(t, data))
    return mask


def _show_left_over(t: _Tap, batch: DishTidyBatch) -> Screen:
    """What stayed merged, shown once with ↩️; the batch is settled by being seen."""
    batch.state = "applied"
    t.db.commit()
    return ended_view(t.db, batch, t.user, NOT_SENT)


def _closed(t: _Tap, batch: DishTidyBatch) -> Optional[Screen]:
    """The batch's ended view when its buttons no longer apply, else None. An open
    one past its week ends here, declining nothing."""
    if dish_tidy_batch.left_over(t.db, batch):
        return _show_left_over(t, batch)
    if batch.state in OPEN and t.now > dish_tidy.aware(batch.expires_at or t.now):
        batch.state = "expired"
        t.db.commit()
    if batch.state in OPEN:
        return None
    head = _head(t.db, batch)
    toast = None if batch.state in ("expired", "retired") else SAME_TOAST  # text says
    return replace(ended_view(t.db, batch, t.user, head), toast=toast)


def _head(db, batch: DishTidyBatch) -> str:
    """What an ended batch says above its lines: never «Применено» for what was not."""
    if batch.state == "expired":
        same = any(
            item.kind == dish_tidy.SAME for item in crud_dish_tidy.items(db, batch.id)
        )
        return EXPIRED + (EXPIRED_SAME if same else "")
    heads = {
        "retired": RETIRED,
        "applied": APPLIED,
        "undone": UNDONE_HEAD,
    }
    return heads.get(batch.state, CLOSED)


def _v_open(t: _Tap, data: str) -> Screen:
    """🍲 Блюда → «Порядок»: the open batch, else a pass right now."""
    for batch in crud_dish_tidy.open_batches(t.db, t.user.id):
        if dish_tidy_batch.left_over(t.db, batch):
            return _show_left_over(t, batch)
        if _closed(t, batch) is None:
            return open_view(t.db, batch, t.user, default_mask(_items(t, batch)))
    batch = crud_dish_tidy.claim(
        t.db, user_id=t.user.id, week=None, group_id=dish_ops.new_group(), now=t.now
    )
    if batch is None:
        return nothing_view(t.user)
    if not dish_tidy_batch.prepare(t.db, t.user, batch, t.now):
        return nothing_view(t.user)
    batch.state = "sent"
    batch.chat_id = t.telegram_id
    batch.message_id = t.message_id
    t.db.commit()
    return open_view(t.db, batch, t.user, default_mask(_items(t, batch)))


def _v_on(t: _Tap, data: str) -> Screen:
    """«🍲 Присылать по воскресеньям» on the nothing-to-offer screen."""
    _set_on(t.db, t.user, True)
    t.db.commit()
    return replace(nothing_view(t.user), toast=ON_TOAST)


def _items(t: _Tap, batch: DishTidyBatch) -> list:
    return crud_dish_tidy.items(t.db, batch.id)


def _v_tick(t: _Tap, data: str, bid: str, idx: str, raw: str) -> Screen:
    batch = _own_batch(t, data, bid)
    closed = _closed(t, batch)
    if closed is not None:
        return closed
    screen = open_view(t.db, batch, t.user, _mask(t, data, batch, raw))
    return replace(screen, toast=None)


def _v_switch(on: bool):
    def verb(t: _Tap, data: str, bid: str, raw: str) -> Screen:
        batch = _own_batch(t, data, bid)
        mask = _mask(t, data, batch, raw)
        _set_on(t.db, t.user, on)
        t.db.commit()
        closed = _closed(t, batch)
        toast = ON_TOAST if on else OFF_TOAST
        if closed is not None:
            return replace(closed, toast=toast)
        return replace(open_view(t.db, batch, t.user, mask), toast=toast)

    return verb


def _v_apply(t: _Tap, data: str, bid: str, raw: str) -> Screen:
    batch = _own_batch(t, data, bid)
    closed = _closed(t, batch)
    if closed is not None:
        return closed
    mask = _mask(t, data, batch, raw)
    result = dish_tidy_batch.apply(t.db, t.user, batch, mask, t.now)
    if result.already:  # another tap got here first
        return _closed(t, batch)
    head = APPLIED if result.kept or result.changed else NOTHING_APPLIED
    return replace(ended_view(t.db, batch, t.user, head), toast=None)


def _v_undo(t: _Tap, data: str, bid: str) -> Screen:
    batch = _own_batch(t, data, bid)
    result = dish_tidy_batch.undo(t.db, t.user, batch, t.now)
    if result.already:
        return replace(
            ended_view(t.db, batch, t.user, _head(t.db, batch)), toast=SAME_TOAST
        )
    head = UNDONE.format(done=result.undone, total=result.undone + len(result.not_back))
    if result.not_back:
        names = ", ".join(f"«{html.escape(n)}»" for n in result.not_back)
        them = "его" if len(result.not_back) == 1 else "их"
        head += NOT_BACK.format(names=names, them=them)
    return replace(ended_view(t.db, batch, t.user, head), toast=None)


_N = r"(\d{1,10})"
_I = r"(\d{1,2})"
_M = r"([0-9a-f]{1,6})"
_VERBS = (
    (re.compile(r"dt:o"), _v_open),
    (re.compile(r"dt:on"), _v_on),
    (re.compile(rf"dt:k:{_N}:{_I}:{_M}"), _v_tick),
    (re.compile(rf"dt:a:{_N}:{_M}"), _v_apply),
    (re.compile(rf"dt:u:{_N}"), _v_undo),
    (re.compile(rf"dt:x:{_N}:{_M}"), _v_switch(False)),
    (re.compile(rf"dt:s:{_N}:{_M}"), _v_switch(True)),
)


def is_known_shape(data: str) -> bool:
    return any(pattern.fullmatch(data) for pattern, _verb in _VERBS)


def handle_callback(
    db,
    telegram_id: int,
    data: str,
    now: Optional[datetime] = None,
    message_id: Optional[int] = None,
) -> Screen:
    """The screen a tap on a 🍲 tidy-up button leads to."""
    user = _user(db, telegram_id)
    t = _Tap(db, user, telegram_id, now or datetime.now(UTC), message_id)
    for pattern, verb in _VERBS:
        match = pattern.fullmatch(data)
        if match is None:
            continue
        if user is None:
            break
        try:
            return verb(t, data, *match.groups())
        except _Answer as answer:
            return answer.screen
    return _unknown(t, data)


def entry_button():
    """In the pager row of the 🍲 Блюда list's first page, beside «🔍 Найти»."""
    return _button(ENTRY_LABEL, CALLBACK_PREFIX + "o")
