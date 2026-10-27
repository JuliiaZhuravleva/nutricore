"""⚖️ Мой вес: the last weighing, the change, the 7-day average; type a new one.

    ⚖️ Мой вес → <b>⚖️ Вес</b> 72,4 кг · вт 28 окт — the history, then
    «Напиши вес, например 72,5»
    72,5 → ✅ Записано: 72,5 кг  [↩️ Отменить]

No goal and no chart yet (Stage 2 adds them). Undo instead of «вы уверены?»:
the record is saved at once and the button takes it back; a stale undo gets a
notice, never an error. Every id is checked against the tapping user.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_body_metric import crud_body_metric
from app.models.body_metric import BodyMetric
from app.models.user import User
from app.schemas.body_metric import BodyMetricCreate
from app.services.favorites import Screen
from app.services.meal_draft import num
from app.services.stats_screen import day_title, local_time

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "wt:"
MIN_KG, MAX_KG = 20.0, 400.0
AVERAGE_DAYS = 7
HISTORY = 5
SOURCE = "bot"  # BodyMetric.metrics["source"] of a weight typed in the chat

ASK = "Напиши вес в килограммах, например <code>72,5</code>."

KG_PATTERN = re.compile(r"^\s*(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:кг|kg)?\.?\s*$", re.I)


def parse_kg(text: str | None) -> float | None:
    """Kilograms from "72,5", "72.5 кг", "72 kg"; None if not a weight."""
    match = KG_PATTERN.match(text or "")
    if not match:
        return None
    kg = float(match.group(1).replace(",", "."))
    return kg if MIN_KG <= kg <= MAX_KG else None


def _kg(value: float) -> str:
    return num(value, 1).replace(".", ",") + " кг"


def _delta(value: float) -> str:
    if abs(value) < 0.05:
        return "без изменений"
    sign = "+" if value > 0 else "−"
    return f"{sign}{num(abs(value), 1).replace('.', ',')} кг"


def _btn(text: str, *parts) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text, callback_data=CALLBACK_PREFIX + ":".join(map(str, parts))
    )


# --- screens ----------------------------------------------------------------


def view_screen(
    weighings: list[BodyMetric],
    week: list[BodyMetric],
    notice: str | None = None,
) -> Screen:
    """``weighings`` latest first (the last few); ``week`` the last 7 days'."""
    lines = ["<b>⚖️ Вес</b>"]
    if notice:
        lines.append(notice)
    lines.append("")
    if not weighings:
        lines += ["Записей пока нет.", "", ASK]
        return Screen("\n".join(lines), None)
    latest = weighings[0]
    when = day_title(local_time(latest.timestamp).date())
    lines.append(f"<b>{_kg(latest.weight)}</b> · {when}")
    if len(weighings) > 1:
        lines.append(f"С прошлого раза: {_delta(latest.weight - weighings[1].weight)}")
    if len(week) > 1:
        average = sum(m.weight for m in week) / len(week)
        lines.append(
            f"Среднее за {AVERAGE_DAYS} дней: {_kg(average)} ({len(week)} зап.)"
        )
    if len(weighings) > 1:
        lines.append("")
        for metric in weighings[1:]:
            when = local_time(metric.timestamp)
            lines.append(f"{day_title(when.date())} · {_kg(metric.weight)}")
    lines += ["", ASK]
    return Screen("\n".join(lines), None)


def saved_screen(metric: BodyMetric, previous: BodyMetric | None) -> Screen:
    text = f"✅ <b>Записано:</b> {_kg(metric.weight)}"
    if previous is not None:
        text += f"\nС прошлого раза: {_delta(metric.weight - previous.weight)}"
    return Screen(
        text, InlineKeyboardMarkup([[_btn("↩️ Отменить", "u", metric.id)]]), "Записано"
    )


# --- operations -------------------------------------------------------------


def _user(db, telegram_id: int, username: str | None = None, create=False):
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user is None and create:
        user = User(telegram_id=telegram_id, username=username)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def open_view(
    db, telegram_id: int, notice: str | None = None, now: datetime | None = None
) -> Screen:
    user = _user(db, telegram_id)
    if user is None:
        return view_screen([], [], notice)
    since = (now or datetime.now(UTC)) - timedelta(days=AVERAGE_DAYS)
    weighings = crud_body_metric.latest_weights(db, user.id, HISTORY)
    week = crud_body_metric.get_user_weights_since(db, user.id, since)
    return view_screen(weighings, week, notice)


def record(db, telegram_id: int, kg: float, username: str | None = None) -> Screen:
    """Save a typed weight now; the screen offers the undo."""
    user = _user(db, telegram_id, username, create=True)
    last = crud_body_metric.latest_weights(db, user.id, 1)
    metric = crud_body_metric.create(
        db,
        BodyMetricCreate(
            timestamp=datetime.now(UTC), weight=kg, metrics={"source": SOURCE}
        ),
        user.id,
    )
    return saved_screen(metric, last[0] if last else None)


def handle_callback(db, telegram_id: int, data: str) -> Screen:
    parts = data[len(CALLBACK_PREFIX) :].split(":")
    if len(parts) == 2 and parts[0] == "u" and parts[1].isdigit():
        user = _user(db, telegram_id)
        metric = crud_body_metric.get(db, int(parts[1])) if user else None
        if (
            metric is None
            or metric.user_id != user.id
            or (metric.metrics or {}).get("source") != SOURCE
        ):
            return open_view(db, telegram_id, "<i>Эта запись уже отменена.</i>")
        kg = metric.weight
        crud_body_metric.remove(db, metric.id)
        screen = open_view(db, telegram_id, f"↩️ Отменено: {_kg(kg)} не записано.")
        return Screen(screen.text, screen.markup, "Отменено")
    logger.warning("weight: unknown callback data %r", data)
    return open_view(db, telegram_id)
