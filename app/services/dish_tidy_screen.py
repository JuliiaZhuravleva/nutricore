"""🍲 Порядок в блюдах: the batch on the chat's message, and what its buttons do.

    the batch → 🍲 <b>Порядок в блюдах</b> · 15 ноября, the rows in three parts, one
      button a row ([✓ 1. томатный соус → соус томатный]), [✅ Применить отмеченное (3)]
      and [✖ Не присылать по воскресеньям]
    a row's tap → the same message, that row's ✓ flipped; nothing is written
    ✅ → the ticks become the dishes: done, declined or «не вышло», on the same message,
      [✅ ОК] / [↩️ Вернуть всё] when something was done
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
from datetime import UTC, datetime, timedelta
from typing import Optional

from telegram import InlineKeyboardMarkup

from app.crud.crud_dish_tidy import OPEN, crud_dish_tidy
from app.models.dish_tidy import DishTidyBatch, DishTidyItem
from app.models.personal_food import PersonalFood
from app.models.record_event import RecordEvent
from app.models.user import User
from app.services import dish_ops, dish_tidy, meal_card, settings_screen
from app.services.close_row import ok_row
from app.services.day_card import local_time
from app.services.favorites import Screen
from app.services.meal_card import NO_MEAL, _Answer, _button, _cut, _Tap, _user
from app.services.meal_items import SAME_TOAST

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "dt:"
SWITCH = "dish_tidy"  # User.settings: False, no Sunday batch; on unless turned off
ROW_LABEL_MAX = 34
LINE_NAME_MAX = 60  # 20 lines of two names stay well under Telegram's 4096
OPEN_DAYS = 7  # the proposals hold this long
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
UNDO_ALL = "↩️ Вернуть всё"
BACK = "⬅️ 🍲 Блюда"
NOTHING = "Сейчас предлагать нечего."
APPLIED = "✓ Применено."
NOTHING_APPLIED = "✓ Готово: ничего не изменено."
EXPIRED = "Предложения устарели: им больше недели."
EXPIRED_SAME = " Объединённое само не менялось."
RETIRED = "Эта подборка закрыта: есть новая."
CLOSED = "Эта подборка закрыта."
UNDONE_HEAD = "↩️ Возвращено."
UNDONE = "↩️ Возвращено: {done} из {total}."
NOT_BACK = " Не вернулось: {names} — {them} меняли после."
STALE_LINE = " — не вышло: блюда изменились после подборки"
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


# --- the switch ---------------------------------------------------------------------


def tidy_on(user: Optional[User]) -> bool:
    return (user.settings or {}).get(SWITCH) is not False if user else True


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
    local = local_time(_aware(moment), settings_screen.user_zone(user)).date()
    return f"{local.day} {_MONTHS[local.month - 1]}"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)  # SQLite drops it


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


def _ticked(mask: int, item: DishTidyItem) -> bool:
    return bool(mask >> item.idx & 1)


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
        mark = "✓ " if _ticked(mask, item) else ""
        lead = f"{mark}{item.idx + 1}. " + (
            "⭐ " if item.kind == dish_tidy.STAR else ""
        )
        label = lead + _cut(_pair_text(item), ROW_LABEL_MAX - len(lead))
        flipped = mask ^ (1 << item.idx)
        rows.append([_btn(label, f"k:{batch.id}:{item.idx}:{_hex(flipped)}")])
    count = sum(1 for item in items if _ticked(mask, item))
    rows.append(
        [
            _btn(
                APPLY.format(count=count) if count else DONE_LABEL,
                f"a:{batch.id}:{_hex(mask)}",
            )
        ]
    )
    if tidy_on(user):
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
    if user is not None and not tidy_on(user):
        rows.append([_btn(ON_LABEL, "on")])
    rows.append([_button(BACK, "dh:l:0")])
    text = f"{TITLE}\n{NOTHING}"
    return Screen(text, InlineKeyboardMarkup(rows), view=NO_MEAL)


# --- making a batch -----------------------------------------------------------------


def prepare(db, user: User, batch: DishTidyBatch, now: datetime) -> bool:
    """The finder's rows into the batch, the near-identical merged under the batch's
    group; one commit. False when there is nothing to show (the batch is ``empty``).

    The finder reads before the lock: a failed embeddings query rolls back, which
    would let the lock go. ``merge`` re-reads each dish under the lock and refuses
    one that moved on meanwhile."""
    found = dish_tidy.find(db, user, now)
    dish_ops.lock_user(db, user)
    dish_ops.link_stars(db, user.id)  # an old ⭐ row moves with its merged dish
    idx = 0
    for row in found.rows:
        event_id, state = None, "proposed"
        if row.kind == dish_tidy.SAME:
            source = db.get(PersonalFood, row.source_id)
            target = db.get(PersonalFood, row.target_id)
            try:
                event = dish_ops.merge(
                    db, user, source, target, now=now, group_id=batch.group_id
                )
            except dish_ops.Refused:
                continue
            event_id, state = event.id, "applied"
        db.add(
            DishTidyItem(
                batch_id=batch.id,
                idx=idx,
                kind=row.kind,
                source_id=row.source_id,
                target_id=row.target_id,
                key=row.key,
                label=list(row.names),
                fingerprint=row.fingerprint,
                ticked=row.ticked,
                days=row.days,
                event_id=event_id,
                state=state,
            )
        )
        idx += 1
    batch.state = "ready" if idx else "empty"
    batch.expires_at = now + timedelta(days=OPEN_DAYS)
    db.commit()
    return bool(idx)


def roll_back(db, user: User, batch: DishTidyBatch, state: str) -> None:
    """The message never reached the person: the merges come apart again, and its rows
    count as never shown (not as declines). One commit.

    A merge that would not come apart (its dishes changed since) stays ``applied``,
    and the batch stays open as ``maybe``: 🍲 Блюда → «🍲 Порядок» shows it with
    «↩️ Вернуть всё», so no merge is left where nobody can see it. A blocked bot
    shows nothing anyway: that one only logs."""
    dish_ops.lock_user(db, user)
    result = dish_ops.undo_group(db, user, batch.group_id)
    for item in crud_dish_tidy.items(db, batch.id):
        event = db.get(RecordEvent, item.event_id) if item.event_id else None
        if event is None or dish_ops.is_undone(db, event):
            item.state = "rolled_back"
    if result.refused_names:
        logger.error(
            "dish tidy: batch %s not fully rolled back: %s left",
            batch.id,
            len(result.refused_names),
        )
        if state != "blocked":
            state = "maybe"
    batch.state = state
    db.commit()


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


def _closed(t: _Tap, batch: DishTidyBatch) -> Optional[Screen]:
    """The batch's ended view when its buttons no longer apply, else None. An open
    one past its week ends here, declining nothing."""
    if batch.state in OPEN and t.now > _aware(batch.expires_at or t.now):
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
        if _closed(t, batch) is None:
            return open_view(t.db, batch, t.user, default_mask(_items(t, batch)))
    batch = crud_dish_tidy.claim(
        t.db, user_id=t.user.id, week=None, group_id=dish_ops.new_group(), now=t.now
    )
    if batch is None:
        return nothing_view(t.user)
    if not prepare(t.db, t.user, batch, t.now):
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


def _still_as_shown(db, item: DishTidyItem) -> bool:
    """The row's dishes are still live and named and counted as in the message."""
    source = db.get(PersonalFood, item.source_id or 0)
    if source is None or source.hidden_at is not None or source.merged_into_id:
        return False
    if item.kind == dish_tidy.STAR:
        return dish_tidy.star_fingerprint(_dish(source)) == item.fingerprint
    target = db.get(PersonalFood, item.target_id or 0)
    if target is None or target.hidden_at is not None or target.merged_into_id:
        return False
    return dish_tidy.pair_fingerprint(_dish(source), _dish(target)) == item.fingerprint


def _dish(row: PersonalFood) -> dish_tidy.Dish:
    return dish_tidy.Dish(
        id=row.id, name=row.canonical_name, kcal=dish_tidy._float(row.per_100g_calories)
    )


def _v_apply(t: _Tap, data: str, bid: str, raw: str) -> Screen:
    batch = _own_batch(t, data, bid)
    closed = _closed(t, batch)
    if closed is not None:
        return closed
    mask = _mask(t, data, batch, raw)
    dish_ops.lock_user(t.db, t.user)
    t.db.refresh(batch)
    if batch.state not in OPEN:  # another tap got here first
        return _closed(t, batch)
    dish_ops.link_stars(t.db, t.user.id)
    items = _items(t, batch)
    order = {dish_tidy.SAME: 0, dish_tidy.MERGE: 1, dish_tidy.STAR: 2}
    changed = False
    for item in sorted(items, key=lambda i: (order[i.kind], i.idx)):
        ticked = _ticked(mask, item)
        item.decided_at = t.now
        if item.kind == dish_tidy.SAME:
            if ticked or item.state != "applied":
                continue
            event = t.db.get(RecordEvent, item.event_id or 0)
            try:
                if event is None or dish_ops.is_undone(t.db, event):
                    raise dish_ops.Refused(_pair_text(item))
                dish_ops.undo(t.db, t.user, event, group_id=batch.group_id)
            except dish_ops.Refused:
                item.state = "stale"
                continue
            item.state = "declined"
            changed = True
            continue
        if not ticked:
            item.state = "declined"
            continue
        if not _still_as_shown(t.db, item):
            item.state = "stale"
            continue
        source = t.db.get(PersonalFood, item.source_id)
        try:
            if item.kind == dish_tidy.MERGE:
                target = t.db.get(PersonalFood, item.target_id)
                event = dish_ops.merge(
                    t.db, t.user, source, target, now=t.now, group_id=batch.group_id
                )
            else:
                event = dish_ops.star(t.db, t.user, source, group_id=batch.group_id)
        except dish_ops.Refused:
            item.state = "stale"
            continue
        item.state = "applied"
        item.event_id = event.id if event is not None else None
        changed = True
    batch.state = "applied"
    t.db.commit()
    done = any(item.state == "applied" for item in items)
    head = APPLIED if done or changed else NOTHING_APPLIED
    return replace(ended_view(t.db, batch, t.user, head), toast=None)


def _v_undo(t: _Tap, data: str, bid: str) -> Screen:
    batch = _own_batch(t, data, bid)
    dish_ops.lock_user(t.db, t.user)
    t.db.refresh(batch)
    if batch.state in ("undone", "blocked", "failed"):
        return replace(
            ended_view(t.db, batch, t.user, _head(t.db, batch)), toast=SAME_TOAST
        )
    items = [
        item
        for item in _items(t, batch)
        if item.state == "applied" and item.event_id is not None
    ]
    result = dish_ops.undo_group(t.db, t.user, batch.group_id)
    if result.already:
        t.db.rollback()
        return replace(
            ended_view(t.db, batch, t.user, _head(t.db, batch)), toast=SAME_TOAST
        )
    for item in items:
        event = t.db.get(RecordEvent, item.event_id)
        if event is not None and dish_ops.is_undone(t.db, event):
            item.state = "undone"
            item.decided_at = t.now
    batch.state = "undone"
    t.db.commit()
    head = UNDONE.format(
        done=result.undone, total=result.undone + len(result.refused_names)
    )
    if result.refused_names:
        names = ", ".join(f"«{html.escape(n)}»" for n in result.refused_names)
        them = "его" if len(result.refused_names) == 1 else "их"
        head += NOT_BACK.format(names=names, them=them)
    screen = ended_view(t.db, batch, t.user, head)
    return replace(screen, toast=None)


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
