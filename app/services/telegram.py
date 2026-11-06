import asyncio
import base64
import enum
import logging
import os
import re
import time
import traceback
import warnings
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import httpx
from sqlalchemy.exc import SQLAlchemyError
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    MessageOriginUser,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    ReplyParameters,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from app.core.config import settings
from app.crud.crud_access_grant import crud_access_grant
from app.crud.crud_feedback import crud_feedback
from app.crud.crud_subscription import crud_subscription
from app.db.session import SessionLocal
from app.models import action_event
from app.models import feedback as feedback_model
from app.services import (
    action_journal,
    ai_errors,
    capture,
    close_row,
    custom_emoji,
    dishes,
    explain_screen,
    favorites,
    feedback_inbox,
    feedback_screen,
    food_lists,
    image_clean,
)
from app.services import inbound_message_service as inbound_msg
from app.services import (
    invites,
    meal_card,
    meal_draft,
    meal_items,
    owner_buttons,
    people_screen,
    portion_suggestions,
    reminders,
    routines,
    settings_screen,
    stats_screen,
    voice,
    weight_screen,
    welcome_screen,
)
from app.services.access_control import (
    NO_ACCESS,
    TERM_ENDED,
    access_gate,
    admin_required,
    may_receive,
)
from app.services.admin_notify import deliver_to_admins, deliver_to_one, notify_admins
from app.services.ai_call_log_service import analyze_and_log
from app.services.attribution import attribute_to
from app.services.card_delivery import CardDelivery
from app.services.consult_service import consult_service
from app.services.loader import Loader
from app.services.model_selection import (
    apply_persisted_model,
    persist_model,
    persist_task_model,
)
from app.services.model_watch import render_status, run_model_watch
from app.services.openai_service import ModelUnavailableError, get_openai_service
from app.services.picture_renderer import PictureRenderer
from app.services.product_lookup_service import (
    _parse_portion_grams,
    has_usable_macros,
    parse_nutrition,
    resolve_meal_nutrition,
)
from app.services.usage_meter import Meter, metering

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
)
logger = logging.getLogger(__name__)

# Light anti-spam for /consult: min seconds between a user's hub calls.
CONSULT_COOLDOWN_SECONDS = 3
# How long «➕» or «✏️» waits for the typed routine name.
ROUTINE_NAME_WAIT = timedelta(minutes=10)
# The same for a food typed on the lists' screens.
FOOD_NAME_WAIT = timedelta(minutes=10)
_last_consult: Dict[int, datetime] = {}

# Max inbound messages re-analyzed per /reprocess call — a cap on OpenAI spend
# per owner invocation. If more are pending, the owner just runs it again.
REPROCESS_BATCH_LIMIT = 20

# A used-up AI balance seen by someone other than the owner: tell the owner at
# most this often per provider (in this process), not once per meal.
CREDIT_ALERT_EVERY_SECONDS = 3600
_credit_alerted: Dict[str, datetime] = {}

# Reads running at once (section 5.2), and the tasks of them that are running.
READS_AT_ONCE = 3
_meal_reads: set = set()
# The meals a task is reading now, with their diagnostic row. A sweep leaves these be.
_live_reads: Dict[int, Optional[int]] = {}
_read_holders: Dict[int, asyncio.Task] = {}  # which task holds a meal in _live_reads

SWEEP_EVERY_SECONDS = 300
SHOW_TRIES = 3  # a line, to Telegram: past a moment of it, the sweep owes the line
SHOW_PAUSE_SECONDS = 1.0
SHOW_WAIT_MAX_SECONDS = 5.0  # a flood wait longer than this is not waited out here
REPLAY_LIMIT = REPROCESS_BATCH_LIMIT  # one start's pending reads; the rest wait
SWEEP_TASK = "meal_sweep"
REPLAY_DONE = "meal_replay_done"
INVITE_WAIT_SECONDS = 600  # how long a forward or a contact means an invitation
_INVITE_UNTIL = "invite_until"  # in ``user_data``
FEEDBACK_WAIT_SECONDS = 900  # how long the next message means the owner, not the diary
FEEDBACK_ALBUM_SECONDS = 60  # the rest of an album, which Telegram sends one by one
_FEEDBACK_UNTIL = "feedback_until"  # in ``user_data``
_FEEDBACK_PROMPT = "feedback_prompt"  # the screen that asked: it loses its buttons
_FEEDBACK_ALBUM = "feedback_album"  # (media_group_id, until)

# Conversation states
(
    CHOOSING_ACTION,
    DISAMBIGUATING_PRODUCT,  # future: multiple-candidate product picker (A8/A9)
    ENTERING_WEIGHT,
    ENTERING_ROUTINE_NAME,
    ENTERING_FOOD_NAME,
) = range(5)

# The keyboard of the old padlock screen: it is gone, and phones still carry its labels.
_OLD_LOCK_LABELS = ("❓ О боте", "💎 Получить подписку", "📱 Связаться с админом")

# Keyboard layouts
main_keyboard = ReplyKeyboardMarkup(
    [
        ["🍽 Добавить приём пищи", "⭐ Избранное"],
        ["📊 Статистика", "⚖️ Мой вес"],
        ["⚙️ Настройки", "🍲 Блюда"],
    ],
    resize_keyboard=True,
)

# What a reply keyboard sends: never the answer to a typed slot.
# What a menu button sends: it ends the wait for a feedback message at once. A bare
# «Сейчас» or «Обед» is no menu button, and may be just what someone has to say.
_FEEDBACK_LEAVES = frozenset(action_event.MENU_LABELS) | {
    "🍽 Добавить прием пищи",  # the label keyboards already on phones still carry
    *_OLD_LOCK_LABELS,
}
_KEYBOARD_LABEL = re.compile(
    "🍽 Добавить при[её]м пищи|⭐ Избранное|📊 Статистика|⚖️ Мой вес|⚙️ Настройки"
    "|🍲 Блюда"
    "|❓ О боте|💎 Получить подписку|📱 Связаться с админом|Отмена"
    "|👤 Выбрать человека|✖️ Отмена"
    "|Сейчас|Завтрак|Обед|Ужин|Назад"
)


class TelegramService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TelegramService, cls).__new__(cls)
            # Shared process-wide singleton (see openai_service.get_openai_service):
            # the resolution pipeline uses the same instance, so a runtime model
            # switch (TD-005) is visible everywhere without a circular import.
            cls._instance.openai_service = get_openai_service()
        return cls._instance

    def __init__(self):
        # The initialization is done in __new__
        pass


# Create a single instance to be used throughout the application
telegram_service = TelegramService()


async def check_subscription(telegram_id: int) -> bool:
    """Check if user has active subscription.

    Admins (the bot owner) always pass — this is a personal tool and the owner
    should never be gated by the subscription check.
    """
    if telegram_id in settings.admin_ids:
        return True
    db = SessionLocal()
    try:
        return crud_subscription.is_active_subscription(db, telegram_id)
    finally:
        db.close()


def refusal(telegram_id: int) -> str:
    """The one line for someone the gate let through whose diary is shut."""
    with SessionLocal() as db:
        had_a_term = crud_subscription.get_by_telegram_id(db, telegram_id) is not None
    return TERM_ENDED if had_a_term else NO_ACCESS


def subscription_required(func):
    """Decorator to check subscription before executing function"""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_subscription(update.effective_user.id):
            await update.message.reply_text(refusal(update.effective_user.id))
            return CHOOSING_ACTION
        return await func(update, context)

    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await _note_first_open(context, update.effective_user.id)
    if not await check_subscription(update.effective_user.id):
        await update.message.reply_text(refusal(update.effective_user.id))
        return CHOOSING_ACTION
    try:
        with SessionLocal() as db:
            first_run = welcome_screen.opens_welcome(db, update.effective_user.id)
    except Exception as e:
        logger.error("First run not read: %s", type(e).__name__)
        first_run = False
    if first_run:
        await _reply_screen(update, welcome_screen.welcome())
        return CHOOSING_ACTION
    await update.message.reply_text("Что делаем?", reply_markup=main_keyboard)
    return CHOOSING_ACTION


async def _note_first_open(context, telegram_id: int) -> None:
    """Tells the owner, once and without a sound, that an invited person came. Nothing
    here may cost that person their /start."""
    try:
        with SessionLocal() as db:
            grant = crud_access_grant.mark_opened(db, telegram_id)
            name = grant.display_name if grant is not None else None
    except Exception as e:
        logger.error("First open not recorded: %s", type(e).__name__)
        return
    if name is None:
        return
    for admin_id in settings.admin_ids:
        try:
            await context.bot.send_message(
                admin_id, f"👋 {name} теперь в боте.", disable_notification=True
            )
        except TelegramError as e:
            logger.warning("First open not told to the owner: %s", type(e).__name__)


def _new_meal_hint() -> dict:
    """M9a: no state, no steps, the next thing sent is the meal."""
    favorite = InlineKeyboardButton("⭐ Избранное", callback_data="fav:p:0")
    all_dishes = InlineKeyboardButton("🍲 Блюда", callback_data="dh:l:0")
    return {
        "text": capture.M9A_NEW_MEAL,
        "parse_mode": ParseMode.HTML,
        "reply_markup": InlineKeyboardMarkup([[favorite, all_dishes]]),
    }


@subscription_required
async def add_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """«🍽 Добавить приём пищи» in the menu."""
    await update.message.reply_text(**_new_meal_hint())
    return CHOOSING_ACTION


async def add_meal_from_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The same on an empty day card. The card stays as it is."""
    query = update.callback_query
    if not await check_subscription(update.effective_user.id):
        await query.answer(TERM_ENDED, show_alert=True)
        return None
    await query.answer()
    await update.effective_chat.send_message(**_new_meal_hint())
    return None


# parse_nutrition is imported from product_lookup_service (where the pipeline
# logic lives).  Keep a private alias so existing tests referencing
# tg._parse_nutrition continue to work without change.
_parse_nutrition = parse_nutrition


def _image_data_url(image_bytes) -> str:
    """base64 data URL for OpenAI vision — never Telegram's token-bearing file URL
    (api.telegram.org/file/bot<TOKEN>/…), and no dependency on OpenAI fetching it."""
    clean = image_clean.clean(bytes(image_bytes))
    mime = image_clean.mime_of(clean)
    return f"data:{mime};base64," + base64.b64encode(clean).decode()


def _schedule_personal_food_save(
    *,
    user_id: int,
    meal_id: int,
    nutrition: dict,
    resolution_signals: dict | None,
    resolution_source: str | None,
    canonical_name: str | None = None,
) -> None:
    """Enqueue the embed+upsert Celery task for the confirmed food (B4 / ADR-0003).

    Fire-and-forget: the Celery task writes to personal_foods in the background so
    the confirm reply is not delayed.  All failures are logged and swallowed — the
    personal DB is an optimisation layer and must never block the confirm reply.

    Canonical-name priority (ADR-0003 §B4 contract):
      1. saved_food_name — already in personal DB (saved_rag path)
      2. product_name   — OFF-matched product name (barcode_off / name_off path)
      3. first food     — vision / text analysis result
    ``canonical_name``, when given, is the dish the meal was linked to at save, and
    outranks them: a barcode found under another name must not make a second row.

    A plate's numbers belong to no one of its foods, so they teach nothing (H6).
    """
    try:
        signals = resolution_signals or {}

        if not meal_items.holds_one_dish(nutrition, signals):
            logger.debug(
                "_schedule_personal_food_save: a plate (user_id=%d meal_id=%d)"
                " — its numbers are not one food's",
                user_id,
                meal_id,
            )
            return

        # Canonical name extraction
        canonical_name = (
            canonical_name
            or signals.get("saved_food_name")
            or signals.get("product_name")
            or (nutrition.get("foods") or [None])[0]
        )
        if not canonical_name or not str(canonical_name).strip():
            logger.warning(
                "_schedule_personal_food_save: no canonical_name found"
                " (user_id=%d meal_id=%d) — skipping",
                user_id,
                meal_id,
            )
            return
        canonical_name = str(canonical_name).strip()

        # Barcode — only present in barcode_off signals (ADR-0003 §B4)
        barcode = signals.get("barcode_raw") or None  # empty str → None

        # Brand — every OFF-backed strategy already resolves it into signals, and
        # personal_foods has had a brand column, a schema field and CRUD support
        # from the start; only this hop was missing, so the column was NULL for
        # every row ever written. It is what separates a store brand from a name
        # brand with the same generic product name.
        brand = signals.get("brand") or None

        # Per-100g macro computation:
        #   Image paths: portion_grams pre-computed in resolution_signals.
        #   Text paths:  attempt to parse grams from nutrition["portion"] string.
        #   Neither available: the numbers are absolute for an UNKNOWN weight, so
        #   no per-100g basis exists — see the guard below.
        portion_grams = signals.get("portion_grams")
        if portion_grams is None:
            portion_grams = _parse_portion_grams(nutrition)

        if portion_grams and portion_grams > 0:
            # Covers the per-100g sentinel too: every strategy that cannot scale
            # emits "100г", which parses back to 100.0 → factor 1.0.
            factor = 100.0 / portion_grams
        else:
            # The old code used factor=1.0 here, which stored the macros of a WHOLE
            # portion in the per_100g_* columns. Those rows are then served back by
            # SavedFoodRAGStrategy at medium confidence under "⭐ из твоей базы" —
            # a wrong number that outlives the meal that created it. Learn nothing
            # rather than learn a lie.
            logger.warning(
                "_schedule_personal_food_save: no per-100g basis "
                "(user_id=%d meal_id=%d) — not saving to the personal DB",
                user_id,
                meal_id,
            )
            return

        def _to_per100g(v: object) -> float | None:
            try:
                return round(float(v) * factor, 2) if v is not None else None  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        per_100g_calories = _to_per100g(nutrition.get("calories"))
        if not has_usable_macros(per_100g_calories):
            # SavedFoodRAGStrategy refuses a row with no calories, so saving one
            # would create a personal_foods row that can never be served — and
            # that still counts as "learned" for times_used.
            logger.warning(
                "_schedule_personal_food_save: no usable calories "
                "(user_id=%d meal_id=%d) — not saving to the personal DB",
                user_id,
                meal_id,
            )
            return
        per_100g_proteins = _to_per100g(nutrition.get("protein"))
        per_100g_fats = _to_per100g(nutrition.get("fats"))
        per_100g_carbs = _to_per100g(nutrition.get("carbs"))

        # Lazy import: avoids a circular-import risk at module load time and lets
        # tests mock .delay without needing a live Celery broker.
        from celery_app.tasks.personal_food import embed_and_save_personal_food

        embed_and_save_personal_food.delay(
            user_id=user_id,
            canonical_name=canonical_name,
            meal_id=meal_id,
            resolution_source=resolution_source,
            barcode=barcode,
            brand=brand,
            per_100g_calories=per_100g_calories,
            per_100g_proteins=per_100g_proteins,
            per_100g_fats=per_100g_fats,
            per_100g_carbs=per_100g_carbs,
        )
        logger.debug(
            "_schedule_personal_food_save: enqueued for user_id=%d meal_id=%d",
            user_id,
            meal_id,
        )
    except Exception:
        logger.warning(
            "_schedule_personal_food_save: failed to enqueue task"
            " (user_id=%d meal_id=%d) — ignored",
            user_id,
            meal_id,
            exc_info=True,
        )


async def _analyze_and_parse(
    *, kind: str, payload: str, input_ref: str, telegram_id: int | None
) -> dict:
    """Run the OpenAI analysis for `kind`, record it to ai_call_logs, and return
    the parsed nutrition.

    The single analyze pipeline shared by the background read (``_read_one``)
    and the replay (``reprocess``) so both go through the same code AND the same
    audit trail. Raises ``ModelUnavailableError`` on a dead model and ``ValueError``
    on a malformed response (both handled by the callers).
    """
    svc = telegram_service.openai_service
    if kind == "image":
        coro = svc.analyze_food_image(payload)
    elif kind == "voice":
        coro = svc.analyze_food_entry(payload, spoken=True)
    else:
        coro = svc.analyze_food_entry(payload)
    return await analyze_and_log(
        coro,
        kind=kind,
        input_ref=input_ref,
        telegram_id=telegram_id,
        model=svc.model_for("vision" if kind == "image" else "text"),
        parse=_parse_nutrition,
    )


async def _download_image(bot, file_id: str) -> str:
    file = await bot.get_file(file_id)
    return _image_data_url(await file.download_as_bytearray())


async def _credit_alert(bot, err, job: capture.Read) -> None:
    """The AI balance or spend limit is used up (``ai_errors``): the person sees only
    M7. The owner learns where to top up, in a message of their own, at most once an
    hour per provider when it was someone else's meal."""
    provider, where = ai_errors.billing(err)
    head = f"💳 У {provider} закончился баланс или лимит трат на ИИ"
    try:
        if job.telegram_id in settings.admin_ids:
            await bot.send_message(
                job.chat_id,
                f"{head} — разобрать еду сейчас не получится, повтор не поможет.\n"
                f"Пополни баланс или подними лимит: {where}.\n"
                "Сообщение сохранено: после пополнения отправь /reprocess.",
            )
            return
        now = datetime.now(UTC)
        last = _credit_alerted.get(provider)
        if last and (now - last).total_seconds() < CREDIT_ALERT_EVERY_SECONDS:
            return
        if await notify_admins(
            f"{head} — у пользователя {job.telegram_id} не разобралась еда.\n"
            f"Пополни баланс или подними лимит: {where}, потом /reprocess."
        ):
            _credit_alerted[provider] = now
    except TelegramError as e:
        logger.warning("Credit alert not sent: %s", type(e).__name__)


async def _offer_model_choice(
    application: Application, job: capture.Read, err: ModelUnavailableError
) -> None:
    """A model went missing: the owner gets a picker, and a pick reads the meal again.
    Only an admin picks (ADR-0004); ``OpenAIService`` has told them already when it
    was someone else's meal. In memory, like the conversation it replaces: the meal
    stands either way, and /reprocess reads it after a restart."""
    if job.telegram_id not in settings.admin_ids:
        return
    svc = telegram_service.openai_service
    task = err.task or ("vision" if job.kind == capture.IMAGE else "text")
    # Replace the model where it was configured: a task with its own pick gets a
    # new pick; a task on the default replaces the default, so one dead default
    # model takes one choice, not one per task.
    model_task = None if err.task is None or svc.runs_on_default(task) else task
    models = await svc.list_candidates(task, exclude=err.model)
    stash = application.user_data[job.telegram_id]
    stash["pending_meal"] = job.meal_id
    stash["model_task"] = model_task
    stash["model_choices"] = models
    await application.bot.send_message(
        job.chat_id,
        f"⚠️ Модель «{err.model}» недоступна (устарела, удалена или нет ключа).\n"
        "Выбери другую — я запомню её и сразу повторю анализ:",
        reply_markup=ReplyKeyboardMarkup(
            [[m] for m in models] + [["Отмена"]], resize_keyboard=True
        ),
    )


async def _model_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """The answer to the picker, ahead of the router; True if this message was it."""
    stash, text = context.user_data, update.message.text
    choices = stash.get("model_choices")
    if not choices or (text not in choices and text != "Отмена"):
        return False
    meal_id = stash.pop("pending_meal", None)
    task = stash.pop("model_task", None)
    stash.pop("model_choices", None)
    if text == "Отмена":
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard)
        return True
    telegram_service.openai_service.set_model(text, task=task)
    if task is None:
        persist_model(text)
    else:
        persist_task_model(task, text)
    await update.message.reply_text(
        f"✅ Модель обновлена: {text}. Повторяю анализ…", reply_markup=main_keyboard
    )
    with SessionLocal() as db:
        job = capture.read_again(db, meal_id) if meal_id else None
    if job is not None:
        _start_read(context.application, job)
    return True


async def _tries(send):
    """A moment of Telegram is not worth a lost line: a timeout, a network error or a
    short flood wait is tried again. A refusal is an answer, and it comes out at once.
    """
    for left in range(SHOW_TRIES - 1, -1, -1):
        try:
            return await send()
        except BadRequest:
            raise
        except RetryAfter as e:
            wait = float(getattr(e, "retry_after", SHOW_WAIT_MAX_SECONDS + 1))
            if not left or wait > SHOW_WAIT_MAX_SECONDS:
                raise
            await asyncio.sleep(wait)
        except NetworkError:
            if not left:
                raise
            await asyncio.sleep(SHOW_PAUSE_SECONDS)


def _is_not_modified(e: BadRequest) -> bool:
    return "not modified" in str(e).casefold()


async def _edit_line(bot, chat_id: int, message_id: int, line: capture.Line) -> None:
    await _tries(
        lambda: bot.edit_message_text(
            line.text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=line.markup,
        )
    )


async def _send_unsent(bot, job: capture.Read, line: capture.Line) -> None:
    """The first line never went out, so nothing can be edited: the answer goes as a
    message of its own, under what was sent, and is the meal's line from now on."""
    with SessionLocal() as db:
        message_id = capture.message_of(db, job.source_id)
    quote = None
    if message_id is not None:
        quote = ReplyParameters(message_id=message_id, allow_sending_without_reply=True)
    try:
        sent = await _tries(
            lambda: bot.send_message(
                job.chat_id,
                line.text,
                parse_mode=ParseMode.HTML,
                reply_markup=line.markup,
                disable_notification=True,
                reply_parameters=quote,
            )
        )
    except TelegramError as e:
        logger.warning("Meal line not sent: %s", type(e).__name__)
        return
    _map_line(line, sent, "line")


async def _show(bot, job: capture.Read, line: Optional[capture.Line]) -> None:
    """The result into the same line. A refusal costs nothing, the record is written.
    Telegram being away is another matter: the line would stay on «читаю», so it is owed
    and the sweep puts it right."""
    if line is None:
        return
    if job.line_id is None:
        if job.unsent:
            await _send_unsent(bot, job, line)
        return
    try:
        await _edit_line(bot, job.chat_id, job.line_id, line)
    except BadRequest as e:
        if _is_not_modified(e):
            return
        logger.warning("Meal line not edited: %s", type(e).__name__)
        owed = False
    except (RetryAfter, NetworkError) as e:
        logger.warning("Meal line not edited, owed: %s", type(e).__name__)
        owed = True
    except TelegramError as e:
        logger.warning("Meal line not edited: %s", type(e).__name__)
        owed = False
    else:
        return
    with SessionLocal() as db:
        capture.line_not_shown(db, job, line, owed=owed)


def _may_hear(telegram_id: int) -> bool:
    """For what the bot says unasked: someone whose door was closed hears nothing more."""
    with SessionLocal() as db:
        return may_receive(db, telegram_id)


async def _show_owed(application: Application) -> None:
    """The lines Telegram did not take when their read ended. Nothing is read here. One
    that cannot be shown does not hold back the next."""
    with SessionLocal() as db:
        owed = capture.owed_lines(db, _live_reads)
    for one in owed:
        try:
            if not _may_hear(one.chat_id):
                with SessionLocal() as db:
                    capture.owed_settled(db, one.chat_id, one.message_id)
                continue
            try:
                await _edit_line(application.bot, one.chat_id, one.message_id, one.line)
            except (RetryAfter, NetworkError) as e:
                if isinstance(e, BadRequest):
                    logger.warning("Owed line refused: %s", type(e).__name__)
                else:
                    logger.warning("Owed line still owed: %s", type(e).__name__)
                    continue
            except TelegramError as e:
                logger.warning("Owed line refused: %s", type(e).__name__)
            with SessionLocal() as db:
                capture.owed_settled(db, one.chat_id, one.message_id)
        except Exception as e:
            logger.error("Owed line failed: %s", type(e).__name__)


async def _retouch(bot, chat_id: int, retouch: Optional[meal_card.Retouch]) -> None:
    """The original line, to the meal's new list. Silent, and best effort: it may be
    too old to edit, and the person was answered anyway."""
    if retouch is None:
        return
    try:
        await bot.edit_message_text(
            retouch.text,
            chat_id=chat_id,
            message_id=retouch.message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=retouch.markup,
        )
    except TelegramError as e:
        logger.warning("Original line not edited: %s", type(e).__name__)


async def _reading(bot, job: capture.Read) -> tuple:
    """(nutrition, resolution) of the meal's source."""
    if job.kind == capture.IMAGE:
        # Sent to the model as a data URL: Telegram's file URL embeds the bot token.
        payloads, refused = [], None
        for file_id in job.file_ids or (job.file_id,):
            try:
                payloads.append(await _download_image(bot, file_id))
            except BadRequest as e:
                # One photo of an album gone: the others are still the meal.
                refused = e
                logger.warning("Meal photo gone: %s", type(e).__name__)
        if not payloads:
            raise capture.DeadFile() from refused
        album = {"more_images": tuple(payloads[1:])} if payloads[1:] else {}
        resolution = await resolve_meal_nutrition(
            payloads[0],
            telegram_id=job.telegram_id,
            input_ref=job.file_id,
            caption=job.text,
            **album,
        )
        return resolution.nutrition, resolution
    if job.kind == capture.TEXT:
        nutrition = await _analyze_and_parse(
            kind="text",
            payload=job.text,
            input_ref=job.text,
            telegram_id=job.telegram_id,
        )
        return nutrition, None
    words = await _voice_words(bot, job)
    nutrition = await _analyze_and_parse(
        kind="voice",
        payload=words,
        # What was said goes in no log: the row names the file.
        input_ref=job.file_id,
        telegram_id=job.telegram_id,
    )
    return nutrition, None


async def _voice_bytes(bot, job: capture.Read) -> bytes:
    """Kept first, before anyone listens; a read again takes the copy that is here."""
    with SessionLocal() as db:
        name = capture.audio_of(db, job.source_id)
    audio = await voice.kept(name)
    if audio is not None:
        return audio
    file = await bot.get_file(job.file_id)
    audio = bytes(await file.download_as_bytearray())
    name = await voice.keep(job.source_id, audio)
    if name:
        with SessionLocal() as db:
            capture.audio_kept(db, job.source_id, name)
    return audio


async def _voice_words(bot, job: capture.Read) -> str:
    """What the model is to read. Words heard once are not listened to or paid for
    again. The log says who answered and how fast, and no word of it."""
    with SessionLocal() as db:
        transcript = meal_card.heard_of(db, job.meal_id)
    if transcript is None:
        audio = await _voice_bytes(bot, job)
        started = time.monotonic()
        heard = await voice.hear(
            audio,
            paid=telegram_service.openai_service.transcribe,
            local_only=job.automatic,
        )
        took_s = round(time.monotonic() - started, 1)
        transcript = heard.text
        with SessionLocal() as db:
            duration_s = capture.store_heard(db, job, heard, took_s)
        logger.info(
            "Voice heard: meal=%s backend=%s duration_s=%s took_s=%s",
            job.meal_id,
            heard.backend,
            duration_s,
            took_s,
        )
    with SessionLocal() as db:
        return capture.spoken_words(db, job, transcript)


def _model_of(job: capture.Read) -> Optional[str]:
    task = "vision" if job.kind == capture.IMAGE else "text"
    return telegram_service.openai_service.model_for(task)


async def _say_not_yet(bot, job: capture.Read) -> None:
    """The toast of a 🔁 that failed. Best effort: that query was answered with «Читаю»
    and Telegram may refuse a second answer; the line back on M7 is what says it."""
    if job.tap is None:
        return
    try:
        await bot.answer_callback_query(job.tap, capture.AGAIN_FAILED_TOAST)
    except TelegramError as e:
        logger.info("Second toast refused: %s", type(e).__name__)


async def _read_one(bot, job: capture.Read, meter: Meter) -> tuple:
    """One read of a meal, booked to the person whose meal it is, whoever started it: a
    sweep with nobody behind it, or the owner's /reprocess."""
    with attribute_to(job.telegram_id):
        return await _read_and_show(bot, job, meter)


def _note_failed(job: capture.Read) -> None:
    # A retry nobody asked for is no new failure: the person met this one already.
    if not job.automatic:
        action_journal.note(job.telegram_id, action_event.FAILED, job.kind)


async def _read_and_show(bot, job: capture.Read, meter: Meter) -> tuple:
    """(read, the dead model's error if that is why not). Nothing here logs a message
    body: only the class of what went wrong."""
    try:
        nutrition, resolution = await _reading(bot, job)
    except voice.NoSpeech:
        with SessionLocal() as db:
            done = capture.read_no_speech(db, job)
        await _tell_read(bot, job, done.line)
        return False, None
    except Exception as e:
        error = type(e).__name__
        logger.error("Meal %s not read: %s", job.meal_id, error)
        _note_failed(job)
        inbound_msg.mark_failed(job.inbound_id, error)
        with SessionLocal() as db:
            done = capture.read_failed(
                db,
                job,
                error,
                status=capture.failure_status(e),
                model=_model_of(job),
            )
        await _tell_read(bot, job, done.line, not_yet=True)
        refusal = ai_errors.out_of_credit(e)
        if refusal is not None:
            await _credit_alert(bot, refusal, job)
        return False, e if isinstance(e, ModelUnavailableError) else None
    # Recorded now, independent of the line below: a Telegram failure must not make
    # /reprocess pay for this again.
    inbound_msg.mark_analyzed(job.inbound_id, nutrition)
    try:
        with SessionLocal() as db:
            cost = (
                meter.line()
                if settings_screen.shows_costs(db, job.telegram_id)
                else None
            )
            done = capture.read_done(
                db, job, nutrition, resolution, cost=cost, model=_model_of(job)
            )
    except Exception as e:
        # The answer is paid for and no retry would read it differently: kept, and the
        # line says it did not work. The class and the place only: a validation error's
        # text quotes the answer.
        error = type(e).__name__
        place = traceback.extract_tb(e.__traceback__)[-1]
        logger.error(
            "Meal %s not written after its read: %s at %s:%s",
            job.meal_id,
            error,
            os.path.basename(place.filename),
            place.lineno,
        )
        _note_failed(job)
        with SessionLocal() as db:
            done = capture.read_failed(
                db,
                job,
                error,
                status=capture.FAILED,
                model=_model_of(job),
                output=nutrition,
            )
        await _tell_read(bot, job, done.line, not_yet=True)
        return False, None
    if done.learn:
        # A read with no food in it, or of a meal gone meanwhile, has nothing to learn.
        action_journal.note(job.telegram_id, action_event.SAVED, job.kind)
        _schedule_personal_food_save(**done.learn)
    await _tell_read(
        bot, job, done.line, retouch=done.retouch, recorded=bool(done.learn)
    )
    return True, None


async def _tell_read(
    bot,
    job: capture.Read,
    line: Optional[capture.Line],
    *,
    not_yet: bool = False,
    retouch: Optional[meal_card.Retouch] = None,
    recorded: bool = False,
) -> None:
    """All a finished read says to the person, and only if the door is still open: it may
    have closed while the model was thinking. The line goes on the sweep's list, which
    asks the door again, so a moment of a database that cannot say costs no line."""
    try:
        may_hear = _may_hear(job.telegram_id)
    except Exception as e:
        logger.error("Door not read after a read: %s", type(e).__name__)
        may_hear = False
    if not may_hear:
        logger.info("Meal %s read, and its person hears nothing", job.meal_id)
        if line is not None:
            with SessionLocal() as db:
                capture.line_not_shown(db, job, line, owed=True)
        return
    await _show(bot, job, line)
    if not_yet:
        await _say_not_yet(bot, job)
    await _retouch(bot, job.chat_id, retouch)
    if recorded:
        await _first_record_hint(bot, job)
    else:
        await _menu_once(bot, job.chat_id, job.telegram_id)


async def _menu_once(bot, chat_id: int, telegram_id: int) -> None:
    """The main keyboard for someone who skipped the welcome's question and whose first
    message recorded nothing, so no hint will bring it. It can cost the meal nothing."""
    try:
        with SessionLocal() as db:
            words = welcome_screen.take_menu(db, telegram_id)
        if words is not None:
            await bot.send_message(chat_id, words, reply_markup=main_keyboard)
    except Exception as e:
        logger.warning("Menu not given: %s", type(e).__name__)


async def _welcome_unasked(update: Update, context) -> None:
    """An invited person who wrote before ever pressing /start: the food goes on as ever,
    and the first message of the bot still says who sees what. It can cost the meal
    nothing; one Telegram did not take stays owed."""
    message, user = update.message, update.effective_user
    try:
        with SessionLocal() as db:
            owed = welcome_screen.owed_unasked(db, user.id)
    except Exception as e:
        logger.error("First run not read: %s", type(e).__name__)
        return
    if not owed:
        return
    await _note_first_open(context, user.id)
    screen = welcome_screen.welcome()
    try:
        await message.reply_text(
            screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
        )
    except TelegramError as e:
        logger.warning("Welcome not sent: %s", type(e).__name__)
        with SessionLocal() as db:
            welcome_screen.still_owed(db, user.id)


async def _first_record_hint(bot, job: capture.Read) -> None:
    """One line after the first record of someone who came through the welcome, with the
    main keyboard for whoever wrote before answering its question."""
    try:
        with SessionLocal() as db:
            hint = welcome_screen.take_hint(db, job.telegram_id)
        if hint is None:
            return
        await bot.send_message(job.chat_id, hint, reply_markup=main_keyboard)
        with SessionLocal() as db:
            line = welcome_screen.zone_line(db, job.telegram_id, datetime.now(UTC))
        if line is not None:
            await bot.send_message(
                job.chat_id,
                line.text,
                reply_markup=line.markup,
                disable_notification=True,
            )
    except Exception as e:
        logger.warning("First record hint not sent: %s", type(e).__name__)


async def _album_settles(job: capture.Read) -> None:
    """Until the album's last photo is old enough. Outside the gate: a wait holds no
    other meal's read back. Nothing waits for a photo that came alone."""
    if job.kind != capture.IMAGE:
        return
    while True:
        with SessionLocal() as db:
            left = capture.album_wait(db, job.meal_id)
        if left <= 0:
            return
        await asyncio.sleep(left)


async def _album_went_on(bot, job: capture.Read) -> Optional[capture.Read]:
    """A photo came during the read: the next read, with the line back on the count.
    None: the meal is no longer held, in one step with the look at the rows, so that
    the next photo starts a read of its own."""
    with SessionLocal() as db:
        again = capture.album_after(db, job)
        line = capture.album_line(db, job.meal_id) if again else None
    if again is None:
        _live_reads.pop(job.meal_id, None)
        return None
    if _may_hear(job.telegram_id):
        await _retouch(bot, job.chat_id, line)
    return again


async def _read_meal(application: Application, job: capture.Read) -> None:
    """The background read. At most ``READS_AT_ONCE``, each with its own meter."""
    gate = application.bot_data.setdefault(
        "meal_reads_gate", asyncio.Semaphore(READS_AT_ONCE)
    )
    try:
        while job is not None:
            await _album_settles(job)
            async with gate:
                with SessionLocal() as db:
                    job = capture.read_as_of_now(db, job)
                # Closed while this waited its turn: not paid for. The sweep gives the
                # meal up, and says nothing to its person.
                if not _may_hear(job.telegram_id):
                    logger.info("Meal %s not read: the door closed", job.meal_id)
                    return
                if job.automatic:
                    with SessionLocal() as db:
                        refused = capture.automatic_start(db, job)
                    if refused is not None:
                        await _tell_read(application.bot, job, refused.line)
                        return
                with metering() as meter:
                    _, dead = await _read_one(application.bot, job, meter)
            # The picker is a push, and a read nobody asked for pushes nothing: the
            # next live meal asks.
            if dead is not None and not job.automatic:
                await _offer_model_choice(application, job, dead)
            job = await _album_went_on(application.bot, job)
    except Exception as e:
        # The record stands; the sweep gives the line its failure.
        logger.error("Meal %s read crashed: %s", job.meal_id, type(e).__name__)


async def _suggest_portion(dish_id: int) -> None:
    """A dish's guessed portion, behind the screen that was already sent. Nobody waits
    for it and nobody hears of it."""
    try:
        await portion_suggestions.ask(SessionLocal, get_openai_service(), dish_id)
    except Exception as e:
        logger.warning("Portion suggestion not asked: %s", type(e).__name__)


def _keep_task(task) -> None:
    # Held here: the event loop keeps only a weak reference to a running task.
    _meal_reads.add(task)
    task.add_done_callback(_meal_reads.discard)


def _start_read(application: Application, job: capture.Read) -> asyncio.Task:
    # Live from now, not from when the task first runs: nothing awaits in between.
    _live_reads[job.meal_id] = job.inbound_id
    task = application.create_task(_read_meal(application, job))
    _keep_task(task)
    _read_holders[job.meal_id] = task

    def let_go(ended) -> None:
        # Not a later read's hold: an album's read lets go before its task is over.
        if _read_holders.get(job.meal_id) is ended:
            del _read_holders[job.meal_id]
            _live_reads.pop(job.meal_id, None)

    task.add_done_callback(let_go)
    return task


def _pending_at_start() -> list:
    """Taken before polling begins: a meal born after this has its own task coming."""
    try:
        with SessionLocal() as db:
            return capture.pending_meals(db)
    except Exception as e:
        logger.error("Pending meals not read at start: %s", type(e).__name__)
        return []


async def _replay_pending(application: Application, meals: list) -> None:
    """What the last process left: read again from the stored source. What may not be
    read now is told so, and no line stays on «читаю»."""
    begun = 0
    for meal_id in meals:
        if meal_id in _live_reads:
            continue
        try:
            with SessionLocal() as db:
                job = None
                if begun < REPLAY_LIMIT:
                    job = capture.automatic_read(db, meal_id)
                given_up = None if job is not None else capture.give_up(db, meal_id)
            if job is not None:
                begun += 1
                _start_read(application, job)
            else:
                await _show_given_up(application, [given_up])
        except Exception as e:
            logger.error("Meal %s not replayed: %s", meal_id, type(e).__name__)
    await _show_owed(application)


async def _show_given_up(application: Application, given_up: list) -> None:
    for job, done in given_up:
        if job is not None:
            action_journal.note(job.telegram_id, action_event.FAILED, job.kind)
            if _may_hear(job.telegram_id):
                await _show(application.bot, job, done.line)


async def _sweep(application: Application) -> None:
    """One pass: a stuck pending fails and its line says so; what is due is read again."""
    with SessionLocal() as db:
        stuck = capture.stuck_meals(db, _live_reads)
    for meal_id in stuck:
        try:
            with SessionLocal() as db:
                given_up = capture.give_up(db, meal_id)
            await _show_given_up(application, [given_up])
        except Exception as e:
            logger.error("Meal %s not given up: %s", meal_id, type(e).__name__)
    await _show_owed(application)
    with SessionLocal() as db:
        due = capture.due_retries(db, capture.RETRIES_PER_SWEEP)
    for meal_id in due:
        try:
            with SessionLocal() as db:
                job = capture.read_again(db, meal_id, automatic=True)
            if job is not None:
                _start_read(application, job)
        except Exception as e:
            logger.error("Meal %s not read again: %s", meal_id, type(e).__name__)


async def _sweep_loop(application: Application, meals: list) -> None:
    """Nothing here may end the loop but the end of the process."""
    while not application.running:
        await asyncio.sleep(0.05)
    try:
        await _replay_pending(application, meals)
    except Exception as e:
        logger.error("Replay of pending meals failed: %s", type(e).__name__)
    finally:
        application.bot_data[REPLAY_DONE].set()
    while True:
        await asyncio.sleep(SWEEP_EVERY_SECONDS)
        if not application.running:
            return
        try:
            await _sweep(application)
        except Exception as e:
            logger.error("Meal sweep failed: %s", type(e).__name__)


# The few of every day; the rest stay out of the way, and the owner's get their own scope.
_COMMANDS = (
    BotCommand("start", "Открыть меню"),
    BotCommand("feedback", "Написать владельцу бота"),
)
_OWNER_COMMANDS = (
    BotCommand("invite", "Пригласить человека"),
    BotCommand("users", "Кто пользуется и сколько это стоит"),
)


async def _set_commands(application: Application) -> None:
    """The command menu. A convenience: without it the bot starts all the same. /invite and
    /users are in each admin's own, and in nobody else's; each is set apart, so that a chat Telegram
    refuses costs that admin the entry and no one else theirs."""
    try:
        await application.bot.set_my_commands(_COMMANDS)
    except Exception as e:
        logger.warning("Command menu not set: %s", type(e).__name__)
    for admin_id in settings.admin_ids:
        try:
            await application.bot.set_my_commands(
                (*_COMMANDS, *_OWNER_COMMANDS), scope=BotCommandScopeChat(admin_id)
            )
        except Exception as e:
            logger.warning("Owner's command menu not set: %s", type(e).__name__)


async def _on_start(application: Application) -> None:
    # Not ``application.create_task``: not running yet, it would only warn, and made
    # while running ``stop()`` would wait for this one for ever.
    meals = _pending_at_start()
    application.bot_data[REPLAY_DONE] = asyncio.Event()
    application.bot_data[SWEEP_TASK] = asyncio.create_task(
        _sweep_loop(application, meals)
    )
    await _set_commands(application)
    await _load_custom_emoji(application)


async def _on_stop(application: Application) -> None:
    task = application.bot_data.get(SWEEP_TASK)
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _send_line(message, line: capture.Line):
    """A silent reply to the person's message, mapped when it belongs to a meal; None
    if Telegram refused it, after a moment of it was tried again."""
    try:
        sent = await _tries(
            lambda: message.reply_text(
                line.text,
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard if line.menu else line.markup,
                disable_notification=True,
                reply_parameters=ReplyParameters(
                    message_id=message.message_id, allow_sending_without_reply=True
                ),
            )
        )
    except TelegramError as e:
        logger.warning("Meal line not sent: %s", type(e).__name__)
        return None
    return sent


def _map_line(line: capture.Line, sent, view: str) -> None:
    if sent is None or line.meal_id is None:
        return
    with SessionLocal() as db:
        capture.map_message(db, sent.chat_id, sent.message_id, line.meal_id, view)


async def _new_meal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    came: capture.Incoming,
    note: Optional[str],
    words: Optional[str] = None,
    day=None,
) -> bool:
    """Row 10: the record first, then the line, then the read. False if no record."""
    message = update.message
    try:
        with SessionLocal() as db:
            born = capture.born(db, came, note=note, words=words, day=day)
    except Exception as e:
        logger.error("Meal not written: %s", type(e).__name__)
        await message.reply_text(capture.M7F_REFUSED)
        return False
    if born is None:
        return True
    if born.joined:
        await _album_grew(context.application, came, born)
        return True
    # The diagnostic row that /reprocess replays; a voice has its meal for that.
    inbound_id = None
    if came.kind != "voice":
        image = came.kind != "text"
        inbound_id = inbound_msg.record_inbound(
            telegram_id=came.telegram_id,
            kind="image" if image else "text",
            content=came.text,
            photo_file_id=came.file_id if image else None,
        )
    if born.final is not None:
        inbound_msg.mark_analyzed(inbound_id, {"exact_name": born.names})
        sent = await _send_line(message, born.final)
        _map_line(born.final, sent, "line")
        if sent is not None:
            await _rollout(message, came.telegram_id)
        await _menu_once(context.bot, came.chat_id, came.telegram_id)
        return True
    sent = await _send_line(message, born.first)
    _map_line(born.first, sent, "pending")
    job = replace(
        born.job,
        line_id=sent.message_id if sent else None,
        inbound_id=inbound_id,
        unsent=sent is None,
    )
    _start_read(context.application, job)
    if sent is not None:
        await _rollout(message, came.telegram_id)
    return True


async def _rollout(message, telegram_id: int) -> None:
    """M12, right after the line and never in its place. Its own message: no quote, no
    meal. It can cost the meal nothing, whatever goes wrong here."""
    try:
        with SessionLocal() as db:
            words = capture.take_rollout(db, telegram_id)
        if words is None:
            return
        try:
            await message.reply_text(
                words, reply_markup=main_keyboard, disable_notification=True
            )
        except TelegramError as e:
            logger.warning("Rollout line not sent: %s", type(e).__name__)
            with SessionLocal() as db:
                capture.rollout_again(db, telegram_id)
    except Exception as e:
        logger.error("Rollout line failed: %s", type(e).__name__)


async def _album_grew(
    application: Application, came: capture.Incoming, born: capture.Born
) -> None:
    """Another photo of an album, on the same line. A read still to come takes it in
    by itself; after one that ended, this is the straggler's own."""
    with SessionLocal() as db:
        again = None
        if born.meal_id not in _live_reads:
            again = capture.album_again(db, born.meal_id)
        line = capture.album_line(db, born.meal_id)
    if again is not None:
        # Before the await: the next photo must find this read.
        _live_reads[again.meal_id] = again.inbound_id
    await _retouch(application.bot, came.chat_id, line)
    if again is not None:
        _start_read(application, again)


async def _read_split(
    application: Application,
    chat_id: int,
    tapped: Optional[int],
    split: capture.Split,
) -> None:
    """M2b done: each photo gets a line of its own under it, and a read of its own."""
    tasks = []
    for message_id, job in split.jobs:
        with SessionLocal() as db:
            words = capture.split_pending(db, job.meal_id)
        line = capture.Line(words, meal_id=job.meal_id)
        sent = None
        try:
            sent = await _tries(
                lambda: application.bot.send_message(
                    chat_id,
                    line.text,
                    disable_notification=True,
                    reply_parameters=ReplyParameters(
                        message_id=message_id, allow_sending_without_reply=True
                    ),
                )
            )
        except TelegramError as e:
            logger.warning("Meal line not sent: %s", type(e).__name__)
        _map_line(line, sent, "pending")
        line_id = sent.message_id if sent else None
        job = replace(
            job,
            line_id=line_id,
            shown=line.text if sent else None,
            unsent=sent is None,
        )
        tasks.append(_start_read(application, job))
    if tapped is not None:
        _keep_task(
            application.create_task(
                _split_settles(application, chat_id, tapped, split, tasks)
            )
        )


async def _split_settles(
    application: Application,
    chat_id: int,
    tapped: int,
    split: capture.Split,
    tasks: list,
) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)
    with SessionLocal() as db:
        retouch = capture.split_settled(db, split.event_id, tapped)
    await _retouch(application.bot, chat_id, retouch)


async def _take_lines_back(bot, chat_id: int, message_ids: tuple) -> None:
    """The lines of the meals a way back removed. Best effort: too old to delete, a
    line stays, and it maps to the meal that holds its photo now."""
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramError as e:
            logger.warning("Meal line not removed: %s", type(e).__name__)


class _Went(enum.Enum):
    """What became of a message in the diary: the one thing «↩️ Это была еда» has to know.
    REFUSED may pass on a second try; NOTHING was answered, and writes nothing however
    many times it is tried."""

    WRITTEN = "written"
    REFUSED = "refused"
    NOTHING = "nothing"


async def capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Section 5.1: what a private message is, when no menu, command or slot took it.

    Outside the conversation, so no state ever eats food. The handler returns before
    the read ends.
    """
    await _capture(update, context)


async def _capture(
    update: Update, context: ContextTypes.DEFAULT_TYPE, as_food: bool = False
) -> _Went:
    """``as_food``: the person has said so, and neither a final «?» nor a «спасибо» is a
    reason to keep it out of the diary."""
    message, user = update.message, update.effective_user
    if message is None or user is None:
        return _Went.REFUSED
    if not await check_subscription(user.id):
        await message.reply_text(refusal(user.id))
        return _Went.REFUSED
    await _welcome_unasked(update, context)
    if not as_food and message.text and await _model_choice(update, context):
        return _Went.NOTHING
    came = capture.incoming(message, user)
    if came.kind is None:
        await message.reply_text(capture.M9_UNREADABLE)
        return _Went.REFUSED
    if capture.too_big(came):
        await message.reply_text(capture.M4F_TOO_BIG)
        return _Went.REFUSED
    try:
        with SessionLocal() as db:
            if capture.seen(db, came):
                return _Went.WRITTEN
            if came.kind == "text":
                route = capture.route_text(db, came, as_food=as_food)
            else:
                route = capture.route_media(db, came)
    except Exception as e:
        logger.error("Router failed: %s", type(e).__name__)
        await message.reply_text(capture.M7F_REFUSED)
        return _Went.REFUSED

    if route.action == capture.NEW_MEAL:
        written = await _new_meal(update, context, came, route.note, route.words)
        return _Went.WRITTEN if written else _Went.REFUSED
    if route.action == capture.SILENT:
        return _Went.NOTHING
    went = _Went.WRITTEN
    if route.action == capture.LINE:
        line = route.line
        _map_line(line, await _send_line(message, line), line.view)
        await _retouch(context.bot, came.chat_id, line.retouch)
        if line.fill:
            task = context.application.create_task(_fill_typed_dish(line.fill, user.id))
            _keep_task(task)
        # A line with no meal behind it only answered: «В дневнике ничего не поменялось».
        if line.meal_id is None:
            went = _Went.NOTHING
    elif route.action == capture.WEIGHT:
        await process_weight(update, context)
    elif route.action == capture.TICK:
        await message.reply_text(f"✓ {route.tick}", reply_markup=_day_card_button())
    await _menu_once(context.bot, came.chat_id, user.id)
    return went


async def capture_edited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """M11, ahead of everything else (group -1): an edit is never a new message, so no
    conversation, slot or menu label sees it. No read and no payment here."""
    message, user = update.edited_message, update.effective_user
    if message is None or user is None or message.chat.type != "private":
        raise ApplicationHandlerStop
    came = capture.incoming(message, user)
    if came.kind is None or not await check_subscription(user.id):
        raise ApplicationHandlerStop
    try:
        with SessionLocal() as db:
            line = capture.edited(db, came)
    except Exception as e:
        # A database error's text holds the row: the words.
        logger.error("Edited message not kept: %s", type(e).__name__)
        raise ApplicationHandlerStop
    if line is not None:
        _map_line(line, await _send_line(message, line), line.view)
    raise ApplicationHandlerStop


def _day_card_button() -> InlineKeyboardMarkup:
    """Under a ticked routine: the day, where it now shows."""
    day = InlineKeyboardButton(
        "📊 Карточка дня",
        callback_data=stats_screen.CALLBACK_PREFIX + stats_screen.TODAY,
    )
    return InlineKeyboardMarkup([[day]])


async def meal_stale_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on the buttons of a draft from before the drafts were retired, still on
    someone's screen: say so and drop the buttons."""
    query = update.callback_query
    await query.answer(
        "Этот черновик уже неактуален — отправь приём пищи заново", show_alert=True
    )
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        pass


@subscription_required
async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The favorites list (menu button «⭐ Избранное»)."""
    with SessionLocal() as db:
        screen = favorites.open_list(db, update.effective_user.id)
    await update.message.reply_text(
        screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
    )
    return CHOOSING_ACTION


@subscription_required
async def show_dishes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The dish base (menu button «🍲 Блюда»)."""
    with SessionLocal() as db:
        screen = dishes.open_list(db, update.effective_user.id)
    await update.message.reply_text(
        screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
    )
    return CHOOSING_ACTION


_card_delivery = CardDelivery(PictureRenderer(Path(settings.RENDER_DIR)))


async def _send_files(context, chat_id: int, screen):
    """M13's photos, as they came and silently, before the tap is answered: what
    Telegram says of them is the toast."""
    if not screen.files:
        return screen
    bot = context.bot
    try:
        for kind, file_id in screen.files:
            send = bot.send_document if kind == "document_photo" else bot.send_photo
            await send(chat_id, file_id, disable_notification=True)
    except BadRequest as e:
        logger.info("Kept photo not sent again: %s", type(e).__name__)
        return replace(screen, toast=capture.M13_PHOTO_GONE_TOAST)
    except TelegramError as e:
        # The file may well be there: only Telegram's refusal says it is not.
        logger.warning("Kept photo not sent: %s", type(e).__name__)
        return replace(screen, toast="Не получилось, попробуй ещё раз")
    return screen


async def dishes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The ``dh:`` verbs. A list or a dish holds no meal; a dish just logged does, so a
    reply to it means that meal."""
    message = update.callback_query.message
    message_id = getattr(message, "message_id", None)
    chat_id = getattr(message, "chat_id", None)

    shown = []

    def handle(db, telegram_id: int, data: str):
        screen = dishes.handle_callback(db, telegram_id, data, message_id=message_id)
        shown.append(screen)
        if screen.text:
            capture.tapped(db, chat_id, message_id, screen)
        return screen

    await _screen_callback(
        update,
        handle,
        TERM_ENDED,
        "Dishes",
        deliver=_card_delivery.tap,
    )
    if shown and shown[0].suggest_dish:
        _keep_task(
            context.application.create_task(_suggest_portion(shown[0].suggest_dish))
        )


async def meal_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The ``ml:`` verbs. The message id goes along: a typed answer retires its
    keyboard, and a line that became a screen is no longer the read's to edit."""
    message = update.callback_query.message
    message_id = getattr(message, "message_id", None)
    chat_id = getattr(message, "chat_id", None)
    shown = []

    def handle(db, telegram_id: int, data: str):
        screen = meal_card.handle_callback(db, telegram_id, data, message_id=message_id)
        if screen.text:
            capture.tapped(db, chat_id, message_id, screen)
        if screen.read:
            # Held from now: an old meal gone pending and held by no task looks stuck.
            _live_reads[screen.read.meal_id] = screen.read.inbound_id
        if screen.split:
            for _message_id, job in screen.split.jobs:
                _live_reads[job.meal_id] = job.inbound_id
        shown.append(screen)
        return screen

    try:
        await _screen_callback(
            update,
            handle,
            TERM_ENDED,
            "Meal card",
            deliver=_card_delivery.tap,
            before_answer=lambda screen: _send_files(context, chat_id, screen),
        )
    finally:
        # After the delivery: an answer that came at once would be written over.
        if shown and shown[0].read:
            job = replace(shown[0].read, tap=update.callback_query.id)
            _start_read(context.application, job)
        if shown and shown[0].split:
            await _read_split(context.application, chat_id, message_id, shown[0].split)
    if not shown:
        return
    if shown[0].gone:
        await _take_lines_back(context.bot, chat_id, shown[0].gone)
    if shown[0].retouch:
        await _retouch(context.bot, chat_id, shown[0].retouch)
    if shown[0].retouch_too:
        await _retouch(context.bot, chat_id, shown[0].retouch_too)
    if shown[0].fill:
        task = context.application.create_task(
            _fill_typed_dish(shown[0].fill, update.effective_user.id)
        )
        _keep_task(task)


def _is_plain_answer(text: Optional[str]) -> bool:
    """Whether a text can answer a slot: a command or a keyboard's label never does."""
    if not text or text.startswith("/"):
        return False
    return _KEYBOARD_LABEL.fullmatch(text) is None


# Held here: the event loop keeps only a weak reference to a running task.
_typed_fills: set = set()


async def _fill_typed_dish(fill: meal_card.Fill, telegram_id: int) -> None:
    """N4: a typed new name's numbers, with no new message. If this fails the name
    stays, and its screen says «Цифры не посчитаны.»."""
    try:
        with attribute_to(telegram_id):
            nutrition = await _analyze_and_parse(
                kind="text",
                payload=fill.name,
                input_ref=fill.name,
                telegram_id=telegram_id,
            )
        with SessionLocal() as db:
            meal_items.fill_numbers(
                db, fill.item_id, fill.rev, fill.event_id, nutrition
            )
    except Exception as e:
        logger.warning("Typed dish left without numbers: %s", type(e).__name__)


async def _meal_on_asked_day(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """O1b: the next text or photo is a meal of the day the slot holds, with no way
    down the router, where a number would be a weigh-in. What is no meal, or no
    longer waited for, returns False and goes the usual way, which says why."""
    message, user = update.message, update.effective_user
    with SessionLocal() as db:
        if not meal_card.waits_for_day(db, user.id):
            return False
    if message.text is not None and not _is_plain_answer(message.text):
        return False
    if not await check_subscription(user.id):
        return False
    came = capture.incoming(message, user)
    if came.kind is None or capture.too_big(came):
        return False
    with SessionLocal() as db:
        if capture.seen(db, came):
            return False
        asked = meal_card.take_day(db, user.id)
    if asked is None:
        return False
    day, message_id = asked
    if message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=message.chat_id, message_id=message_id, reply_markup=None
            )
        except TelegramError as e:
            logger.info("Day slot: the old keyboard stays: %s", e)
    await _new_meal(update, context, came, None, day=day)
    return True


async def meal_slot_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The answer to the card's typed slot, ahead of the conversation (group -1).

    With no slot, or with anything that is not its answer, the message goes on down
    the router: a wait never eats food. An answer goes out as a new message, under
    which the old screen loses its keyboard.
    """
    message, user = update.message, update.effective_user
    if message is None or user is None:
        return
    try:
        with SessionLocal() as db:
            if not meal_card.has_slot(db, user.id):
                return
        if await _meal_on_asked_day(update, context):
            raise ApplicationHandlerStop
        text = message.text if _is_plain_answer(message.text) else None
        if text is not None and not await check_subscription(user.id):
            text = None
        said = (message.chat_id, message.message_id, message.date)
        with SessionLocal() as db:
            typed = meal_card.take_typed(db, user.id, text, said=said)
    except ApplicationHandlerStop:
        raise  # the meal is written: this is the way out, not a failure
    except Exception as e:
        # A database error's text holds the row: the words typed.
        logger.error(
            "Meal slot failed: %s",
            type(e).__name__,
            exc_info=not isinstance(e, SQLAlchemyError),
        )
        await message.reply_text("Не получилось, попробуй ещё раз")
        raise ApplicationHandlerStop
    if typed is None:
        return
    if typed.message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=message.chat_id,
                message_id=typed.message_id,
                reply_markup=None,
            )
        except TelegramError as e:
            logger.info("Meal slot: the old keyboard stays: %s", e)
    sent = await message.reply_text(
        typed.screen.text,
        parse_mode=ParseMode.HTML,
        reply_markup=typed.screen.markup,
    )
    try:
        with SessionLocal() as db:
            meal_card.slot_sent(db, user.id, sent.message_id)
            capture.tapped(db, message.chat_id, sent.message_id, typed.screen)
    except Exception as e:
        logger.error("Meal slot: the answer's id was not kept: %s", e, exc_info=True)
    if typed.screen.read:
        _start_read(
            context.application, replace(typed.screen.read, line_id=sent.message_id)
        )
    if typed.fill:
        task = asyncio.create_task(_fill_typed_dish(typed.fill, user.id))
        _typed_fills.add(task)
        task.add_done_callback(_typed_fills.discard)
    if typed.screen.suggest_dish:
        _keep_task(
            context.application.create_task(_suggest_portion(typed.screen.suggest_dish))
        )
    raise ApplicationHandlerStop


async def meal_slot_left_by_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any inline tap outside the card, in its own group: the person has moved on."""
    try:
        with SessionLocal() as db:
            meal_card.leave_slot(db, update.effective_user.id)
    except Exception as e:
        logger.error("Meal slot not closed: %s", e, exc_info=True)


async def _screen_callback(
    update: Update,
    handle,
    locked: Optional[str],
    what: str,
    deliver=None,
    before_answer=None,
):
    """A tap on a screen's inline button: ``handle(db, telegram_id, data)``
    gives the next screen, which replaces the message in place.

    Registered outside the ConversationHandler: the callback data carries the
    whole step, so it works in any conversation state and after a restart.
    """
    query = update.callback_query
    # ``locked`` None: a screen that is open whatever the subscription says.
    if locked is not None and not await check_subscription(update.effective_user.id):
        await query.answer(locked, show_alert=True)
        return
    try:
        with SessionLocal() as db:
            screen = handle(db, update.effective_user.id, query.data or "")
    except Exception as e:
        # A database error's text holds the row, and a meal's row holds its words.
        logger.error(
            "%s callback %r failed: %s",
            what,
            query.data,
            type(e).__name__,
            exc_info=not isinstance(e, SQLAlchemyError),
        )
        await query.answer("Не получилось, попробуй ещё раз", show_alert=True)
        return
    if before_answer is not None:
        screen = await before_answer(screen)
    await query.answer(screen.toast)
    if not screen.text:
        return
    if deliver is not None:
        await deliver(query, screen)
        return
    try:
        await query.edit_message_text(
            screen.text,
            parse_mode=ParseMode.HTML,
            reply_markup=screen.markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except BadRequest as e:
        # A repeated tap that leads to the same screen: nothing to change.
        if "not modified" not in str(e).lower():
            raise


async def favorites_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on a favorites inline button (``fav:…``). A list or a ⭐ holds no meal; one
    just logged does, so a reply to it means that meal."""
    message = update.callback_query.message
    message_id = getattr(message, "message_id", None)
    chat_id = getattr(message, "chat_id", None)

    def handle(db, telegram_id: int, data: str):
        screen = favorites.handle_callback(db, telegram_id, data, message_id=message_id)
        if screen.text:
            capture.tapped(db, chat_id, message_id, screen)
        return screen

    await _screen_callback(
        update,
        handle,
        TERM_ENDED,
        "Favorites",
        deliver=_card_delivery.tap,
    )


async def reminders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on a 🔔 reminder or its settings (``rm:…``). A ⭐ logged from a reminder
    makes the message hold that meal, as a ⭐ from the list does."""
    message = update.callback_query.message
    message_id = getattr(message, "message_id", None)
    chat_id = getattr(message, "chat_id", None)
    strip = []

    def handle(db, telegram_id: int, data: str):
        screen = reminders.handle_callback(db, telegram_id, data, message_id=message_id)
        if screen.text:
            capture.tapped(db, chat_id, message_id, screen)
        strip.append(screen.view == reminders.STRIP)
        return screen

    await _screen_callback(
        update,
        handle,
        TERM_ENDED,
        "Reminders",
        deliver=_card_delivery.tap,
    )
    if strip and strip[0]:
        # ✅ ОК: the buttons leave, the text stays.
        try:
            await update.callback_query.edit_message_reply_markup(reply_markup=None)
        except TelegramError as e:
            logger.warning("reminders: buttons stayed on the message: %s", e)


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A statistics tab (``st:…``). The message shows no one meal from here on."""
    message = update.callback_query.message

    def handle(db, telegram_id: int, data: str):
        capture.left_meal(
            db,
            getattr(message, "chat_id", None),
            getattr(message, "message_id", None),
        )
        return stats_screen.handle_callback(db, telegram_id, data)

    await _screen_callback(
        update,
        handle,
        TERM_ENDED,
        "Stats",
        deliver=_card_delivery.tap,
    )


async def close_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """✅ ОК: the buttons leave the message, its text stays. Nothing else changes."""
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError as e:
        logger.warning("ok: buttons stayed on the message: %s", e)


async def weight_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The weight screen's undo (``wt:…``)."""
    await _screen_callback(
        update,
        weight_screen.handle_callback,
        TERM_ENDED,
        "Weight",
    )


async def _reply_screen(update: Update, screen) -> None:
    await update.message.reply_text(
        screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
    )


@subscription_required
async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Today's meals and totals, tabs for yesterday and 7 days (📊 Статистика)."""
    with SessionLocal() as db:
        screen = stats_screen.open_view(db, update.effective_user.id)
    await _card_delivery.send(update.message, screen)
    return CHOOSING_ACTION


@subscription_required
async def show_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The weight history, then a typed number is the new weighing (⚖️ Мой вес)."""
    with SessionLocal() as db:
        screen = weight_screen.open_view(db, update.effective_user.id)
    await _reply_screen(update, screen)
    return ENTERING_WEIGHT


@subscription_required
async def process_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A typed weight is saved (with undo); after «⚖️ Мой вес» anything else
    gets the hint. The state stays, so a second number corrects the first;
    the menu buttons keep working in it."""
    kg = weight_screen.parse_kg(update.message.text)
    if kg is None:
        await update.message.reply_text(
            "Не похоже на вес. " + weight_screen.ASK, parse_mode=ParseMode.HTML
        )
        return ENTERING_WEIGHT
    user = update.effective_user
    with SessionLocal() as db:
        screen = weight_screen.record(db, user.id, kg, user.username)
    await _reply_screen(update, screen)
    return ENTERING_WEIGHT


@subscription_required
async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """What the bot runs with, and its switches (⚙️ Настройки)."""
    with SessionLocal() as db:
        screen = settings_screen.open_view(db, update.effective_user.id)
    await _reply_screen(update, screen)
    return CHOOSING_ACTION


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A switch on the settings screen (``set:…``)."""
    await _screen_callback(
        update,
        settings_screen.handle_callback,
        TERM_ENDED,
        "Settings",
    )


async def routines_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on the routine screens (``rt:…``)."""
    await _screen_callback(update, routines.handle_callback, TERM_ENDED, "Routines")


async def food_lists_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on the food lists (``fl:…``)."""
    await _screen_callback(
        update,
        food_lists.handle_callback,
        TERM_ENDED,
        "Food lists",
    )


async def explain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on «ℹ️ Как это работает» (``hw:…``)."""
    await _screen_callback(
        update,
        explain_screen.handle_callback,
        TERM_ENDED,
        "Explanations",
    )


async def routine_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """➕ or ✏️ on the routine screens: the next text message is the name. A tap
    that opens no name step changes no state."""
    asked = {}

    def handle(db, telegram_id, data):
        screen, asked["rename"], asked["wait"] = routines.ask_name(
            db, telegram_id, data
        )
        return screen

    await _screen_callback(update, handle, TERM_ENDED, "Routines")
    if not asked.get("wait"):
        return None
    context.user_data["routine_rename"] = asked["rename"]
    context.user_data["routine_asked_at"] = datetime.now(UTC)
    return ENTERING_ROUTINE_NAME


async def routine_leave_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A routine button instead of a name: the question is closed, so the next
    text is not taken for a name."""
    context.user_data.pop("routine_rename", None)
    context.user_data.pop("routine_asked_at", None)
    await routines_callback(update, context)
    return CHOOSING_ACTION


async def routine_left_by_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any other inline tap, in its own handler group: those screens run outside
    the conversation and do not change its state, but the user has moved on."""
    context.user_data.pop("routine_asked_at", None)


@subscription_required
async def process_routine_name(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """The typed name; a name that does not fit asks again. Text arriving after
    the user moved on is what it would be anywhere else, never a silent rename."""
    asked_at = context.user_data.pop("routine_asked_at", None)
    if asked_at is None or datetime.now(UTC) - asked_at > ROUTINE_NAME_WAIT:
        context.user_data.pop("routine_rename", None)
        await capture_message(update, context)
        return CHOOSING_ACTION
    with SessionLocal() as db:
        screen, done = routines.submit_name(
            db,
            update.effective_user.id,
            update.message.text,
            context.user_data.get("routine_rename"),
        )
    await _reply_screen(update, screen)
    if not done:
        context.user_data["routine_asked_at"] = asked_at
        return ENTERING_ROUTINE_NAME
    context.user_data.pop("routine_rename", None)
    return CHOOSING_ACTION


async def _capture_and_leave_wait(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Media while a typed name is waited for: a meal, and the wait is over."""
    for key in ("routine_asked_at", "routine_rename", "food_asked_at", "food_kind"):
        context.user_data.pop(key, None)
    await capture_message(update, context)
    return CHOOSING_ACTION


async def food_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """➕ or ✋ on the food lists: the next text message is a food. A tap that opens
    no such step changes no state."""
    asked = {}

    def handle(db, telegram_id, data):
        screen, asked["kind"], asked["wait"] = food_lists.ask_food(
            db, telegram_id, data
        )
        return screen

    await _screen_callback(update, handle, TERM_ENDED, "Food lists")
    if not asked.get("wait"):
        return None
    context.user_data["food_kind"] = asked["kind"]
    context.user_data["food_asked_at"] = datetime.now(UTC)
    return ENTERING_FOOD_NAME


async def food_leave_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A list button instead of a food: the next text is not taken for one."""
    context.user_data.pop("food_kind", None)
    context.user_data.pop("food_asked_at", None)
    await food_lists_callback(update, context)
    return CHOOSING_ACTION


async def food_left_by_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any other inline tap, in its own handler group: the user has moved on."""
    context.user_data.pop("food_asked_at", None)


@subscription_required
async def process_food_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The typed food. Text arriving after the user moved on is what it would be
    anywhere else, never a silent entry on a list."""
    asked_at = context.user_data.pop("food_asked_at", None)
    kind = context.user_data.get("food_kind")
    if (
        asked_at is None
        or kind is None
        or datetime.now(UTC) - asked_at > FOOD_NAME_WAIT
    ):
        context.user_data.pop("food_kind", None)
        await capture_message(update, context)
        return CHOOSING_ACTION
    with SessionLocal() as db:
        screen, wait = food_lists.submit_food(
            db, update.effective_user.id, kind, update.message.text
        )
    await _reply_screen(update, screen)
    if wait:
        context.user_data["food_asked_at"] = datetime.now(UTC)
        return ENTERING_FOOD_NAME
    context.user_data.pop("food_kind", None)
    return CHOOSING_ACTION


async def old_lock_label(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A label of the padlock's keyboard, still on a phone. That keyboard goes, and comes
    back as the menu for someone whose term was renewed."""
    if await check_subscription(update.effective_user.id):
        await update.message.reply_text("Что делаем?", reply_markup=main_keyboard)
    else:
        await update.message.reply_text(
            refusal(update.effective_user.id), reply_markup=ReplyKeyboardRemove()
        )
    return CHOOSING_ACTION


@admin_required
async def grant_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant subscription to user (admin only)"""
    admin_id = update.effective_user.id
    logger.info(f"Grant subscription request from admin {admin_id}")

    try:
        # Expected format: /grant_sub user_id months
        _, user_id, months = update.message.text.split()
        user_id = int(user_id)
        months = int(months)

        logger.info(
            f"Attempting to grant subscription: user_id={user_id}, months={months}, admin_id={admin_id}"
        )

        with SessionLocal() as db:
            try:
                invites.by_id(db, user_id, admin_id, months)
                granted = True
            except invites.GrantFailed:
                granted = False

        if granted:
            logger.info(f"Successfully granted subscription to user {user_id}")
            await update.message.reply_text(
                f"✅ Доступ открыт: {user_id}, срок {people_screen.term_words(months)}. "
                "Человек теперь в /users. Сам бот написать не сможет, пока этот "
                "человек его не открыл: ссылку на бота отправь сам."
            )
        else:
            logger.error(
                f"Failed to create subscription: user_id={user_id}, months={months}, admin_id={admin_id}"
            )
            await update.message.reply_text("❌ Ошибка при выдаче подписки")

    except ValueError:
        logger.error(f"Invalid command format: {update.message.text}", exc_info=True)
        await update.message.reply_text("❌ Формат команды: /grant_sub user_id months")
    except Exception as e:
        logger.error(
            f"Unexpected error while granting subscription: {str(e)}", exc_info=True
        )
        await update.message.reply_text(f"❌ Произошла ошибка: {str(e)}")


@admin_required
async def consult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Relay a health question to the my-health hub copilot and show its answer.

    Owner-only (via @admin_required): the hub is single-tenant and its answers carry
    no requester identity. Thin relay: no medical logic, no medical storage, and —
    importantly — no OpenAI call on this path (OpenAI stays for food parsing only).
    The hub owns all medical reasoning and the mental-health guardrails.
    """
    question = " ".join(context.args) if context.args else ""
    if not question:
        await update.message.reply_text("Задай вопрос так: /consult <вопрос>")
        return

    if not settings.MYHEALTH_CONSULT_URL or not settings.CONSULT_TOKEN:
        await update.message.reply_text("Консультации сейчас недоступны.")
        return

    # Light per-user cooldown so a rapid burst doesn't hammer the hub.
    now = datetime.now(UTC)
    user_id = update.effective_user.id
    last = _last_consult.get(user_id)
    if last and (now - last).total_seconds() < CONSULT_COOLDOWN_SECONDS:
        await update.message.reply_text("Подожди пару секунд и спроси снова.")
        return

    try:
        # ValueError covers a malformed/non-object body; InvalidURL covers a
        # misconfigured MYHEALTH_CONSULT_URL (not an httpx.HTTPError subclass).
        async with Loader(update.message, "⏳ Спрашиваю…") as loader:
            data = await consult_service.ask(question)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
        logger.error(f"Consult relay failed: {e}", exc_info=True)
        await loader.reply("Не удалось получить ответ. Попробуй позже.")
        return

    # Record the cooldown only after a successful call, so a failed attempt
    # doesn't block an immediate retry.
    _last_consult[user_id] = now

    # Crisis path FIRST — deterministic, from the hub, before the model answer.
    # Never hardcode psyche logic or a crisis number here; surface crisis_hint verbatim.
    if data.get("crisis_hint"):
        await loader.reply(f"⚠️ {data['crisis_hint']}")
    # str() guards against a non-string answer (e.g. a number) from the hub.
    answer = str(data.get("answer") or "Пустой ответ.")
    await loader.reply(answer + "\n\n(описательно, не медицинская консультация)")


@admin_required
async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Which model runs each task, and the shutdown-date check run now (ADR-0004).

    Owner only. The same check as the weekly Celery job: news also goes out as
    the usual alert and is recorded, so the Monday run won't repeat it.
    """
    text = "⏳ Проверяю модели у OpenAI, Google и Anthropic…"
    try:
        async with Loader(update.message, text) as loader:
            report = await run_model_watch(get_openai_service())
    except Exception as e:
        logger.error("/models: check failed: %s", e, exc_info=True)
        await loader.reply("Не удалось проверить модели, смотри логи.")
        return
    today = datetime.now(UTC).date()
    await loader.reply(render_status(report, today))


@admin_required
async def reprocess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read again what was not read (owner only — TD-009, TD-010).

    The replay after a model fix or a top-up. A meal still without a reading is read
    through the live path, so the meal gets filled and its line says so; a voice is
    listened to there, and its empty row never goes to the text model. What remains of the older
    diagnostic rows, with no meal of their own, is re-analyzed and written back to its
    row; a row whose meal stands, or with nothing to read, leaves the queue unpaid.
    Capped at REPROCESS_BATCH_LIMIT per call to bound OpenAI spend; run again for
    more. If the model is still unavailable it stops early and says so.
    """
    try:
        with SessionLocal() as db:
            # A read already running is paid for: it is not asked twice.
            meals = capture.unread_meals(db, REPROCESS_BATCH_LIMIT)
            meals = [meal_id for meal_id in meals if meal_id not in _live_reads]
            meals = [m for m in meals if capture.owner_may_receive(db, m)]
            jobs = [capture.read_again(db, meal_id) for meal_id in meals]
        jobs = [job for job in jobs if job is not None]
        room = REPROCESS_BATCH_LIMIT - len(jobs)
        rows = (
            inbound_msg.get_reprocessable(limit=REPROCESS_BATCH_LIMIT) if room else []
        )
    except Exception as e:
        logger.error("reprocess: could not load queue: %s", type(e).__name__)
        await update.message.reply_text("Не удалось получить список сообщений.")
        return
    # A meal's own diagnostic row is its meal's, and is not paid for twice.
    taken = {job.inbound_id for job in jobs} | set(_live_reads.values())
    rows = [row for row in rows if row.id not in taken]
    replayed = []
    try:
        with SessionLocal() as db:
            for row in rows:
                fate, written = capture.row_fate(db, row)
                if fate == capture.REPLAY:
                    # Stays in the queue: «↩️ Вернуть» finds it as it was.
                    if may_receive(db, row.telegram_id):
                        replayed.append(row)
                elif fate == capture.SETTLED:
                    inbound_msg.mark_analyzed(row.id, written)
    except Exception as e:
        logger.error("reprocess: could not sort the queue: %s", type(e).__name__)
        await update.message.reply_text("Не удалось получить список сообщений.")
        return
    rows = replayed[:room]

    if not jobs and not rows:
        await update.message.reply_text("Нет сообщений для повторной обработки. 👍")
        return

    def stopped(e: ModelUnavailableError) -> str:
        # Still broken — leave the rest queued and tell the owner to switch.
        return (
            f"⚠️ Модель «{e.model}» всё ещё недоступна. Смени модель (отправь "
            "приём пищи, выбери модель) и повтори /reprocess.\n"
            f"Обработано до остановки: успешно {analyzed}, с ошибкой {failed}."
        )

    analyzed = failed = 0
    for job in jobs:
        _live_reads[job.meal_id] = job.inbound_id
    try:
        for job in jobs:
            with metering() as meter:
                read, dead = await _read_one(context.bot, job, meter)
            _live_reads.pop(job.meal_id, None)
            if dead is not None:
                await update.message.reply_text(stopped(dead))
                return
            analyzed, failed = analyzed + read, failed + (not read)
    finally:
        # Those not reached stay pending, and the sweep gives their lines the failure.
        for job in jobs:
            _live_reads.pop(job.meal_id, None)

    for row in rows:
        try:
            # The row's person pays for its replay, not the owner who asked for it.
            with attribute_to(row.telegram_id):
                if row.kind == "image":
                    if not row.photo_file_id:
                        raise ValueError("no photo reference to re-fetch")
                    payload = await _download_image(context.bot, row.photo_file_id)
                    # Use the full pipeline so reprocessed images also benefit from
                    # barcode→OFF lookup (same quality as the live flow).
                    resolution_result = await resolve_meal_nutrition(
                        payload,
                        telegram_id=row.telegram_id,
                        input_ref=row.photo_file_id,
                        # The stored caption. /reprocess OVERWRITES ai_analysis, so
                        # replaying without it would actively downgrade a record the
                        # live flow got right.
                        caption=row.content,
                    )
                    parsed = resolution_result.nutrition
                else:
                    payload = row.content or ""
                    input_ref = row.content or ""
                    parsed = await _analyze_and_parse(
                        kind=row.kind,
                        payload=payload,
                        input_ref=input_ref,
                        telegram_id=row.telegram_id,
                    )
        except ModelUnavailableError as e:
            await update.message.reply_text(stopped(e))
            return
        except Exception as e:
            logger.error("reprocess: message %s failed: %s", row.id, type(e).__name__)
            inbound_msg.mark_failed(row.id, type(e).__name__)
            failed += 1
            continue
        # Only count success once the status actually persisted — mark_analyzed is
        # best-effort, so a swallowed DB write must not be reported as done (the
        # row would otherwise silently reappear in the next /reprocess batch).
        if inbound_msg.mark_analyzed(row.id, parsed):
            analyzed += 1
        else:
            logger.warning(
                "reprocess: analyzed row %s but could not persist its status", row.id
            )
            failed += 1

    await update.message.reply_text(
        f"Повторная обработка ({len(jobs) + len(rows)}): "
        f"успешно {analyzed}, с ошибкой {failed}."
    )


# --- letting a person in ---------------------------------------------------------------


async def _ask_to_pick(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_INVITE_UNTIL] = time.monotonic() + INVITE_WAIT_SECONDS
    await message.reply_text(
        people_screen.PROMPT, reply_markup=people_screen.picker_keyboard()
    )


@admin_required
async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/invite: Telegram's own picker; the bot learns only who was picked."""
    await _ask_to_pick(update.message, context)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent to anyone else: the screen is not advertised, and a refusal would be."""
    if update.effective_user.id not in settings.admin_ids:
        logger.warning("/users by %s: refused", update.effective_user.id)
        return
    with SessionLocal() as db:
        screen = people_screen.hub(db)
    await _reply_screen(update, screen)


async def _users_send(bot, admin_id: int, screen):
    """A re-send goes out before the screen is drawn, which then says how it went."""
    if not isinstance(screen, feedback_inbox.Resend):
        return screen
    whole = await deliver_to_one(
        admin_id, list(screen.parts), file=screen.file, bot=bot
    )
    with SessionLocal() as db:
        return feedback_inbox.after_send(db, screen, whole)


async def _users_below(query, screen) -> None:
    """A new message: the one tapped holds a person's words, not a screen."""
    await query.message.reply_text(
        screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
    )


async def users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Checked here and again in ``people_screen.handle``: for anyone else a tap answers
    the same words whatever it names, and nothing is read or sent."""
    query, admin_id = update.callback_query, update.effective_user.id
    if admin_id not in settings.admin_ids:
        logger.warning("people: %r by %s: unknown or foreign", query.data, admin_id)
        await query.answer(people_screen.NOT_YOURS)
        return
    if query.data == people_screen.INVITE:
        await query.answer()
        await _ask_to_pick(query.message, context)
        return

    async def send(screen):
        return await _users_send(context.bot, admin_id, screen)

    below = (query.data or "").startswith(owner_buttons.CARD_HERE)
    await _screen_callback(
        update,
        lambda db, person, data: people_screen.handle(
            db, person, data, context.bot.username
        ),
        None,
        "users",
        deliver=_users_below if below else None,
        before_answer=send,
    )


async def welcome_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _screen_callback(
        update,
        welcome_screen.handle,
        TERM_ENDED,
        "welcome",
        deliver=_first_record_screen,
    )


async def _first_record_screen(query, screen) -> None:
    """A new message: an edited one cannot bring the main keyboard."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError as e:
        logger.warning("Welcome buttons not taken off: %s", type(e).__name__)
    await query.message.reply_text(
        screen.text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard
    )


def _feedback_arm(context, prompt_id: Optional[int]) -> None:
    """The next message is the owner's. No other wait outlives this promise."""
    context.user_data[_FEEDBACK_UNTIL] = time.monotonic() + FEEDBACK_WAIT_SECONDS
    context.user_data[_FEEDBACK_PROMPT] = prompt_id
    context.user_data.pop(_INVITE_UNTIL, None)


def _feedback_disarm(context) -> None:
    context.user_data.pop(_FEEDBACK_UNTIL, None)
    context.user_data.pop(_FEEDBACK_PROMPT, None)


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open to anyone through the gate: a locked diary is a thing to write about."""
    user = update.effective_user
    context.user_data.pop("routine_asked_at", None)
    context.user_data.pop("food_asked_at", None)
    try:
        with SessionLocal() as db:
            meal_card.leave_slot(db, user.id)
    except Exception as e:
        logger.error("Meal slot not closed: %s", type(e).__name__)
    screen = feedback_screen.prompt_screen()
    sent = await update.message.reply_text(
        screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
    )
    _feedback_arm(context, sent.message_id)


async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A tap on ``fb:…``. 👁 leaves the wait as it is: it is a look, not a way out."""
    query = update.callback_query
    data = query.data or ""
    if data.startswith(feedback_screen.CALLBACK_PREFIX + "food:"):
        await _feedback_was_food(update, context)
        return
    await _screen_callback(update, feedback_screen.handle, None, "Feedback")
    if data == feedback_screen.OPEN:
        _feedback_arm(context, getattr(query.message, "message_id", None))
    elif data == feedback_screen.CANCEL:
        _feedback_disarm(context)


async def feedback_left_by_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any other inline tap, in its own group: the person has moved on."""
    _feedback_disarm(context)


async def feedback_intake(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The message the 💬 screen promised, ahead of the slots and the diary. With no
    wait, past the wait, or for a command or a menu button, it goes on as ever: a wait
    never eats food. The row is written before anything is sent."""
    message, user = update.message, update.effective_user
    wait = context.user_data
    album = wait.get(_FEEDBACK_ALBUM)
    if (
        album is None
        or message.media_group_id != album[0]
        or time.monotonic() >= album[1]
    ):
        until = wait.get(_FEEDBACK_UNTIL)
        if until is None:
            return
        if (
            time.monotonic() >= until
            or message.text in _FEEDBACK_LEAVES
            or filters.COMMAND.check_update(update)
        ):
            _feedback_disarm(context)
            return
    came = capture.incoming(message, user)
    if came.kind is None:
        await message.reply_text(feedback_screen.UNSUPPORTED)
        raise ApplicationHandlerStop
    try:
        with SessionLocal() as db:
            row = feedback_screen.take(db, came, message.to_dict())
            feedback_id = row.id
    except Exception as e:
        # A database error's text holds the row: the words. The wait stays.
        logger.error("Feedback not saved: %s", type(e).__name__)
        await message.reply_text(feedback_screen.NOT_SAVED)
        raise ApplicationHandlerStop
    prompt_id = wait.get(_FEEDBACK_PROMPT)
    _feedback_disarm(context)
    if message.media_group_id:
        wait[_FEEDBACK_ALBUM] = (
            message.media_group_id,
            time.monotonic() + FEEDBACK_ALBUM_SECONDS,
        )
    if prompt_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=came.chat_id, message_id=prompt_id, reply_markup=None
            )
        except TelegramError as e:
            logger.info("Feedback: the old keyboard stays: %s", type(e).__name__)
    screen = feedback_screen.sent_screen(feedback_id)
    try:
        await message.reply_text(
            screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
        )
    except TelegramError as e:
        logger.warning("Feedback: no answer sent: %s", type(e).__name__)
    person = invites.Person(user.id, user.first_name, user.last_name, user.username)
    # Local whisper may take its two minutes, and this handler holds up the next update.
    _keep_task(
        context.application.create_task(
            _feedback_deliver(
                context.bot, feedback_id, person, came.chat_id, came.message_id
            )
        )
    )
    raise ApplicationHandlerStop


async def _feedback_heard(bot, feedback_id: int, file_id: str) -> None:
    """The free door only: no ``paid`` is handed to ``voice.hear``, so there is none.
    Whatever goes wrong here costs the owner the text, never the voice itself."""
    try:
        file = await bot.get_file(file_id)
        audio = bytes(await file.download_as_bytearray())
        heard = await voice.hear(audio, local_only=True, prompt=voice.GENERAL_PROMPT)
        with SessionLocal() as db:
            crud_feedback.set_transcript(db, feedback_id, heard.text)
    except (voice.NotHeard, voice.NoSpeech) as e:
        logger.info("Feedback voice not heard: %s", type(e).__name__)
    except Exception as e:
        logger.warning("Feedback voice failed: %s", type(e).__name__)


async def _feedback_deliver(
    bot, feedback_id: int, person, chat_id: int, message_id: int
) -> None:
    """The owner's copy. A failure leaves ``sent_at`` empty, and the row says so."""
    try:
        with SessionLocal() as db:
            row = crud_feedback.get(db, feedback_id)
            kind, file_id = row.kind, row.file_id
            food = row.was_food_at is not None
        if kind == feedback_model.VOICE and not food:
            await _feedback_heard(bot, feedback_id, file_id)
        with SessionLocal() as db:
            row = crud_feedback.get(db, feedback_id)
            if row.was_food_at is not None:
                # A record now, and the owner's screens hold no records. Only their
                # count says it was there.
                logger.info("Feedback %s turned out to be food: not sent", feedback_id)
                return
            user = settings_screen._user(db, person.telegram_id)
            parts = feedback_screen.owner_parts(
                row, person, datetime.now(UTC), settings_screen.user_zone(user)
            )
        forward = None if kind == feedback_model.TEXT else (chat_id, message_id)
        if not await deliver_to_admins(parts, forward=forward, bot=bot):
            logger.warning("Feedback %s stays undelivered", feedback_id)
            return
        with SessionLocal() as db:
            crud_feedback.mark_sent(db, feedback_id)
            row = crud_feedback.get(db, feedback_id)
            late_food = row.was_food_at is not None
        if late_food:  # tapped while this was on its way: the words are there already
            await notify_admins(feedback_screen.food_follow_up(person), bot=bot)
    except Exception as e:
        logger.error("Feedback %s not delivered: %s", feedback_id, type(e).__name__)


async def _feedback_was_food(update: Update, context) -> None:
    """«↩️ Это была еда»: the same message, down the real diary path, its form no reason
    to keep it out. Not written there, it is not marked and the button stays, so the food
    can still be saved and the owner can still read the words."""
    query, user = update.callback_query, update.effective_user
    try:
        with SessionLocal() as db:
            found = feedback_screen.handle(db, user.id, query.data)
    except Exception as e:
        logger.error("Feedback tap failed: %s", type(e).__name__)
        await query.answer(feedback_screen.FOOD_FAILED, show_alert=True)
        return
    if not isinstance(found, feedback_screen.Replay):
        await query.answer(found.toast)
        if found.text:
            await _feedback_retitle(query, found)
        return
    if not await check_subscription(user.id):
        await query.answer(feedback_screen.FOOD_LOCKED, show_alert=True)
        return
    try:
        message = Message.de_json(found.original, context.bot)
        went = await _capture(
            Update(update.update_id, message=message), context, as_food=True
        )
    except Exception as e:
        logger.error("Feedback replay failed: %s", type(e).__name__)
        went = _Went.REFUSED
    if went == _Went.NOTHING:
        await query.answer(feedback_screen.FOOD_NOT_WRITTEN, show_alert=True)
        return
    if went == _Went.REFUSED:
        await query.answer(feedback_screen.FOOD_FAILED, show_alert=True)
        return
    await query.answer()
    try:
        with SessionLocal() as db:
            first = crud_feedback.mark_food(db, found.feedback_id)
            sent = crud_feedback.get(db, found.feedback_id).sent_at is not None
    except Exception as e:
        # The meal stands, and a second tap finds it seen: nothing is written twice.
        logger.error("Feedback not marked as food: %s", type(e).__name__)
        return
    await _feedback_retitle(query, feedback_screen.food_done_screen())
    if first and sent:
        person = invites.Person(user.id, user.first_name, user.last_name, user.username)
        await notify_admins(feedback_screen.food_follow_up(person), bot=context.bot)


async def _feedback_retitle(query, screen) -> None:
    try:
        await query.edit_message_text(
            screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
        )
    except TelegramError as e:
        logger.info("Feedback: the answer stays as it was: %s", type(e).__name__)


def _named_person(message) -> invites.Person | None:
    """Who a forward or a contact names; None if Telegram hides them."""
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        user = origin.sender_user
        return invites.Person(user.id, user.first_name, user.last_name, user.username)
    contact = message.contact
    if contact is not None and contact.user_id is not None:
        return invites.Person(contact.user_id, contact.first_name, contact.last_name)
    return None


async def _tell_owner(message, text: str, **options) -> None:
    """A moment of Telegram is tried again, and past it the reply is lost, not the turn:
    an error here would carry on past ``invite_intake`` and put what the owner forwarded
    into the owner's diary."""
    try:
        await _tries(lambda: message.reply_text(text, **options))
    except TelegramError as e:
        logger.warning("Invitation reply not sent: %s", type(e).__name__)


async def _let_in(message, context, person: invites.Person) -> None:
    """Every way out of here gives the main keyboard back."""
    context.user_data.pop(_INVITE_UNTIL, None)
    if person.telegram_id in settings.admin_ids:
        await _tell_owner(message, people_screen.YOURSELF, reply_markup=main_keyboard)
        return
    try:
        with SessionLocal() as db:
            invites.invite(db, person, message.from_user.id)
    except Exception as e:
        logger.error("Invitation not written: %s", type(e).__name__)
        await _tell_owner(message, people_screen.FAILED, reply_markup=main_keyboard)
        return
    await _tell_owner(
        message,
        people_screen.notice_opened(person.name),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard,
    )
    delivered = False
    if settings.access_mode != "closed":
        delivered = await _invite_directly(context.bot, person.telegram_id)
    screen = people_screen.invited(context.bot.username, delivered, person)
    await _tell_owner(
        message, screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
    )


async def _invite_directly(bot, telegram_id: int) -> bool:
    """Only Telegram knows whether this person ever pressed Start here, and asking is the
    send itself. A refusal is the usual answer and leaves the share button."""
    try:
        words = people_screen.invitation(invites.DEFAULT_MONTHS)
        await bot.send_message(telegram_id, words)
    except TelegramError as e:
        logger.info("Invitation not delivered: %s", type(e).__name__)
        return False
    return True


async def invite_intake(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """What belongs to the invitation, before the slots, the conversation and the diary:
    a person picked, the two labels of the picker, and while it waits a forward or a
    contact. Anything else, food too, goes on to the diary as ever."""
    message = update.message
    is_owner = update.effective_user.id in settings.admin_ids
    if message.users_shared is not None:
        if is_owner:
            await _picked(message, context)
        else:
            logger.warning("A person picked by a non-owner: ignored")
        raise ApplicationHandlerStop
    if message.text == people_screen.CANCEL:
        if is_owner:
            context.user_data.pop(_INVITE_UNTIL, None)
            await _tell_owner(
                message, people_screen.CANCELLED, reply_markup=main_keyboard
            )
        raise ApplicationHandlerStop
    if message.text == people_screen.PICK:
        if is_owner:
            # This label arrives as text only from a client that cannot pick.
            context.user_data[_INVITE_UNTIL] = time.monotonic() + INVITE_WAIT_SECONDS
            await _tell_owner(
                message,
                people_screen.FALLBACK,
                reply_markup=people_screen.picker_keyboard(),
            )
        raise ApplicationHandlerStop
    until = context.user_data.get(_INVITE_UNTIL)
    if until is None or not is_owner:
        return
    if time.monotonic() > until:
        context.user_data.pop(_INVITE_UNTIL, None)
        await _tell_owner(message, people_screen.EXPIRED, reply_markup=main_keyboard)
        return
    if (message.text or "").startswith("/"):
        context.user_data.pop(_INVITE_UNTIL, None)
        await _tell_owner(message, people_screen.CANCELLED, reply_markup=main_keyboard)
        return
    if message.forward_origin is None and message.contact is None:
        return
    person = _named_person(message)
    if person is None:
        await _tell_owner(
            message,
            people_screen.HIDDEN_ORIGIN,
            reply_markup=people_screen.picker_keyboard(),
        )
    elif isinstance(message.forward_origin, MessageOriginUser) and (
        message.forward_origin.sender_user.is_bot
    ):
        await _tell_owner(
            message, people_screen.A_BOT, reply_markup=people_screen.picker_keyboard()
        )
    else:
        await _let_in(message, context, person)
    raise ApplicationHandlerStop


async def _picked(message, context) -> None:
    shared = message.users_shared
    if shared.request_id != people_screen.REQUEST_ID or not shared.users:
        logger.warning("A person picked for an unknown request: %s", shared.request_id)
        context.user_data.pop(_INVITE_UNTIL, None)
        await _tell_owner(message, people_screen.FAILED, reply_markup=main_keyboard)
        return
    user = shared.users[0]
    await _let_in(
        message,
        context,
        invites.Person(user.user_id, user.first_name, user.last_name, user.username),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel and end the conversation."""
    await update.message.reply_text(
        "Действие отменено. Чем могу помочь?",
        reply_markup=main_keyboard,
    )
    context.user_data.clear()
    return CHOOSING_ACTION


async def _load_custom_emoji(application: Application) -> None:
    """Read the custom emoji sets at startup; a set that was named but could
    not be read is told to the owner, the bot starts with plain emoji."""
    emoji = application.bot.emoji
    await emoji.load(application.bot, settings.CUSTOM_EMOJI)
    if emoji.problem:
        await notify_admins(
            f"⚠️ Кастомные эмодзи не включились: {emoji.problem}. "
            "Бот работает с обычными эмодзи. Проверь CUSTOM_EMOJI."
        )


# What the bot asks Telegram for, polling and webhook alike: an edit comes only if asked.
ALLOWED_UPDATES = ["message", "edited_message", "callback_query"]


def create_bot_application() -> Application:
    """Create and configure the bot application."""
    # httpx/httpcore log the full request URL at INFO, and the Telegram Bot API
    # embeds the token in the path (.../bot<TOKEN>/getUpdates). Clamp them to
    # WARNING so the live token never lands in the logs (still surfaces failures).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Restore a model the owner picked after a past deprecation (TD-005). The
    # singleton also loads it at construction (TD-007); this re-asserts it in case
    # that construction happened before the DB was ready.
    apply_persisted_model(telegram_service.openai_service)

    # The bot draws its marks with custom emoji when CUSTOM_EMOJI names the sets;
    # unset, every call goes out as before (app/services/custom_emoji.py).
    bot = custom_emoji.build_bot(
        settings.TELEGRAM_BOT_TOKEN, custom_emoji.CustomEmoji()
    )
    application = (
        Application.builder()
        .bot(bot)
        .post_init(_on_start)
        .post_shutdown(_on_stop)
        .build()
    )

    # Whose update this is, and what they did: before anything can pay for a model.
    application.add_handler(TypeHandler(Update, action_journal.note_update), group=-5)

    # Global access gate — runs before every other handler (group=-4) and drops
    # updates from users not allowed by the current BOT_ACCESS_MODE.
    application.add_handler(TypeHandler(Update, access_gate), group=-4)

    # A person picked, and the picker's labels: never food, never a slot's answer.
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE, invite_intake
        ),
        group=-3,
    )

    # The message the 💬 screen asked for. In its own group, ahead of the slot's: only
    # the first matching handler of a group runs, and the slot's matches every message.
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE, feedback_intake
        ),
        group=-2,
    )

    # The card's typed slot answers before the conversation sees the text; with no
    # slot it does nothing and the update goes on.
    application.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE, meal_slot_text), group=-1
    )
    application.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE, capture_edited), group=-1
    )

    # A callback handler inside a conversation tracked per chat, not per message, on
    # purpose: the callback data carries the step. PTB warns about exactly that setup.
    warnings.filterwarnings(
        "ignore", message="If 'per_message=False'", category=PTBUserWarning
    )

    # Add conversation handler
    # The main menu (main_keyboard): a tapped button comes back as its text.
    def menu():
        return [
            # «прием» is the label keyboards already on phones still carry.
            MessageHandler(filters.Regex("^🍽 Добавить при[её]м пищи$"), add_meal),
            MessageHandler(filters.Regex("^⭐ Избранное$"), show_favorites),
            MessageHandler(filters.Regex("^📊 Статистика$"), show_statistics),
            MessageHandler(filters.Regex("^⚖️ Мой вес$"), show_weight),
            MessageHandler(filters.Regex("^⚙️ Настройки$"), show_settings),
            MessageHandler(filters.Regex("^🍲 Блюда$"), show_dishes),
            CallbackQueryHandler(
                add_meal_from_card, pattern=f"^{stats_screen.ADD_DATA}$"
            ),
        ]

    routine_ask = CallbackQueryHandler(routine_ask_name, pattern=routines.ASK_PATTERN)
    food_ask = CallbackQueryHandler(food_ask_name, pattern=food_lists.ASK_PATTERN)
    # A photo, a file or a voice while a name is waited for is a meal, not the name.
    media_leaves_wait = MessageHandler(
        ~filters.TEXT & ~filters.COMMAND, _capture_and_leave_wait
    )
    old_lock_labels = MessageHandler(
        filters.Regex(f"^({'|'.join(_OLD_LOCK_LABELS)})$"), old_lock_label
    )
    conv_handler = ConversationHandler(
        # The menu buttons are entry points too: the conversation state lives in
        # memory, so after a bot restart they would otherwise do nothing until
        # /start. Everything else a person sends is the router's, below.
        entry_points=[
            CommandHandler("start", start),
            *menu(),
            old_lock_labels,
            routine_ask,
            food_ask,
        ],
        states={
            CHOOSING_ACTION: [
                *menu(),
                routine_ask,
                food_ask,
            ],
            ENTERING_FOOD_NAME: [
                *menu(),
                routine_ask,
                food_ask,
                CallbackQueryHandler(
                    food_leave_name, pattern=f"^{food_lists.CALLBACK_PREFIX}"
                ),
                media_leaves_wait,
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_food_name),
            ],
            ENTERING_ROUTINE_NAME: [
                *menu(),
                routine_ask,
                food_ask,
                CallbackQueryHandler(
                    routine_leave_name, pattern=f"^{routines.CALLBACK_PREFIX}"
                ),
                media_leaves_wait,
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_routine_name),
            ],
            ENTERING_WEIGHT: [
                *menu(),
                routine_ask,
                food_ask,
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            old_lock_labels,
        ],
    )

    application.add_handler(conv_handler)

    # Section 5.1: every private message the conversation did not take. No state of
    # the conversation is involved, so a wait never eats food.
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE & ~filters.COMMAND,
            capture_message,
        )
    )

    # ✅ ОК under a finished result: takes that message's buttons away.
    application.add_handler(
        CallbackQueryHandler(close_buttons, pattern=close_row.PATTERN)
    )

    # Favorites inline buttons (stateless; see favorites_callback).
    application.add_handler(
        CallbackQueryHandler(
            favorites_callback, pattern=f"^{favorites.CALLBACK_PREFIX}"
        )
    )

    # 🔔 reminders and their settings (stateless, like favorites).
    application.add_handler(
        CallbackQueryHandler(
            reminders_callback, pattern=f"^{reminders.CALLBACK_PREFIX}"
        )
    )

    # The dish base (stateless, like favorites). It holds a typed slot of its own, so
    # a tap here closes that itself and does not count as leaving one (see below).
    application.add_handler(
        CallbackQueryHandler(dishes_callback, pattern=f"^{dishes.CALLBACK_PREFIX}")
    )

    # Statistics tabs and the weight undo (stateless, like favorites).
    application.add_handler(
        CallbackQueryHandler(stats_callback, pattern=f"^{stats_screen.CALLBACK_PREFIX}")
    )
    application.add_handler(
        CallbackQueryHandler(
            weight_callback, pattern=f"^{weight_screen.CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            settings_callback, pattern=f"^{settings_screen.CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(routines_callback, pattern=f"^{routines.CALLBACK_PREFIX}")
    )
    application.add_handler(
        CallbackQueryHandler(
            food_lists_callback, pattern=f"^{food_lists.CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            explain_callback, pattern=f"^{explain_screen.CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            meal_card_callback, pattern=f"^{meal_card.CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            routine_left_by_tap, pattern=f"^(?!{routines.CALLBACK_PREFIX})"
        ),
        group=1,
    )
    application.add_handler(
        CallbackQueryHandler(
            food_left_by_tap, pattern=f"^(?!{food_lists.CALLBACK_PREFIX})"
        ),
        group=2,
    )
    application.add_handler(
        CallbackQueryHandler(
            meal_slot_left_by_tap,
            # ⚖️ Порция of a ⭐ opens a slot, and the pager's middle leaves one alone.
            pattern=(
                f"^(?!{meal_card.CALLBACK_PREFIX}|{dishes.CALLBACK_PREFIX}"
                f"|{favorites.CALLBACK_PREFIX}g:|{favorites.NOP}$)"
            ),
        ),
        group=3,
    )
    application.add_handler(
        CallbackQueryHandler(
            feedback_left_by_tap, pattern=f"^(?!{feedback_screen.CALLBACK_PREFIX})"
        ),
        group=4,
    )

    # Buttons of the retired draft, still on someone's screen.
    application.add_handler(
        CallbackQueryHandler(
            meal_stale_callback, pattern=f"^{meal_draft.CALLBACK_PREFIX}"
        )
    )

    # Add subscription management commands
    application.add_handler(CommandHandler("grant_sub", grant_subscription))

    # Letting people in, and the first run (owner only, but for the first run).
    application.add_handler(CommandHandler("invite", invite_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("feedback", feedback_command))
    application.add_handler(
        CallbackQueryHandler(
            feedback_callback, pattern=f"^{feedback_screen.CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            users_callback, pattern=f"^{people_screen.CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            welcome_callback, pattern=f"^{welcome_screen.CALLBACK_PREFIX}"
        )
    )

    # Consult relay → my-health hub
    application.add_handler(CommandHandler("consult", consult))

    # Replay failed/pending inbound messages after a model fix (owner only).
    application.add_handler(CommandHandler("reprocess", reprocess))

    # Models per task + shutdown-date check on demand (owner only, ADR-0004).
    application.add_handler(CommandHandler("models", models_command))

    return application
