"""Taps inside Telegram HTML widgets (Bot API 10.4 ``widget_query``).

python-telegram-bot 21.9 has no widget support: the ``widget_query`` field of an update
lands in ``update.api_kwargs`` as a plain dict, ``update.effective_user`` is None for it,
and the methods go out raw through ``bot.do_api_request``.

Shapes and parameter names, from the Bot API guide copy (telegram-api-watch widgets.md,
"In the bot" / "Answering" / "Changing the state"):
- update field ``widget_query``: ``id``, ``from`` (User), ``chat`` + ``message_id`` (an
  original in a chat; absent for a message sent via the bot, which has
  ``inline_message_id`` instead), ``chat_instance``, ``data`` (1-64 B), ``widget_id``.
  ``type`` is only named in the guide for a ``sent_transaction`` query; kept as it comes.
- ``answerWidgetQuery``: ``widget_query_id`` + optional ``data`` (<= 1024 B), within 15 s.
- ``editMessageWidgetData``: ``chat_id`` + ``message_id`` + ``chat_data`` (<= 1024 B);
  "Bad Request: message is not modified" counts as done.
ASSUMED: the exact parameter names above are the guide's examples; the per-method pages
(/bots/api/methods/...) were not read.

Errors are told apart by HTTP 400 (python-telegram-bot's BadRequest) plus the method,
never by an error code string: the texts differ from the guide (skill telegram-widgets,
section 6).
"""

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import BaseHandler, ContextTypes

logger = logging.getLogger(__name__)

MAX_DATA_BYTES = 1024  # an answer's data and a chat state, each

Callback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


@dataclass(frozen=True)
class WidgetQuery:
    id: str
    from_id: int
    chat_id: Optional[int]
    message_id: Optional[int]
    data: str
    type: Any = None


_warned_malformed = False


def _malformed(raw: Any) -> None:
    """Once per process: a broken query is not worth a line each."""
    global _warned_malformed
    if not _warned_malformed:
        _warned_malformed = True
        logger.warning(
            "A malformed widget_query was ignored (shape: %s)",
            sorted(raw) if isinstance(raw, dict) else type(raw).__name__,
        )


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def query_of(update: Update) -> Optional[WidgetQuery]:
    """The widget query an update carries; None for any other update or a malformed one.
    Never raises."""
    try:
        raw = (getattr(update, "api_kwargs", None) or {}).get("widget_query")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            _malformed(raw)
            return None
        sender = raw.get("from")
        from_id = _int(sender.get("id")) if isinstance(sender, dict) else None
        query_id = raw.get("id")
        data = raw.get("data", "")
        if from_id is None or not isinstance(query_id, str) or not query_id:
            _malformed(raw)
            return None
        if not isinstance(data, str):
            _malformed(raw)
            return None
        chat = raw.get("chat")
        return WidgetQuery(
            id=query_id,
            from_id=from_id,
            chat_id=_int(chat.get("id")) if isinstance(chat, dict) else None,
            message_id=_int(raw.get("message_id")),
            data=data,
            type=raw.get("type"),
        )
    except Exception as e:  # a log line, never a dropped update
        logger.warning("widget_query not read: %s", type(e).__name__)
        return None


def person_of(update: Update) -> Optional[int]:
    """The telegram id behind an update: the usual sender, else a widget query's ``from``
    (Telegram authenticates it: it cannot be forged)."""
    user = update.effective_user
    if user is not None:
        return user.id
    query = query_of(update)
    return query.from_id if query is not None else None


# -- the router -------------------------------------------------------------------------

_routes: Dict[str, Callback] = {}


def register(prefix: str, callback: Callback) -> None:
    """Route widget queries whose data starts with ``prefix:`` (or is exactly ``prefix``).
    The callback answers the query itself (``answer``)."""
    if prefix in _routes:
        raise ValueError(f"widget prefix {prefix!r} is already registered")
    _routes[prefix] = callback


async def dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One handler for every update (TypeHandler): does nothing for an update that is no
    widget query. An unknown prefix is answered empty; a failing route is logged and the
    tap is answered empty too, since an unanswered one ends in the widget as TIMEOUT."""
    query = query_of(update)
    if query is None:
        return
    prefix = query.data.partition(":")[0]
    callback = _routes.get(prefix)
    if callback is None:
        logger.warning("Widget query with an unknown prefix %r answered empty", prefix)
        await answer(context.bot, query.id, None)
        return
    try:
        await callback(update, context)
    except Exception as e:
        logger.error("Widget route %r failed: %s", prefix, type(e).__name__)
        await answer(context.bot, query.id, None)


class WidgetQueryHandler(BaseHandler):
    """Matches only a widget query. A TypeHandler(Update) matches every update, and PTB
    runs just the first matching handler of a group, so it would shadow the group's other
    handlers (it hid invite_intake in group -3)."""

    def __init__(self) -> None:
        super().__init__(dispatch)

    def check_update(self, update: object) -> bool:
        return isinstance(update, Update) and query_of(update) is not None


# -- raw methods ------------------------------------------------------------------------


def _fits(text: str, what: str) -> bool:
    size = len(text.encode("utf-8"))
    if size > MAX_DATA_BYTES:
        logger.warning(
            "%s refused: %d bytes, the limit is %d", what, size, MAX_DATA_BYTES
        )
        return False
    return True


async def answer(bot, query_id: str, data: Optional[str]) -> bool:
    """``answerWidgetQuery``. True when Telegram took it."""
    if data is not None and not _fits(data, "answerWidgetQuery data"):
        return False
    params: Dict[str, Any] = {"widget_query_id": query_id}
    if data is not None:
        params["data"] = data
    try:
        await bot.do_api_request("answerWidgetQuery", api_kwargs=params)
        return True
    except TelegramError as e:
        # "too late" and "bad id" read the same: nothing to tell apart, nothing to retry.
        logger.warning("answerWidgetQuery failed: %s", type(e).__name__)
        return False


async def edit_data(bot, chat_id: int, message_id: int, chat_data: str) -> bool:
    """``editMessageWidgetData``: the widget's chat state. "Not modified" is success;
    any other failure is logged and False (the caller does not commit its own state)."""
    if not _fits(chat_data, "editMessageWidgetData chat_data"):
        return False
    try:
        await bot.do_api_request(
            "editMessageWidgetData",
            api_kwargs={
                "chat_id": chat_id,
                "message_id": message_id,
                "chat_data": chat_data,
            },
        )
        return True
    except BadRequest as e:  # HTTP 400 on this method
        if "not modified" in str(e).lower():
            return True
        logger.warning("editMessageWidgetData refused (400): %s", str(e)[:200])
        return False
    except TelegramError as e:
        logger.warning("editMessageWidgetData failed: %s", type(e).__name__)
        return False
