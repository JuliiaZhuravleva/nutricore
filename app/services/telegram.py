import base64
import logging
import warnings
from datetime import UTC, datetime
from typing import Dict

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
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
from app.crud.crud_meal import crud_meal
from app.crud.crud_subscription import crud_subscription
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.meal import MealCreate
from app.services import ai_errors, custom_emoji, favorites
from app.services import inbound_message_service as inbound_msg
from app.services import meal_draft, settings_screen, stats_screen, weight_screen
from app.services.access_control import access_gate, admin_required
from app.services.admin_notify import notify_admins
from app.services.ai_call_log_service import analyze_and_log
from app.services.consult_service import consult_service
from app.services.loader import Loader
from app.services.model_selection import (
    apply_persisted_model,
    persist_model,
    persist_task_model,
)
from app.services.model_watch import render_status, run_model_watch
from app.services.numbers import shows_numbers_for
from app.services.openai_service import ModelUnavailableError, get_openai_service
from app.services.product_lookup_service import (
    ResolutionResult,
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
_last_consult: Dict[int, datetime] = {}

# Max inbound messages re-analyzed per /reprocess call — a cap on OpenAI spend
# per owner invocation. If more are pending, the owner just runs it again.
REPROCESS_BATCH_LIMIT = 20

# A used-up AI balance seen by someone other than the owner: tell the owner at
# most this often per provider (in this process), not once per meal.
CREDIT_ALERT_EVERY_SECONDS = 3600
_credit_alerted: Dict[str, datetime] = {}

# Conversation states
(
    CHOOSING_ACTION,
    ADDING_MEAL,
    ADDING_MEAL_TIME,
    ADDING_MEAL_PHOTO,
    CONFIRMING_MEAL,
    SUBSCRIPTION_INQUIRY,
    CHOOSING_MODEL,
    DISAMBIGUATING_PRODUCT,  # future: multiple-candidate product picker (A8/A9)
    ENTERING_WEIGHT,
) = range(9)

# Keyboard layouts
subscription_keyboard = ReplyKeyboardMarkup(
    [
        ["❓ О боте", "💎 Получить подписку"],
        ["📱 Связаться с админом"],
    ],
    resize_keyboard=True,
)

main_keyboard = ReplyKeyboardMarkup(
    [
        ["🍽 Добавить приём пищи", "⭐ Избранное"],
        ["📊 Статистика", "⚖️ Мой вес"],
        ["⚙️ Настройки"],
    ],
    resize_keyboard=True,
)

time_keyboard = ReplyKeyboardMarkup(
    [
        ["Сейчас"],
        ["Завтрак", "Обед", "Ужин"],
        ["Назад"],
    ],
    resize_keyboard=True,
)

# The only values meals.meal_type may hold. The keyboard above is the contract,
# but a Telegram user can type anything instead of pressing a button, and this
# text is persisted and exported — so an unrecognised answer stores no label
# rather than becoming one. Keep this in step with the keyboard.
_MEAL_TYPE_LABELS = ("Завтрак", "Обед", "Ужин")


def _meal_type_label(text: str | None) -> str | None:
    """Canonical meal-of-day label for an answer, or None if it is not one.

    "Сейчас" is not a meal of the day — it means "just now" — so it maps to None,
    which is what clears a previous answer.
    """
    if not text:
        return None
    cleaned = text.strip()
    for label in _MEAL_TYPE_LABELS:
        if cleaned.casefold() == label.casefold():
            return label
    return None


# The confirm step answers with inline buttons under the draft (meal_draft.py).
# Typed replies still work (TD-015): Да saves, Нет discards, a weight rescales,
# anything else is a correction rather than a silent reject-and-restart. These
# two are the texts of the former reply keyboard, still accepted when typed.
CONFIRM_YES = "✅ Да"
CONFIRM_YES_FAVORITE = "⭐ Да, и в избранное"

# Accepted spellings for the confirm prompt. Matched case-insensitively and with
# trailing punctuation stripped, so a lowercase ``да`` or a stray ``Нет.`` is not
# misread as a correction (the TD-015 papercut).
_CONFIRM_AFFIRM = {"да", "ага", "верно", "yes", "ок", "ok", "👍", "✅", "✅ да"}
_CONFIRM_REJECT = {"нет", "no", "❌"}
# «Отмена» drops the meal and goes back to the menu, like the draft's
# «✖️ Отмена» button; «Нет» asks again (2026-10-28, Julia).
_CONFIRM_CANCEL = {"отмена", "cancel", "✖️ отмена"}

# Shown when a resolution strategy ERRORED (as opposed to finding nothing): the
# numbers came from a shallower path than they should have.
_DEGRADED_LINE = "⚠️ часть путей поиска не сработала — числа могут быть грубее обычного"


def _confirm_intent(text: str | None) -> str:
    """Classify a reply to "Всё верно? (Да/Нет)" as
    affirm | affirm_favorite | reject | cancel | correction."""
    norm = (text or "").strip().lower().strip(".!…? ")
    if norm == CONFIRM_YES_FAVORITE.lower():
        return "affirm_favorite"
    if norm in _CONFIRM_AFFIRM:
        return "affirm"
    if norm in _CONFIRM_REJECT:
        return "reject"
    if norm in _CONFIRM_CANCEL:
        return "cancel"
    return "correction"


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


def subscription_required(func):
    """Decorator to check subscription before executing function"""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_subscription(update.effective_user.id):
            text = (
                "🔒 Эта функция доступна только для пользователей с подпиской.\n\n"
                "Базовая версия бота позволяет:\n"
                "• Узнать о возможностях бота\n"
                "• Связаться с администратором\n"
                "• Получить подписку\n\n"
                "Подробнее — кнопка «💎 Получить подписку»."
            )
            await update.message.reply_text(text, reply_markup=subscription_keyboard)
            return SUBSCRIPTION_INQUIRY
        return await func(update, context)

    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    if not await check_subscription(update.effective_user.id):
        text = (
            "👋 Привет! Я NutriCore — дневник питания и здоровья.\n\n"
            "🤖 Что я умею:\n"
            "• Разбирать еду по фото и описанию с помощью AI\n"
            "• Вести вес и статистику по дням\n\n"
            "💎 Для доступа к этим функциям нужна подписка."
        )
        await update.message.reply_text(text, reply_markup=subscription_keyboard)
        return SUBSCRIPTION_INQUIRY
    else:
        text = "✅ Подписка активна. Что делаем?"
        await update.message.reply_text(text, reply_markup=main_keyboard)
        return CHOOSING_ACTION


@subscription_required
async def add_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the meal addition process."""
    # Start clean: drop any leftover draft from an abandoned previous entry so a
    # new meal never inherits stale nutrition/photos/time.
    context.user_data["current_meal"] = {}
    context.user_data.pop("meal_time", None)
    # Also drop the label: it outlived an abandoned entry and would have been
    # attached to the NEXT meal (which may well be a different one).
    context.user_data.pop("meal_type", None)
    await update.message.reply_text(
        "Когда был приём пищи?",
        reply_markup=time_keyboard,
    )
    return ADDING_MEAL_TIME


@subscription_required
async def process_meal_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the meal time and ask for the meal description."""
    text = update.message.text

    if text == "Назад":
        await update.message.reply_text(
            "Что делаем?",
            reply_markup=main_keyboard,
        )
        return CHOOSING_ACTION

    # Store the meal time in context.
    #
    # Both branches record meal_time as NOW on purpose. "Завтрак" is a label, not
    # a clock reading: deriving 08:00 from it would invent a timestamp the user
    # never gave, and they may well be logging it at 16:00. What they DID tell us
    # is the label, and that is what gets stored (meals.meal_type).
    #
    # This handler is AUTHORITATIVE for both keys: it overwrites meal_type on
    # every answer, including clearing it for "Сейчас". Clearing it only in
    # add_meal covered one of the two ways back to this step — the bot's own
    # retry ("Нет" → "Когда был приём пищи?") re-enters here directly, so a
    # declined label survived and was written to the next meal.
    context.user_data["meal_time"] = datetime.now(UTC)
    label = _meal_type_label(text)
    if label is None:
        context.user_data.pop("meal_type", None)
    else:
        context.user_data["meal_type"] = label

    await update.message.reply_text(
        "Теперь опиши еду или отправь фото.\n\n"
        "Можешь также отправить и то, и другое.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )
    return ADDING_MEAL


@subscription_required
async def quick_meal_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A photo sent outside the meal steps is a meal eaten now.

    The same analysis as «🍽 Добавить приём пищи» → «Сейчас» → the photo, minus
    the two taps: before this the photo matched no handler and was dropped
    without a word. Starts clean like ``add_meal``.
    """
    context.user_data["current_meal"] = {}
    context.user_data["meal_time"] = datetime.now(UTC)
    context.user_data.pop("meal_type", None)
    return await process_meal_input(update, context)


# parse_nutrition is imported from product_lookup_service (where the pipeline
# logic lives).  Keep a private alias so existing tests referencing
# tg._parse_nutrition continue to work without change.
_parse_nutrition = parse_nutrition


def _image_data_url(image_bytes) -> str:
    """base64 data URL for OpenAI vision — never Telegram's token-bearing file URL
    (api.telegram.org/file/bot<TOKEN>/…), and no dependency on OpenAI fetching it."""
    return "data:image/jpeg;base64," + base64.b64encode(bytes(image_bytes)).decode()


def _schedule_personal_food_save(
    *,
    user_id: int,
    meal_id: int,
    nutrition: dict,
    resolution_signals: dict | None,
    resolution_source: str | None,
) -> None:
    """Enqueue the embed+upsert Celery task for the confirmed food (B4 / ADR-0003).

    Fire-and-forget: the Celery task writes to personal_foods in the background so
    the confirm reply is not delayed.  All failures are logged and swallowed — the
    personal DB is an optimisation layer and must never block the confirm reply.

    Canonical-name priority (ADR-0003 §B4 contract):
      1. saved_food_name — already in personal DB (saved_rag path)
      2. product_name   — OFF-matched product name (barcode_off / name_off path)
      3. first food     — vision / text analysis result
    """
    try:
        signals = resolution_signals or {}

        # Canonical name extraction
        canonical_name = (
            signals.get("saved_food_name")
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
                "_schedule_personal_food_save: no per-100g basis for %r "
                "(user_id=%d meal_id=%d portion=%r) — not saving to the personal DB",
                canonical_name,
                user_id,
                meal_id,
                nutrition.get("portion"),
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
                "_schedule_personal_food_save: %r has no usable calories "
                "(user_id=%d meal_id=%d) — not saving to the personal DB",
                canonical_name,
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
            "_schedule_personal_food_save: enqueued for user_id=%d"
            " meal_id=%d canonical=%r",
            user_id,
            meal_id,
            canonical_name,
        )
    except Exception:
        logger.warning(
            "_schedule_personal_food_save: failed to enqueue task"
            " (user_id=%d meal_id=%d) — ignored",
            user_id,
            meal_id,
            exc_info=True,
        )


def _source_badge(result: ResolutionResult | None) -> str:
    """Localised source/confidence badge for the reply, or empty for text inputs.

    Matches the ADR-0001 §6 confidence-tier taxonomy so the user always knows how
    much to trust the numbers (see also: docs/product-philosophy.md).
    """
    if result is None:
        return ""
    if result.source == "saved_rag":
        return "⭐ из твоей базы"
    if result.source == "barcode_off":
        # The tier is downgraded when OFF is missing macros for this product. The
        # badge has to follow, otherwise the downgrade exists only in the DB and
        # the user still reads "точно" next to a "—" macro.
        if result.confidence_tier != "high":
            return "📦 по штрих-коду (неполные данные)"
        return "📦 по штрих-коду (точно)"
    if result.source == "label_ocr":
        return "🏷️ с этикетки (проверь)"
    if result.source == "name_web":
        # Dynamic confidence_tier: "medium" when OFF re-query succeeded,
        # "low" when only web-prose numbers are available (ADR-0002 §6).
        if result.confidence_tier == "low":
            return "🌐 нашли в сети (сверь — веб)"
        return "🌐 нашли в сети (проверь)"
    if result.confidence_tier == "high":
        return "✅ из базы (точно)"
    if result.confidence_tier == "medium":
        return "🔍 нашли в базе (проверь)"
    return "📷 оценка по фото"


def _resolution_detail_lines(result: ResolutionResult | None) -> list:
    """Key intermediate values surfaced for high/medium-confidence results.

    Returns the EAN read and the matched product name. The gram basis used for
    scaling (A6 — CQ2) and the per-100g warning live in the draft's "Порция"
    line now (``meal_draft``), next to the buttons that correct them.
    Vision-only results carry no meaningful intermediate signals beyond the
    nutrition fields already shown in the reply.

    These lines let the user catch a wrong barcode read, wrong product match,
    or wrong portion estimate before confirming the meal
    (ADR-0001 §6 + §7, north-star transparency principle).
    """
    if result is None:
        return []
    signals = result.signals or {}
    # A strategy that BROKE (not one that merely found nothing) means the numbers
    # below came from a shallower path than the user would otherwise have got.
    # This is the only user-visible trace of a swallowed failure, so it fires even
    # for a vision-only result — that is precisely the case where a broken
    # strategy is what pushed us down to vision.
    # Only warn when the failure could actually have cost the user accuracy. If a
    # strategy broke but a HIGH-confidence path (a barcode match with complete
    # data) still won, the numbers are the best the pipeline can produce and
    # "числа могут быть грубее обычного" would contradict the badge beside it.
    # Keyed on a RECORDED error, not on outcome == "error": a strategy can return
    # a result AND have recorded a failure (NameWebSearchStrategy falls back to
    # web prose when its OFF re-query breaks). Reading only the outcome would show
    # a clean reply for numbers that came from the lowest-trust leg.
    has_error = any(a.get("error") for a in (signals.get("strategy_attempts") or []))
    degraded = has_error and result.confidence_tier != "high"
    if result.source == "vision":
        return [_DEGRADED_LINE] if degraded else []
    lines: list = []
    barcode_raw = signals.get("barcode_raw")
    if barcode_raw:
        lines.append(f"Штрих-код: {barcode_raw}")
    product_name = signals.get("product_name")
    if product_name:
        lines.append(f"Продукт: {product_name}")
    missing = signals.get("off_missing_macros") or []
    if missing:
        lines.append(f"⚠️ в базе нет: {', '.join(missing)} — впиши при подтверждении")
    if degraded:
        lines.append(_DEGRADED_LINE)
    return lines


def _start_draft(
    draft: dict,
    header: str,
    resolution_result: ResolutionResult | None,
    numbers: bool = False,
) -> dict:
    """Stamp the confirm view onto a fresh draft: badge, details, gram basis.

    A photo whose portion was estimated gets the size chips around that
    estimate (the samosa: 250 ккал at a guessed 100 г, really ~140 г). A
    database hit that could not be scaled carries per-100g numbers, so its
    base is 100 г and the weight button rescales it. Everything else rescales
    from whatever grams its own portion text names, or not at all.
    """
    nutrition = draft["nutrition"]
    if resolution_result is None:
        return meal_draft.start(
            draft,
            header=header,
            badge="",
            detail_lines=[],
            estimate=True,
            base_grams=_parse_portion_grams(nutrition),
            numbers=numbers,
        )
    grams = resolution_result.portion_grams
    estimated = grams is not None and grams > 0
    per_100g = not estimated and resolution_result.source != "vision"
    if estimated:
        base = float(grams)
    elif per_100g:
        base = 100.0
    else:
        base = _parse_portion_grams(nutrition)
    basis = None
    if estimated:
        signals = resolution_result.signals or {}
        basis = (
            "оценка по фото и твоему описанию"
            if signals.get("portion_source") == "vision+note"
            else "оценка по фото"
        )
    # The ⚠️ lines are caveats about the numbers: hidden numbers hide them too.
    details = _resolution_detail_lines(resolution_result)
    return meal_draft.start(
        draft,
        header=header,
        badge=_source_badge(resolution_result),
        detail_lines=[line for line in details if not line.startswith("⚠️")],
        number_notes=[line for line in details if line.startswith("⚠️")],
        estimate=resolution_result.confidence_tier != "high",
        base_grams=base,
        per_100g=per_100g,
        chips=estimated,
        basis=basis,
        numbers=numbers,
    )


def _nutrition_reply(
    data,
    header,
    resolution_result: ResolutionResult | None = None,
    numbers: bool = False,
):
    """The confirm message for freshly analyzed ``data`` (no buttons pressed).

    ``resolution_result`` is provided for image inputs so the reply can surface
    the source/confidence badge and key intermediate values (EAN, product name,
    gram basis). Text inputs pass ``None``.
    """
    draft = _start_draft({"nutrition": data}, header, resolution_result, numbers)
    return meal_draft.text(draft)


async def _analyze_and_parse(
    *, kind: str, payload: str, input_ref: str, telegram_id: int | None
) -> dict:
    """Run the OpenAI analysis for `kind`, record it to ai_call_logs, and return
    the parsed nutrition.

    The single analyze pipeline shared by the live flow (``_run_meal_analysis``)
    and the replay (``reprocess``) so both go through the same code AND the same
    audit trail. Raises ``ModelUnavailableError`` on a dead model and ``ValueError``
    on a malformed response (both handled by the callers).
    """
    svc = telegram_service.openai_service
    coro = (
        svc.analyze_food_image(payload)
        if kind == "image"
        else svc.analyze_food_entry(payload)
    )
    return await analyze_and_log(
        coro,
        kind=kind,
        input_ref=input_ref,
        telegram_id=telegram_id,
        model=svc.model_for("vision" if kind == "image" else "text"),
        parse=_parse_nutrition,
    )


async def _run_meal_analysis(update, context, *, kind: str, **kw) -> int:
    """``_analyze_meal`` under a loader: the analysis takes seconds (a web
    search, up to half a minute), and silence read as a dead bot. Metered:
    the owner may have asked to see what each analysis cost (⚙️ Настройки)."""
    text = "⏳ Смотрю фото…" if kind == "image" else "⏳ Считаю…"
    try:
        with SessionLocal() as db:
            show_cost = settings_screen.shows_costs(db, update.effective_user.id)
            show_numbers = shows_numbers_for(db, update.effective_user.id)
    except Exception as e:
        # A display preference must not stop the analysis itself.
        logger.warning("Could not read the display settings: %s", e, exc_info=True)
        show_cost = show_numbers = False
    with metering() as meter:
        async with Loader(update.message, text) as loader:
            return await _analyze_meal(
                update,
                context,
                loader,
                meter if show_cost else None,
                kind=kind,
                numbers=show_numbers,
                **kw,
            )


async def _out_of_credit_text(update: Update, err, *, saved: int | None) -> str:
    """What to say when the AI balance or spend limit is used up (``ai_errors``).

    The owner learns where to top up and that the meal waits for /reprocess;
    anyone else hears that the owner knows — and the owner is told, at most
    once an hour per provider, so a burst of meals is one alert.
    """
    provider, where = ai_errors.billing(err)
    head = f"💳 У {provider} закончился баланс или лимит трат на ИИ"
    user = update.effective_user
    if user is not None and user.id in settings.admin_ids:
        text = (
            f"{head} — разобрать еду сейчас не получится, повтор не поможет.\n"
            f"Пополни баланс или подними лимит: {where}."
        )
        if saved is not None:
            text += "\nСообщение сохранено: после пополнения отправь /reprocess."
        return text
    now = datetime.now(UTC)
    last = _credit_alerted.get(provider)
    told = (
        last is not None and (now - last).total_seconds() < CREDIT_ALERT_EVERY_SECONDS
    )
    if not told and await notify_admins(
        f"{head} — у пользователя {user.id if user else '?'} не разобралась еда.\n"
        f"Пополни баланс или подними лимит: {where}, потом /reprocess."
    ):
        _credit_alerted[provider] = now
        told = True
    owner = " — владельцу бота уже пришло уведомление" if told else ""
    return f"💳 Распознавание еды временно недоступно{owner}. Попробуй позже."


async def _analyze_meal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    loader: Loader,
    meter: Meter | None,
    *,
    kind: str,
    input_ref: str,
    payload: str,
    inbound_id: int | None = None,
    caption: str | None = None,
    numbers: bool = False,
) -> int:
    """Analyze one meal input (image data URL or text), reply, and stage the draft.

    Shared by the first attempt (``process_meal_input``) and the retry after a
    model switch (``process_model_choice``). On a model deprecation it offers the
    model picker (→ CHOOSING_MODEL); on any other failure it asks for the input
    again (→ ADDING_MEAL). The draft is committed only after a successful reply,
    so a failure never leaves half-written state in ``user_data``. ``inbound_id``
    (the persisted inbound message, TD-009) is flipped to analyzed/failed here.
    """
    try:
        if kind == "image":
            # Phase 1+2 pipeline: concurrent barcode+vision extraction, then
            # ordered strategy resolution (BarcodeOFFStrategy → VisionFallback).
            resolution_result = await resolve_meal_nutrition(
                payload,
                telegram_id=update.effective_user.id,
                input_ref=input_ref,
                caption=caption,
            )
            nutrition_info = resolution_result.nutrition
        else:
            resolution_result = None
            nutrition_info = await _analyze_and_parse(
                kind=kind,
                payload=payload,
                input_ref=input_ref,
                telegram_id=update.effective_user.id,
            )
    except ModelUnavailableError as e:
        # Configured model is gone — record the miss, then let the owner pick a
        # new model and retry (the same inbound_id is re-marked on success).
        inbound_msg.mark_failed(inbound_id, str(e))
        await loader.drop()  # the picker's reply keyboard needs a new message
        return await _offer_model_choice(
            update,
            context,
            e,
            kind=kind,
            input_ref=input_ref,
            payload=payload,
            inbound_id=inbound_id,
            caption=caption,
        )
    except Exception as e:
        logger.error("Error analyzing meal (%s): %s", kind, e, exc_info=True)
        inbound_msg.mark_failed(inbound_id, str(e))
        cost = f"\n\n{meter.line()}" if meter else ""
        refusal = ai_errors.out_of_credit(e)
        if refusal is not None:
            # A retry cannot pass until someone pays: back to the menu, not
            # «send it again».
            text = await _out_of_credit_text(update, refusal, saved=inbound_id)
            await loader.reply(text + cost, reply_markup=main_keyboard)
            return CHOOSING_ACTION
        if ai_errors.rate_limited(e):
            text = (
                "⏳ ИИ сейчас перегружен запросами. Подожди минуту и отправь ещё раз."
            )
        else:
            text = "Не получилось разобрать. Попробуй ещё раз или опиши еду иначе."
        await loader.reply(text + cost)
        return ADDING_MEAL

    # Analysis succeeded — record it now, independent of the reply below. A
    # Telegram send failure must NOT relabel a clean analysis as failed (that
    # would make /reprocess re-pay OpenAI for an already-analyzed message).
    inbound_msg.mark_analyzed(inbound_id, nutrition_info)

    if kind == "image":
        draft = {
            "nutrition": nutrition_info,
            "description": ", ".join(nutrition_info.get("foods", []))
            or "Фото приёма пищи",
            # Telegram file_id so the saved meal keeps a photo reference
            # (re-fetchable; cheaper than storing the bytes in the DB).
            "photos": [input_ref],
            # Resolution pipeline metadata (A1 columns): persisted in confirm_meal.
            "resolution_source": (
                resolution_result.source if resolution_result else None
            ),
            "resolution_signals": (
                resolution_result.signals if resolution_result else None
            ),
            # The owner's own words name a favorite better than the food list.
            "caption": caption,
        }
        header = "Разбор фото:"
    else:
        draft = {"nutrition": nutrition_info, "description": payload}
        header = "Разбор приёма пищи:"

    if meter:
        draft["cost_line"] = meter.line()
    _start_draft(draft, header, resolution_result, numbers)
    try:
        sent = await loader.reply(
            meal_draft.text(draft), reply_markup=meal_draft.markup(draft)
        )
    except Exception as e:
        # Analysis is saved; only the confirmation reply failed. Keep the draft
        # atomic (don't commit) and ask the owner to send the meal again.
        logger.error("Error sending meal confirmation (%s): %s", kind, e, exc_info=True)
        await update.message.reply_text(
            "Не получилось разобрать. Попробуй ещё раз или опиши еду иначе."
        )
        return ADDING_MEAL

    # Reply succeeded: commit the draft and clear any pending retry state. The
    # draft REPLACES the previous one: a correction's fresh analysis must not
    # inherit the old attempt's photo, resolution or button state.
    _remember_draft_message(draft, sent)
    context.user_data["current_meal"] = draft
    context.user_data.pop("pending_analysis", None)
    return CONFIRMING_MEAL


def _remember_draft_message(draft: dict, sent) -> None:
    """Where the draft's buttons are, so a newer draft can take them away."""
    draft["chat_id"] = getattr(sent, "chat_id", None)
    draft["msg_id"] = getattr(sent, "message_id", None)


async def _strip_draft_buttons(context: ContextTypes.DEFAULT_TYPE, draft: dict):
    """Remove the inline buttons from a draft's message once it is superseded,
    so an old size chip cannot be tapped against the new numbers. Best effort:
    the stale-button check (draft id) still guards a message this misses."""
    bot = getattr(context, "bot", None)
    if bot is None or not draft.get("msg_id") or draft.get("chat_id") is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=draft["chat_id"], message_id=draft["msg_id"], reply_markup=None
        )
    except Exception as e:
        logger.debug("Could not strip draft buttons: %s", e)


async def _resend_draft(
    update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict
) -> int:
    """The draft changed by a typed reply: a new message below it, with the
    buttons moved there (the old message would be out of sight above)."""
    await _strip_draft_buttons(context, draft)
    sent = await update.message.reply_text(
        meal_draft.text(draft), reply_markup=meal_draft.markup(draft)
    )
    _remember_draft_message(draft, sent)
    return CONFIRMING_MEAL


@subscription_required
async def process_meal_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the meal description or photo."""
    context.user_data.setdefault("current_meal", {})

    if update.message.photo:
        # Get the largest photo (best quality).
        photo = update.message.photo[-1]
        kind, input_ref, content = "image", photo.file_id, update.message.caption
    else:
        kind, input_ref, content = "text", update.message.text, update.message.text

    # Persist the raw inbound message on receipt, BEFORE any fetch/OpenAI work, so
    # even a photo-fetch or analysis failure leaves a replayable trace (TD-009).
    inbound_id = inbound_msg.record_inbound(
        telegram_id=update.effective_user.id,
        kind=kind,
        content=content,
        photo_file_id=input_ref if kind == "image" else None,
    )

    if kind == "image":
        try:
            # Send the image to OpenAI as a base64 data URL rather than Telegram's
            # file URL — that URL embeds the bot token (api.telegram.org/file/bot<TOKEN>/…)
            # and we don't want to hand it to a third party. It also avoids relying
            # on OpenAI being able to fetch from api.telegram.org.
            file = await context.bot.get_file(input_ref)
            image_bytes = await file.download_as_bytearray()
        except Exception as e:
            logger.error("Error fetching photo: %s", e, exc_info=True)
            inbound_msg.mark_failed(inbound_id, f"photo fetch failed: {e}")
            await update.message.reply_text(
                "Не удалось загрузить фото. Опиши еду текстом."
            )
            return ADDING_MEAL
        payload = _image_data_url(image_bytes)
    else:
        payload = input_ref
        logger.debug("Processing meal text: %s", payload)

    return await _run_meal_analysis(
        update,
        context,
        kind=kind,
        input_ref=input_ref,
        payload=payload,
        inbound_id=inbound_id,
        # `content` IS update.message.caption for photos. It was persisted to
        # inbound_messages and then dropped here — the user's own words about
        # brand and portion never reached the model (fixed 2026-09-13).
        caption=content if kind == "image" else None,
    )


async def _offer_model_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    err: ModelUnavailableError,
    *,
    kind: str,
    input_ref: str,
    payload: str,
    inbound_id: int | None = None,
    caption: str | None = None,
) -> int:
    """A model went missing mid-analysis: stash the input and show the owner a
    picker, so they can switch models and have the analysis retried automatically.

    Only an admin picks (ADR-0004): anyone else is told the owner already knows —
    the alert went out from ``OpenAIService`` when the failure was detected.
    """
    user = update.effective_user
    if user is None or user.id not in settings.admin_ids:
        await update.message.reply_text(
            "Распознавание сейчас недоступно — владельцу бота уже пришло "
            "уведомление. Попробуй позже.",
            reply_markup=main_keyboard,
        )
        return CHOOSING_ACTION

    svc = telegram_service.openai_service
    task = err.task or ("vision" if kind == "image" else "text")
    context.user_data["pending_analysis"] = {
        "kind": kind,
        "input_ref": input_ref,
        "payload": payload,
        "inbound_id": inbound_id,
        # Stashed too: the retry re-runs the analysis from scratch, and a caption
        # dropped here would silently degrade exactly the retry the owner runs
        # after a model outage.
        "caption": caption,
    }
    # Replace the model where it was configured: a task with its own pick gets a
    # new pick; a task on the default replaces the default, so one dead default
    # model takes one choice, not one per task.
    context.user_data["model_task"] = (
        None if err.task is None or svc.runs_on_default(task) else task
    )
    models = await svc.list_candidates(task, exclude=err.model)
    context.user_data["model_choices"] = models
    keyboard = ReplyKeyboardMarkup(
        [[m] for m in models] + [["Отмена"]], resize_keyboard=True
    )
    await update.message.reply_text(
        f"⚠️ Модель «{err.model}» недоступна (устарела, удалена или нет ключа).\n"
        "Выбери другую — я запомню её и сразу повторю анализ:",
        reply_markup=keyboard,
    )
    return CHOOSING_MODEL


@subscription_required
async def process_model_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Apply the owner's model pick, persist it, and retry the stashed analysis."""
    text = update.message.text
    if text == "Отмена":
        context.user_data.clear()
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard)
        return CHOOSING_ACTION

    user = update.effective_user
    if user is None or user.id not in settings.admin_ids:
        # The picker is only ever shown to an admin; this guards a stale state.
        context.user_data.clear()
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard)
        return CHOOSING_ACTION

    if text not in context.user_data.get("model_choices", []):
        await update.message.reply_text("Выбери модель кнопкой из списка.")
        return CHOOSING_MODEL

    task = context.user_data.pop("model_task", None)
    telegram_service.openai_service.set_model(text, task=task)
    if task is None:
        persist_model(text)
    else:
        persist_task_model(task, text)
    await update.message.reply_text(f"✅ Модель обновлена: {text}. Повторяю анализ…")

    pending = context.user_data.get("pending_analysis")
    if not pending:
        # No stashed input (shouldn't normally happen) — ask for it again.
        await update.message.reply_text(
            "Отправь фото или описание приёма пищи ещё раз.",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
        )
        return ADDING_MEAL
    return await _run_meal_analysis(
        update,
        context,
        kind=pending["kind"],
        input_ref=pending["input_ref"],
        payload=pending["payload"],
        inbound_id=pending.get("inbound_id"),
        caption=pending.get("caption"),
    )


@subscription_required
async def confirm_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A typed reply at the confirm step (the buttons go to ``meal_callback``).

    Five outcomes (TD-015): **Да** saves; **Нет** discards the draft and
    restarts; **Отмена** discards it and goes back to the menu; a weight ("150 г", or a bare number after «Поправить вес»)
    rescales the draft; anything else is a *correction* — re-analyzed together
    with what came before, the photo included, while staying at this step.
    """
    draft = context.user_data.get("current_meal") or {}
    text = update.message.text
    intent = _confirm_intent(text)
    awaiting = draft.get("awaiting")

    if intent == "correction" and meal_draft.can_rescale(draft):
        # A bare "2" may mean two pieces — only a weight that was asked for, or
        # one with its unit, is read as grams.
        grams = meal_draft.parse_grams(text, unit_required=awaiting != "grams")
        if grams is not None:
            meal_draft.set_grams(draft, grams)
            draft["awaiting"] = None
            return await _resend_draft(update, context, draft)
        if awaiting == "grams":
            await update.message.reply_text(
                "Вес не распознан — напиши число граммов, например 150."
            )
            return CONFIRMING_MEAL

    if intent == "correction":
        return await _reanalyze_with_correction(update, context, draft, text)

    if intent in ("affirm", "affirm_favorite"):
        await _strip_draft_buttons(context, draft)
        saved = await _save_current_meal(
            update.effective_user,
            draft,
            context.user_data,
            update.message.reply_text,
            favorite=intent == "affirm_favorite",
        )
        if not saved:
            return CHOOSING_ACTION
        context.user_data.clear()
        return CHOOSING_ACTION

    if intent == "cancel":
        await _strip_draft_buttons(context, draft)
        context.user_data.clear()
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard)
        return CHOOSING_ACTION

    # Rejecting discards the draft — the retry is a fresh entry and must not
    # inherit stale nutrition/photos from the rejected attempt.
    await _strip_draft_buttons(context, draft)
    context.user_data["current_meal"] = {}
    await update.message.reply_text(
        "Давай попробуем ещё раз. Когда был приём пищи?",
        reply_markup=time_keyboard,
    )
    return ADDING_MEAL_TIME


def _correction_caption(draft: dict, correction: str) -> str:
    """The photo's caption for a re-analysis: the original words, the
    correction, and a weight the user already set (it outlives a product fix).

    The original caption stays on the first line — a favorite is named by it.
    """
    parts = [draft.get("caption"), correction]
    if draft.get("grams_by_user"):
        grams = meal_draft.num(draft["nutrition"]["portion_grams"], 0)
        parts.append(f"Съедено {grams} г")
    return "\n".join(p.strip() for p in parts if p and p.strip())


async def _reanalyze_with_correction(
    update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict, correction: str
) -> int:
    """Re-run the analysis with the user's correction, keeping the input.

    A photo draft re-reads the same photo with the correction as part of its
    caption (the caption beats the photo guess), so a wrong product is fixed
    without sending the photo again. A text draft is re-analyzed as the old
    description plus the correction. meal_time and meal_type live outside the
    draft and are kept.
    """
    await _strip_draft_buttons(context, draft)
    user_id = update.effective_user.id
    photos = draft.get("photos") or []
    if photos:
        file_id = photos[0]
        caption = _correction_caption(draft, correction)
        inbound_id = inbound_msg.record_inbound(
            telegram_id=user_id, kind="image", content=caption, photo_file_id=file_id
        )
        try:
            file = await context.bot.get_file(file_id)
            image_bytes = await file.download_as_bytearray()
        except Exception as e:
            # Not a dead end: the correction still goes through, as text.
            logger.error("Error re-fetching photo: %s", e, exc_info=True)
            inbound_msg.mark_failed(inbound_id, f"photo fetch failed: {e}")
        else:
            return await _run_meal_analysis(
                update,
                context,
                kind="image",
                input_ref=file_id,
                payload=_image_data_url(image_bytes),
                inbound_id=inbound_id,
                caption=caption,
            )

    previous = (draft.get("description") or "").strip()
    payload = f"{previous}\nУточнение: {correction}" if previous else correction
    inbound_id = inbound_msg.record_inbound(
        telegram_id=user_id, kind="text", content=payload, photo_file_id=None
    )
    return await _run_meal_analysis(
        update,
        context,
        kind="text",
        input_ref=payload,
        payload=payload,
        inbound_id=inbound_id,
    )


async def _save_current_meal(user, draft: dict, user_data: dict, reply, *, favorite):
    """Save the draft as a meal; True if it was saved.

    ``reply`` sends the outcome (a message's ``reply_text``): the typed "Да"
    and the «Сохранить» button end the same way.
    """
    try:
        nutrition = draft.get("nutrition", {})
        meal_in = MealCreate(
            description=draft.get("description"),
            meal_time=user_data.get("meal_time", datetime.now(UTC)),
            # The answer to "Когда был приём пищи?" — collected since the
            # first version, stored since 2026-09-13.
            meal_type=user_data.get("meal_type"),
            calories=nutrition.get("calories"),
            proteins=nutrition.get("protein"),
            fats=nutrition.get("fats"),
            carbohydrates=nutrition.get("carbs"),
            nutrients=meal_draft.stored_nutrients(draft),
            photos=draft.get("photos", []),
            # What the analysis said, before any portion change by the user,
            # plus the food groups the user confirmed.
            ai_analysis=meal_draft.stored_analysis(draft),
            # Pipeline resolution tracking (A1 columns).
            resolution_source=draft.get("resolution_source"),
            resolution_signals=draft.get("resolution_signals"),
        )

        with SessionLocal() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            if not db_user:
                db_user = User(telegram_id=user.id, username=user.username)
                db.add(db_user)
                db.commit()
                db.refresh(db_user)
            meal = crud_meal.create(db, meal_in, db_user.id)
            saved_user_id = db_user.id
            saved_meal_id = meal.id

        # B4 learning loop: fire-and-forget embed+upsert (outside the DB
        # context so SessionLocal is released before Celery dispatch).
        _schedule_personal_food_save(
            user_id=saved_user_id,
            meal_id=saved_meal_id,
            nutrition=nutrition,
            resolution_signals=draft.get("resolution_signals"),
            resolution_source=draft.get("resolution_source"),
        )

        text = "✅ Записано"
        if favorite:
            text += "\n" + _save_favorite(saved_meal_id, draft)
        await reply(text, reply_markup=main_keyboard)
        return True
    except Exception as e:
        logger.error(f"Error saving meal: {e}", exc_info=True)
        await reply(
            "Не получилось сохранить. Попробуй ещё раз.",
            reply_markup=main_keyboard,
        )
        return False


def _day_card_button() -> InlineKeyboardMarkup:
    """Under a saved draft: the day, where the new meal now shows."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 Карточка дня",
                    callback_data=stats_screen.CALLBACK_PREFIX + stats_screen.TODAY,
                )
            ]
        ]
    )


async def _edit_draft_message(
    query, draft: dict, footer: str | None = None, settled_markup=None
):
    """Redraw the draft in place; a settled draft (``footer``) loses its buttons,
    bar ``settled_markup``."""
    markup = settled_markup if footer else meal_draft.markup(draft)
    try:
        await query.edit_message_text(
            meal_draft.text(draft, footer=footer), reply_markup=markup
        )
    except BadRequest as e:
        # A repeated tap that leads to the same screen: nothing to change.
        if "not modified" not in str(e).lower():
            raise


async def meal_stale_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on the buttons of a draft that is no longer the current one (saved,
    cancelled, corrected, or lost to a restart): say so and drop the buttons."""
    query = update.callback_query
    await query.answer(
        "Этот черновик уже неактуален — отправь приём пищи заново", show_alert=True
    )
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        pass


async def meal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on the draft's inline buttons (``meal:<draft id>:<verb>``)."""
    query = update.callback_query
    if not await check_subscription(update.effective_user.id):
        await query.answer("🔒 Доступно с подпиской", show_alert=True)
        return None
    draft = context.user_data.get("current_meal") or {}
    parsed = meal_draft.parse_callback(query.data)
    if parsed is None:
        logger.warning("meal draft: malformed callback data %r", query.data)
    if parsed is None or parsed[0] != draft.get("id"):
        await meal_stale_callback(update, context)
        return None
    _draft_id, verb, arg = parsed

    if verb == meal_draft.SIZE:
        if not meal_draft.set_size(draft, arg):
            await query.answer()
            return None
        draft["awaiting"] = None
        await query.answer(f"{meal_draft.SIZES[arg][0]} порция")
        await _edit_draft_message(query, draft)
        return None
    if verb == meal_draft.NUMBERS:
        if not meal_draft.toggle_numbers(draft):
            await query.answer()
            return None
        await query.answer()
        await _edit_draft_message(query, draft)
        return None
    if verb in (meal_draft.GROUPS_OPEN, meal_draft.GROUPS_CLOSE):
        draft["groups_open"] = verb == meal_draft.GROUPS_OPEN
        draft["awaiting"] = None
        await query.answer()
        await _edit_draft_message(query, draft)
        return None
    if verb == meal_draft.GROUP_TOGGLE:
        if not meal_draft.toggle_group(draft, arg):
            logger.warning("meal draft: unknown group in callback %r", query.data)
            await query.answer()
            return None
        await query.answer()
        await _edit_draft_message(query, draft)
        return None
    if verb == meal_draft.ASK_GRAMS:
        draft["awaiting"] = "grams"
        await query.answer("Напиши вес в граммах")
        await _edit_draft_message(query, draft)
        return None
    if verb == meal_draft.ASK_FOOD:
        draft["awaiting"] = "food"
        await query.answer("Напиши, что это")
        await _edit_draft_message(query, draft)
        return None
    if verb in (meal_draft.SAVE, meal_draft.SAVE_FAVORITE):
        await query.answer()
        saved = await _save_current_meal(
            update.effective_user,
            draft,
            context.user_data,
            query.message.reply_text,
            favorite=verb == meal_draft.SAVE_FAVORITE,
        )
        if not saved:
            await _edit_draft_message(query, draft, footer="⚠️ Не сохранено")
            return CHOOSING_ACTION
        # The meal is written: a failed redraw must not leave the draft live, or
        # another tap on Save writes it twice. «✅ Записано» has already gone out.
        try:
            await _edit_draft_message(
                query, draft, footer="✅ Сохранено", settled_markup=_day_card_button()
            )
        except TelegramError as e:
            logger.warning("Saved draft not redrawn: %s", type(e).__name__)
        context.user_data.clear()
        return CHOOSING_ACTION
    if verb == meal_draft.CANCEL:
        await query.answer("Отменено")
        await _edit_draft_message(query, draft, footer="✖️ Отменено")
        context.user_data.clear()
        await query.message.reply_text("Отменено.", reply_markup=main_keyboard)
        return CHOOSING_ACTION
    logger.warning("meal draft: unknown verb in callback data %r", query.data)
    await query.answer()
    return None


def _save_favorite(meal_id: int, current_meal: dict) -> str:
    """Add the just-saved meal to favorites; the line to append to the reply.

    Runs after the meal is committed and never raises: a favorites failure must
    not turn a saved meal into "ошибка при сохранении".
    """
    name = favorites.favorite_name(
        caption=current_meal.get("caption"),
        description=current_meal.get("description"),
        foods=current_meal.get("nutrition", {}).get("foods"),
    )
    try:
        with SessionLocal() as db:
            meal = crud_meal.get(db, meal_id)
            fav, created = favorites.save_from_meal(db, meal, name)
            name = fav.name
    except Exception as e:
        logger.error("Error saving favorite for meal %s: %s", meal_id, e, exc_info=True)
        return "⚠️ В избранное добавить не получилось — приём пищи сохранён."
    if created:
        return f"⭐ В избранном: «{name}»"
    return f"⭐ «{name}» уже в избранном"


@subscription_required
async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The favorites list (menu button «⭐ Избранное»)."""
    with SessionLocal() as db:
        screen = favorites.open_list(db, update.effective_user.id)
    await update.message.reply_text(
        screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
    )
    return CHOOSING_ACTION


async def _screen_callback(update: Update, handle, locked: str, what: str):
    """A tap on a screen's inline button: ``handle(db, telegram_id, data)``
    gives the next screen, which replaces the message in place.

    Registered outside the ConversationHandler: the callback data carries the
    whole step, so it works in any conversation state and after a restart.
    """
    query = update.callback_query
    if not await check_subscription(update.effective_user.id):
        await query.answer(locked, show_alert=True)
        return
    try:
        with SessionLocal() as db:
            screen = handle(db, update.effective_user.id, query.data or "")
    except Exception as e:
        logger.error("%s callback %r failed: %s", what, query.data, e, exc_info=True)
        await query.answer("Не получилось, попробуй ещё раз", show_alert=True)
        return
    await query.answer(screen.toast)
    if not screen.text:
        return
    try:
        await query.edit_message_text(
            screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
        )
    except BadRequest as e:
        # A repeated tap that leads to the same screen: nothing to change.
        if "not modified" not in str(e).lower():
            raise


async def favorites_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on a favorites inline button (``fav:…``)."""
    await _screen_callback(
        update,
        favorites.handle_callback,
        "🔒 Избранное доступно с подпиской",
        "Favorites",
    )


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A statistics tab (``st:…``)."""
    await _screen_callback(
        update,
        stats_screen.handle_callback,
        "🔒 Статистика доступна с подпиской",
        "Stats",
    )


async def weight_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The weight screen's undo (``wt:…``)."""
    await _screen_callback(
        update,
        weight_screen.handle_callback,
        "🔒 Дневник веса доступен с подпиской",
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
    await _reply_screen(update, screen)
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
        "🔒 Настройки доступны с подпиской",
        "Settings",
    )


async def handle_subscription_inquiry(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle subscription related questions"""
    text = update.message.text

    if text == "❓ О боте":
        response = (
            "🤖 NutriCore — дневник питания и здоровья.\n\n"
            "✨ С подпиской:\n"
            "• Разбор еды по фото и описанию\n"
            "• Избранные блюда в одно касание\n"
            "• Статистику за день и неделю\n"
            "• Дневник веса"
        )

    elif text == "💎 Получить подписку":
        response = (
            "💫 Подписка открывает доступ ко всем функциям бота!\n\n"
            "Стоимость:\n"
            "• 1 месяц - X руб\n"
            "• 3 месяца - Y руб\n"
            "• 12 месяцев - Z руб\n\n"
            "Чтобы оформить, нажми «📱 Связаться с админом»."
        )

    elif text == "📱 Связаться с админом":
        admin_username = settings.TELEGRAM_ADMIN_USERNAME
        response = f"По вопросам подписки пиши администратору: {admin_username}"

    else:
        return SUBSCRIPTION_INQUIRY

    await update.message.reply_text(response, reply_markup=subscription_keyboard)
    return SUBSCRIPTION_INQUIRY


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
            subscription = crud_subscription.create_subscription(
                db, user_id, admin_id, months
            )

        if subscription:
            logger.info(f"Successfully granted subscription to user {user_id}")
            await update.message.reply_text(
                f"✅ Подписка выдана пользователю {user_id} на {months} месяцев"
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
    """Re-analyze pending/failed inbound messages (owner only — TD-009).

    The replay mechanism after a model fix: switch the model via the self-heal
    picker, then run /reprocess to re-run what failed. Each message is re-analyzed
    through the same pipeline as the live flow (recorded to ai_call_logs) and the
    result is written back to its row (fills ai_analysis, flips status). It does
    NOT create a Meal — turning a recovered analysis into a logged meal is a
    separate step (deferred, TD-010); this only repairs the record. Capped at
    REPROCESS_BATCH_LIMIT per call to bound OpenAI spend; run again for more. If
    the model is still unavailable it stops early and says so.
    """
    try:
        rows = inbound_msg.get_reprocessable(limit=REPROCESS_BATCH_LIMIT)
    except Exception as e:
        logger.error("reprocess: could not load queue: %s", e, exc_info=True)
        await update.message.reply_text("Не удалось получить список сообщений.")
        return

    if not rows:
        await update.message.reply_text("Нет сообщений для повторной обработки. 👍")
        return

    analyzed = failed = 0
    for row in rows:
        try:
            if row.kind == "image":
                if not row.photo_file_id:
                    raise ValueError("no photo reference to re-fetch")
                file = await context.bot.get_file(row.photo_file_id)
                image_bytes = await file.download_as_bytearray()
                payload = _image_data_url(image_bytes)
                input_ref = row.photo_file_id
                # Use the full pipeline so reprocessed images also benefit from
                # barcode→OFF lookup (same quality as the live flow).
                resolution_result = await resolve_meal_nutrition(
                    payload,
                    telegram_id=row.telegram_id,
                    input_ref=input_ref,
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
            # Still broken — leave this row queued and tell the owner to switch.
            await update.message.reply_text(
                f"⚠️ Модель «{e.model}» всё ещё недоступна. Смени модель (отправь "
                "приём пищи, выбери модель) и повтори /reprocess.\n"
                f"Обработано до остановки: успешно {analyzed}, с ошибкой {failed}."
            )
            return
        except Exception as e:
            logger.error("reprocess: message %s failed: %s", row.id, e, exc_info=True)
            inbound_msg.mark_failed(row.id, str(e))
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
        f"Повторная обработка ({len(rows)}): успешно {analyzed}, с ошибкой {failed}."
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
    application = Application.builder().bot(bot).post_init(_load_custom_emoji).build()

    # Global access gate — runs before every other handler (group=-1) and drops
    # updates from users not allowed by the current BOT_ACCESS_MODE.
    application.add_handler(TypeHandler(Update, access_gate), group=-1)

    # The confirm step's inline buttons are tracked per chat, not per message, on
    # purpose: the draft id in the callback data already tells the current
    # draft's buttons from an old one's. PTB warns about exactly that setup.
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
        ]

    conv_handler = ConversationHandler(
        # The menu buttons are entry points too: the conversation state lives in
        # memory, so after a bot restart they would otherwise do nothing until
        # /start.
        entry_points=[
            CommandHandler("start", start),
            *menu(),
            # A bare weight ("72,5") outside any step: the weight screen's
            # prompt still holds after a restart or an undo.
            MessageHandler(filters.Regex(weight_screen.KG_PATTERN), process_weight),
            MessageHandler(filters.PHOTO, quick_meal_photo),
        ],
        states={
            CHOOSING_ACTION: [
                *menu(),
                MessageHandler(filters.Regex(weight_screen.KG_PATTERN), process_weight),
                MessageHandler(filters.PHOTO, quick_meal_photo),
            ],
            ENTERING_WEIGHT: [
                *menu(),
                MessageHandler(filters.PHOTO, quick_meal_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_weight),
            ],
            ADDING_MEAL_TIME: [
                MessageHandler(filters.PHOTO, quick_meal_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_meal_time),
            ],
            ADDING_MEAL: [
                # Ahead of the analysis: «Отмена» and the menu buttons are not
                # food, and sending them to the model costs money. The menu
                # keyboard is still on screen after a failed quick photo.
                MessageHandler(filters.Regex("^Отмена$"), cancel),
                *menu(),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
                    process_meal_input,
                ),
            ],
            CONFIRMING_MEAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_meal),
                CallbackQueryHandler(
                    meal_callback, pattern=f"^{meal_draft.CALLBACK_PREFIX}"
                ),
            ],
            CHOOSING_MODEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_model_choice),
            ],
            SUBSCRIPTION_INQUIRY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, handle_subscription_inquiry
                )
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^❓ О боте$"), start),
            MessageHandler(
                filters.Regex("^💎 Получить подписку$"), handle_subscription_inquiry
            ),
        ],
    )

    application.add_handler(conv_handler)

    # Favorites inline buttons (stateless; see favorites_callback).
    application.add_handler(
        CallbackQueryHandler(
            favorites_callback, pattern=f"^{favorites.CALLBACK_PREFIX}"
        )
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

    # Draft buttons tapped outside the confirm step (after a save, a cancel or
    # a restart): the conversation above did not take them.
    application.add_handler(
        CallbackQueryHandler(
            meal_stale_callback, pattern=f"^{meal_draft.CALLBACK_PREFIX}"
        )
    )

    # Add subscription management commands
    application.add_handler(CommandHandler("grant_sub", grant_subscription))

    # Consult relay → my-health hub
    application.add_handler(CommandHandler("consult", consult))

    # Replay failed/pending inbound messages after a model fix (owner only).
    application.add_handler(CommandHandler("reprocess", reprocess))

    # Models per task + shutdown-date check on demand (owner only, ADR-0004).
    application.add_handler(CommandHandler("models", models_command))

    return application
