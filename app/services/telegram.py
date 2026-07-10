import base64
import logging
from datetime import UTC, datetime
from typing import Dict

import httpx
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.core.config import settings
from app.crud.crud_app_setting import crud_app_setting
from app.crud.crud_meal import crud_meal
from app.crud.crud_subscription import crud_subscription
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.meal import MealCreate
from app.services import inbound_message_service as inbound_msg
from app.services.access_control import access_gate, admin_required
from app.services.ai_call_log_service import analyze_and_log
from app.services.consult_service import consult_service
from app.services.openai_service import (
    OPENAI_MODEL_SETTING_KEY,
    ModelUnavailableError,
    get_openai_service,
)
from app.services.product_lookup_service import (
    ResolutionResult,
    parse_nutrition,
    resolve_meal_nutrition,
)

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
) = range(8)

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
        ["🍽 Добавить прием пищи", "📊 Статистика"],
        ["⚖️ Мой вес", "⚙️ Настройки"],
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

# Confirm-step keyboard (TD-015): explicit Да / Нет so the owner never has to
# guess the exact word, and a free-text reply is handled as a correction rather
# than a silent reject-and-restart.
confirm_keyboard = ReplyKeyboardMarkup(
    [["Да", "Нет"]],
    resize_keyboard=True,
)

# Accepted spellings for the confirm prompt. Matched case-insensitively and with
# trailing punctuation stripped, so a lowercase ``да`` or a stray ``Нет.`` is not
# misread as a correction (the TD-015 papercut).
_CONFIRM_AFFIRM = {"да", "ага", "верно", "yes", "ок", "ok", "👍", "✅"}
_CONFIRM_REJECT = {"нет", "отмена", "no", "cancel", "❌"}


def _confirm_intent(text: str | None) -> str:
    """Classify a reply to "Всё верно? (Да/Нет)" as affirm | reject | correction."""
    norm = (text or "").strip().lower().strip(".!…? ")
    if norm in _CONFIRM_AFFIRM:
        return "affirm"
    if norm in _CONFIRM_REJECT:
        return "reject"
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
                "Нажмите кнопку 'Получить подписку' чтобы узнать больше!"
            )
            await update.message.reply_text(text, reply_markup=subscription_keyboard)
            return SUBSCRIPTION_INQUIRY
        return await func(update, context)

    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    if not await check_subscription(update.effective_user.id):
        text = (
            "👋 Привет! Я NutriCore бот - ваш персональный помощник в отслеживании питания и здоровья!\n\n"
            "🤖 Что я умею:\n"
            "• Анализировать приемы пищи с помощью AI\n"
            "• Отслеживать калории и нутриенты\n"
            "• Интегрироваться с Mi Scale и Samsung Health\n"
            "• Предоставлять детальную аналитику\n\n"
            "💎 Для доступа к этим функциям нужна подписка."
        )
        await update.message.reply_text(text, reply_markup=subscription_keyboard)
        return SUBSCRIPTION_INQUIRY
    else:
        text = "✅ У вас активная подписка! Выберите действие:"
        await update.message.reply_text(text, reply_markup=main_keyboard)
        return CHOOSING_ACTION


@subscription_required
async def add_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the meal addition process."""
    # Start clean: drop any leftover draft from an abandoned previous entry so a
    # new meal never inherits stale nutrition/photos/time.
    context.user_data["current_meal"] = {}
    context.user_data.pop("meal_time", None)
    await update.message.reply_text(
        "Когда был прием пищи?",
        reply_markup=time_keyboard,
    )
    return ADDING_MEAL_TIME


@subscription_required
async def process_meal_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the meal time and ask for the meal description."""
    text = update.message.text

    if text == "Назад":
        await update.message.reply_text(
            "Выберите действие:",
            reply_markup=main_keyboard,
        )
        return CHOOSING_ACTION

    # Store the meal time in context
    if text == "Сейчас":
        context.user_data["meal_time"] = datetime.now(UTC)
    else:
        # Here we'll need to add logic to parse different meal times
        context.user_data["meal_time"] = datetime.now(
            UTC
        )  # For now, default to current time
        context.user_data["meal_type"] = text

    await update.message.reply_text(
        "Отлично! Теперь опиши, что ты ел(а), или отправь фото.\n\n"
        "Можешь также отправить и то, и другое.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )
    return ADDING_MEAL


# parse_nutrition is imported from product_lookup_service (where the pipeline
# logic lives).  Keep a private alias so existing tests referencing
# tg._parse_nutrition continue to work without change.
_parse_nutrition = parse_nutrition


def _image_data_url(image_bytes) -> str:
    """base64 data URL for OpenAI vision — never Telegram's token-bearing file URL
    (api.telegram.org/file/bot<TOKEN>/…), and no dependency on OpenAI fetching it."""
    return "data:image/jpeg;base64," + base64.b64encode(bytes(image_bytes)).decode()


def _source_badge(result: ResolutionResult | None) -> str:
    """Localised source/confidence badge for the reply, or empty for text inputs.

    Matches the ADR-0001 §6 confidence-tier taxonomy so the user always knows how
    much to trust the numbers (see also: docs/product-philosophy.md).
    """
    if result is None:
        return ""
    if result.source == "saved_rag":
        return "⭐ из вашей базы"
    if result.source == "barcode_off":
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

    Returns the EAN read, matched product name, and the gram-basis used for
    scaling (A6 — CQ2): when the pipeline scaled per-100g values to the
    vision-estimated portion, shows that gram basis so the user can verify
    and correct it at the existing confirm step.  When the portion estimate is
    missing entirely, shows a per-100g warning instead.
    Vision-only results carry no meaningful intermediate signals beyond the
    nutrition fields already shown in the reply.

    These lines let the user catch a wrong barcode read, wrong product match,
    or wrong portion estimate before confirming the meal
    (ADR-0001 §6 + §7, north-star transparency principle).
    """
    if result is None or result.source == "vision":
        return []
    signals = result.signals or {}
    lines: list = []
    barcode_raw = signals.get("barcode_raw")
    if barcode_raw:
        lines.append(f"Штрих-код: {barcode_raw}")
    product_name = signals.get("product_name")
    if product_name:
        lines.append(f"Продукт: {product_name}")
    # Explicit gram-basis transparency (ADR-0001 §7 / CQ2 / A6):
    if result.portion_grams is not None and result.portion_grams > 0:
        # Scaled: show the vision-estimated gram basis so the user can verify
        # it is correct and correct it at the confirm step if needed.
        lines.append(
            f"Пересчитано на {result.portion_grams:.0f}г (оценка по фото)"
            " — скорректируй при подтверждении"
        )
    else:
        # No vision portion estimate — per-100g fallback; warn the user.
        lines.append(
            "⚠️ Порция не определена — данные на 100г, скорректируй при подтверждении"
        )
    return lines


def _nutrition_reply(data, header, resolution_result: ResolutionResult | None = None):
    """Format the confirmation message shared by the photo and text branches.

    ``resolution_result`` is provided for image inputs so the reply can surface
    the source/confidence badge and key intermediate values (EAN, product name,
    gram-basis warning).  Text inputs pass ``None`` — the existing format is
    unchanged.
    """
    badge = _source_badge(resolution_result)
    detail_lines = _resolution_detail_lines(resolution_result)

    parts = [header]
    if badge:
        parts.append(f"Источник: {badge}")
    parts.extend(detail_lines)
    parts.append(f"Продукты: {', '.join(data['foods'])}")
    parts.append(f"Калории: {data['calories']} ккал")
    parts.append(f"Белки: {data['protein']}г")
    parts.append(f"Жиры: {data['fats']}г")
    parts.append(f"Углеводы: {data['carbs']}г")
    parts.append(f"Порция: {data['portion']}")
    parts.append("")
    parts.append("Всё верно? (Да/Нет)")
    return "\n".join(parts)


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
        model=svc.model,
        parse=_parse_nutrition,
    )


async def _run_meal_analysis(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    kind: str,
    input_ref: str,
    payload: str,
    inbound_id: int | None = None,
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
        return await _offer_model_choice(
            update,
            context,
            e,
            kind=kind,
            input_ref=input_ref,
            payload=payload,
            inbound_id=inbound_id,
        )
    except Exception as e:
        logger.error("Error analyzing meal (%s): %s", kind, e, exc_info=True)
        inbound_msg.mark_failed(inbound_id, str(e))
        await update.message.reply_text(
            "Извините, не удалось проанализировать. Пожалуйста, попробуйте ещё раз "
            "или опишите приём пищи иначе."
        )
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
        }
        header = "Я проанализировал фото. Вот что я нашел:"
    else:
        draft = {"nutrition": nutrition_info, "description": payload}
        header = "Я проанализировал ваш приём пищи. Вот что получилось:"

    try:
        await update.message.reply_text(
            _nutrition_reply(nutrition_info, header, resolution_result),
            reply_markup=confirm_keyboard,
        )
    except Exception as e:
        # Analysis is saved; only the confirmation reply failed. Keep the draft
        # atomic (don't commit) and ask the owner to send the meal again.
        logger.error("Error sending meal confirmation (%s): %s", kind, e, exc_info=True)
        await update.message.reply_text(
            "Извините, не удалось проанализировать. Пожалуйста, попробуйте ещё раз "
            "или опишите приём пищи иначе."
        )
        return ADDING_MEAL

    # Reply succeeded: commit the draft and clear any pending retry state.
    context.user_data.setdefault("current_meal", {}).update(draft)
    context.user_data.pop("pending_analysis", None)
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
                "Не удалось загрузить фото. Пожалуйста, опишите приём пищи текстом."
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
) -> int:
    """A model went missing mid-analysis: stash the input and show a picker so the
    owner can switch models and have the analysis retried automatically."""
    context.user_data["pending_analysis"] = {
        "kind": kind,
        "input_ref": input_ref,
        "payload": payload,
        "inbound_id": inbound_id,
    }
    models = await telegram_service.openai_service.list_suitable_models()
    context.user_data["model_choices"] = models
    keyboard = ReplyKeyboardMarkup(
        [[m] for m in models] + [["Отмена"]], resize_keyboard=True
    )
    await update.message.reply_text(
        f"⚠️ Модель «{err.model}» недоступна (устарела или удалена).\n"
        "Выбери другую — я запомню её и сразу повторю анализ:",
        reply_markup=keyboard,
    )
    return CHOOSING_MODEL


def _persist_model(model: str) -> None:
    """Best-effort save of the chosen model. A failure just means it won't
    survive a restart — the live service is already switched, so the flow works."""
    try:
        with SessionLocal() as db:
            crud_app_setting.set(db, OPENAI_MODEL_SETTING_KEY, model)
    except Exception as e:
        logger.warning("Could not persist model choice %s: %s", model, e)


def _load_persisted_model() -> None:
    """Apply a previously-persisted model override at startup. Best-effort: the
    table may not exist yet before the first migration, so never fail startup."""
    try:
        with SessionLocal() as db:
            model = crud_app_setting.get(db, OPENAI_MODEL_SETTING_KEY)
        if model:
            telegram_service.openai_service.set_model(model)
            logger.info("Loaded persisted OpenAI model override: %s", model)
    except Exception as e:
        logger.warning("Could not load persisted model override: %s", e)


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

    if text not in context.user_data.get("model_choices", []):
        await update.message.reply_text("Выбери модель кнопкой из списка.")
        return CHOOSING_MODEL

    telegram_service.openai_service.set_model(text)
    _persist_model(text)
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
    )


@subscription_required
async def confirm_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Final confirmation of the meal.

    Three outcomes (TD-015): an explicit **Да** saves; an explicit **Нет/Отмена**
    discards the draft and restarts; anything else is treated as a typed
    *correction* — re-analyzed as a new text description while keeping the flow in
    CONFIRMING_MEAL. The old behavior silently wiped the analyzed draft on any
    non-"Да" text (a mistyped/lowercase reply, or a real correction).
    """
    intent = _confirm_intent(update.message.text)

    if intent == "correction":
        # A free-text reply to the confirm prompt is a correction, not a reject —
        # re-run the analysis on it and re-ask, instead of discarding the draft.
        # NOTE: for a photo draft this re-analyzes the *text* only; combining the
        # original photo with a text correction needs the photo+text merge that is
        # still a gap (docs/diagrams/input-processing-flow.md, Gap ①) → TD-013.
        text = update.message.text
        # Drop the prior attempt's photo/resolution metadata so the corrected,
        # text-based draft doesn't inherit a stale photo or resolution_source
        # (same hygiene as the reject path). meal_time lives outside current_meal
        # and is intentionally preserved.
        context.user_data["current_meal"] = {}
        inbound_id = inbound_msg.record_inbound(
            telegram_id=update.effective_user.id,
            kind="text",
            content=text,
            photo_file_id=None,
        )
        return await _run_meal_analysis(
            update,
            context,
            kind="text",
            input_ref=text,
            payload=text,
            inbound_id=inbound_id,
        )

    if intent == "affirm":
        try:
            # Get meal data from context
            current_meal = context.user_data.get("current_meal", {})
            nutrition = current_meal.get("nutrition", {})

            # Create meal object
            meal_in = MealCreate(
                description=current_meal.get("description"),
                meal_time=context.user_data.get("meal_time", datetime.now(UTC)),
                calories=nutrition.get("calories"),
                proteins=nutrition.get("protein"),
                fats=nutrition.get("fats"),
                carbohydrates=nutrition.get("carbs"),
                nutrients=nutrition,
                photos=current_meal.get("photos", []),
                ai_analysis=nutrition,
                # Pipeline resolution tracking (A1 columns).
                resolution_source=current_meal.get("resolution_source"),
                resolution_signals=current_meal.get("resolution_signals"),
            )

            # Get or create user and save meal
            with SessionLocal() as db:
                # Get or create user
                user = (
                    db.query(User)
                    .filter(User.telegram_id == update.effective_user.id)
                    .first()
                )
                if not user:
                    user = User(
                        telegram_id=update.effective_user.id,
                        username=update.effective_user.username,
                    )
                    db.add(user)
                    db.commit()
                    db.refresh(user)

                # Save meal
                crud_meal.create(db, meal_in, user.id)

            await update.message.reply_text(
                "Прием пищи сохранен! 👍",
                reply_markup=main_keyboard,
            )
        except Exception as e:
            logger.error(f"Error saving meal: {e}", exc_info=True)
            await update.message.reply_text(
                "Извините, произошла ошибка при сохранении. Пожалуйста, попробуйте еще раз.",
                reply_markup=main_keyboard,
            )
            return CHOOSING_ACTION
    else:
        # Rejecting discards the draft — the retry is a fresh entry and must not
        # inherit stale nutrition/photos from the rejected attempt.
        context.user_data["current_meal"] = {}
        await update.message.reply_text(
            "Давай попробуем ещё раз. Когда был прием пищи?",
            reply_markup=time_keyboard,
        )
        return ADDING_MEAL_TIME

    context.user_data.clear()
    return CHOOSING_ACTION


@subscription_required
async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show user statistics."""
    await update.message.reply_text(
        "Эта функция пока в разработке 🚧",
        reply_markup=main_keyboard,
    )
    return CHOOSING_ACTION


async def handle_subscription_inquiry(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle subscription related questions"""
    text = update.message.text

    if text == "❓ О боте":
        response = (
            "🤖 NutriCore - это умный бот для отслеживания питания и здоровья.\n\n"
            "✨ С подпиской вы получите:\n"
            "• AI-анализ питания\n"
            "• Отслеживание калорий и нутриентов\n"
            "• Интеграцию с Mi Scale\n"
            "• Интеграцию с Samsung Health\n"
            "• Детальную аналитику\n"
            "• Персональные рекомендации"
        )

    elif text == "💎 Получить подписку":
        response = (
            "💫 Подписка открывает доступ ко всем функциям бота!\n\n"
            "Стоимость:\n"
            "• 1 месяц - X руб\n"
            "• 3 месяца - Y руб\n"
            "• 12 месяцев - Z руб\n\n"
            "Для оформления нажмите 'Связаться с админом'"
        )

    elif text == "📱 Связаться с админом":
        admin_username = settings.TELEGRAM_ADMIN_USERNAME
        response = (
            f"По вопросам подписки обращайтесь к администратору: {admin_username}"
        )

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

    except ValueError as ve:
        logger.error(f"Invalid command format: {update.message.text}", exc_info=True)
        await update.message.reply_text(
            "❌ Неверный формат команды. Используйте: /grant_sub user_id months"
        )
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
        await update.message.reply_text("Слишком часто — подожди пару секунд.")
        return

    try:
        # ValueError covers a malformed/non-object body; InvalidURL covers a
        # misconfigured MYHEALTH_CONSULT_URL (not an httpx.HTTPError subclass).
        data = await consult_service.ask(question)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
        logger.error(f"Consult relay failed: {e}", exc_info=True)
        await update.message.reply_text("Не удалось получить ответ. Попробуй позже.")
        return

    # Record the cooldown only after a successful call, so a failed attempt
    # doesn't block an immediate retry.
    _last_consult[user_id] = now

    # Crisis path FIRST — deterministic, from the hub, before the model answer.
    # Never hardcode psyche logic or a crisis number here; surface crisis_hint verbatim.
    if data.get("crisis_hint"):
        await update.message.reply_text(f"⚠️ {data['crisis_hint']}")
    # str() guards against a non-string answer (e.g. a number) from the hub.
    answer = str(data.get("answer") or "Пустой ответ.")
    await update.message.reply_text(
        answer + "\n\n(описательно, не медицинская консультация)"
    )


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


def create_bot_application() -> Application:
    """Create and configure the bot application."""
    # httpx/httpcore log the full request URL at INFO, and the Telegram Bot API
    # embeds the token in the path (.../bot<TOKEN>/getUpdates). Clamp them to
    # WARNING so the live token never lands in the logs (still surfaces failures).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Restore a model the owner picked after a past deprecation (TD-005).
    _load_persisted_model()

    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Global access gate — runs before every other handler (group=-1) and drops
    # updates from users not allowed by the current BOT_ACCESS_MODE.
    application.add_handler(TypeHandler(Update, access_gate), group=-1)

    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ACTION: [
                MessageHandler(filters.Regex("^🍽 Добавить прием пищи$"), add_meal),
                MessageHandler(filters.Regex("^📊 Статистика$"), show_statistics),
            ],
            ADDING_MEAL_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_meal_time),
            ],
            ADDING_MEAL: [
                MessageHandler(
                    (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
                    process_meal_input,
                ),
            ],
            CONFIRMING_MEAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_meal),
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

    # Add subscription management commands
    application.add_handler(CommandHandler("grant_sub", grant_subscription))

    # Consult relay → my-health hub
    application.add_handler(CommandHandler("consult", consult))

    # Replay failed/pending inbound messages after a model fix (owner only).
    application.add_handler(CommandHandler("reprocess", reprocess))

    return application
