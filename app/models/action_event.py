import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, func

from app.db.base import Base
from app.db.base_class import BaseClass

UTC = datetime.timezone.utc

PHOTO = "photo"
TEXT = "text"
VOICE = "voice"
BUTTON = "button"
COMMAND = "command"
SAVED = "saved"
CORRECTED = "corrected"
FAILED = "failed"
ACTION_KINDS = (PHOTO, TEXT, VOICE, BUTTON, COMMAND, SAVED, CORRECTED, FAILED)
# What the person did, as against what came of it. A read finishes by itself, later.
PERSON_KINDS = (PHOTO, TEXT, VOICE, BUTTON, COMMAND)

# Everything a label may ever be. No food: a label read from a message, a markup or a
# command's arguments could carry a dish's name, so none of them is ever stored as it
# came. A button is its callback's verb, the text before the first colon.
BUTTON_VERBS = (
    "dh",
    "dy",
    "fav",
    "fb",
    "fl",
    "hw",
    "meal",
    "ml",
    "ok",
    "rm",
    "rt",
    "set",
    "st",
    "usr",
    "wl",
    "wt",
)
# The reply keyboard's labels (``telegram.main_keyboard``); a test holds the two equal.
# «⚖️ Мой вес» left the menu (owner 2026-11-10) but a keyboard already on a phone still
# sends it, so it is still journaled as a tap.
RETIRED_MENU_LABELS = ("⚖️ Мой вес",)
MENU_LABELS = (
    "🍽 Добавить приём пищи",
    "⭐ Избранное",
    "📊 Статистика",
    *RETIRED_MENU_LABELS,
    "⚙️ Настройки",
    "🍲 Блюда",
)
# The registered commands; a test holds the two equal.
COMMANDS = (
    "cancel",
    "consult",
    "diary",
    "feedback",
    "grant_sub",
    "invite",
    "models",
    "reprocess",
    "start",
    "users",
)
# What was read, or that a known dish was logged with no read at all.
READ_LABELS = ("image", "text", "voice", "dish")
# ``record_events`` kinds that are a person setting a record right. Not a delete (it
# says nothing about how the bot read), an automatic reread, or an undo.
CORRECTION_KINDS = (
    "add",
    "grams",
    "groups",
    "join",
    "numbers",
    "portion",
    "qty",
    "remove",
    "rename",
    "replace",
    "split",
    "time",
)
ALLOWED_LABELS = {
    BUTTON: frozenset(BUTTON_VERBS) | frozenset(MENU_LABELS),
    COMMAND: frozenset(COMMANDS),
    SAVED: frozenset(READ_LABELS),
    FAILED: frozenset(READ_LABELS),
    CORRECTED: frozenset(CORRECTION_KINDS),
}


class ActionEvent(Base, BaseClass):
    """What a person did with the bot, and never what about: the owner's own rule is
    no food text here, ever. Bot replies are not stored. Purged after
    ACTION_EVENT_RETENTION_DAYS. No foreign key, as with ``usage_ledger``.
    """

    __tablename__ = "action_events"
    __table_args__ = (Index("ix_action_events_person_at", "telegram_id", "at"),)

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, nullable=False)
    # NOT NULL with a server default: a row can never escape the purge undated.
    at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.datetime.now(UTC),
        index=True,
    )
    kind = Column(String, nullable=False)
    label = Column(String, nullable=True)
