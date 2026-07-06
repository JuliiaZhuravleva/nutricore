import base64
import json
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
from app.services.access_control import access_gate, admin_required
from app.services.ai_call_log_service import analyze_and_log
from app.services.consult_service import consult_service
from app.services.openai_service import (
    OPENAI_MODEL_SETTING_KEY,
    ModelUnavailableError,
    OpenAIService,
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

# Conversation states
(
    CHOOSING_ACTION,
    ADDING_MEAL,
    ADDING_MEAL_TIME,
    ADDING_MEAL_PHOTO,
    CONFIRMING_MEAL,
    SUBSCRIPTION_INQUIRY,
    CHOOSING_MODEL,
) = range(7)

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


class TelegramService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TelegramService, cls).__new__(cls)
            cls._instance.openai_service = OpenAIService()
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


_REQUIRED_NUTRITION_KEYS = ("calories", "protein", "fats", "carbs", "portion")


def _parse_nutrition(raw):
    """OpenAI analysis result → validated dict with a guaranteed list `foods`.

    Both analysis methods return the raw JSON string; the model may also give
    `foods` as a bare string. Normalizes both, and raises ValueError on a
    None/non-object payload or one missing the fields the reply needs — so a
    malformed response is recorded as an error and surfaced, not turned into a
    downstream KeyError.
    """
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    foods = data.get("foods", [])
    foods = [foods] if isinstance(foods, str) else (foods or [])
    # Coerce elements to str: models the owner swaps in (o-series, etc.) may
    # return foods as dicts/numbers, which would blow up ", ".join(...) later.
    data["foods"] = [str(x) for x in foods]
    missing = [k for k in _REQUIRED_NUTRITION_KEYS if k not in data]
    if missing:
        raise ValueError(f"nutrition response missing keys: {missing}")
    return data


def _nutrition_reply(data, header):
    """Format the confirmation message shared by the photo and text branches."""
    return (
        f"{header}\n"
        f"Продукты: {', '.join(data['foods'])}\n"
        f"Калории: {data['calories']} ккал\n"
        f"Белки: {data['protein']}г\n"
        f"Жиры: {data['fats']}г\n"
        f"Углеводы: {data['carbs']}г\n"
        f"Порция: {data['portion']}\n\n"
        f"Всё верно? (Да/Нет)"
    )


async def _run_meal_analysis(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    kind: str,
    input_ref: str,
    payload: str,
) -> int:
    """Analyze one meal input (image data URL or text), reply, and stage the draft.

    Shared by the first attempt (``process_meal_input``) and the retry after a
    model switch (``process_model_choice``). On a model deprecation it offers the
    model picker (→ CHOOSING_MODEL); on any other failure it asks for the input
    again (→ ADDING_MEAL). The draft is committed only after a successful reply,
    so a failure never leaves half-written state in ``user_data``.
    """
    svc = telegram_service.openai_service
    coro = (
        svc.analyze_food_image(payload)
        if kind == "image"
        else svc.analyze_food_entry(payload)
    )
    try:
        # Analyze via OpenAI (recorded to ai_call_logs), then reply. Both the
        # analysis and the confirmation reply are inside the try so any failure
        # falls back cleanly and no half-written draft is committed below.
        nutrition_info = await analyze_and_log(
            coro,
            kind=kind,
            input_ref=input_ref,
            telegram_id=update.effective_user.id,
            model=svc.model,
            parse=_parse_nutrition,
        )
        if kind == "image":
            draft = {
                "nutrition": nutrition_info,
                "description": ", ".join(nutrition_info["foods"]) or "Фото приёма пищи",
                # Telegram file_id so the saved meal keeps a photo reference
                # (re-fetchable; cheaper than storing the bytes in the DB).
                "photos": [input_ref],
            }
            header = "Я проанализировал фото. Вот что я нашел:"
        else:
            draft = {"nutrition": nutrition_info, "description": payload}
            header = "Я проанализировал ваш приём пищи. Вот что получилось:"
        await update.message.reply_text(_nutrition_reply(nutrition_info, header))
    except ModelUnavailableError as e:
        # Configured model is gone — let the owner pick a new one and retry.
        return await _offer_model_choice(
            update, context, e, kind=kind, input_ref=input_ref, payload=payload
        )
    except Exception as e:
        logger.error("Error analyzing meal (%s): %s", kind, e, exc_info=True)
        await update.message.reply_text(
            "Извините, не удалось проанализировать. Пожалуйста, попробуйте ещё раз "
            "или опишите приём пищи иначе."
        )
        return ADDING_MEAL
    # Commit only after the reply succeeded, and clear any pending retry state.
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
        try:
            # Send the image to OpenAI as a base64 data URL rather than Telegram's
            # file URL — that URL embeds the bot token (api.telegram.org/file/bot<TOKEN>/…)
            # and we don't want to hand it to a third party. It also avoids relying
            # on OpenAI being able to fetch from api.telegram.org.
            file = await context.bot.get_file(photo.file_id)
            image_bytes = await file.download_as_bytearray()
        except Exception as e:
            logger.error("Error fetching photo: %s", e, exc_info=True)
            await update.message.reply_text(
                "Не удалось загрузить фото. Пожалуйста, опишите приём пищи текстом."
            )
            return ADDING_MEAL
        image_data_url = (
            "data:image/jpeg;base64," + base64.b64encode(bytes(image_bytes)).decode()
        )
        return await _run_meal_analysis(
            update,
            context,
            kind="image",
            input_ref=photo.file_id,
            payload=image_data_url,
        )

    meal_text = update.message.text
    logger.debug("Processing meal text: %s", meal_text)
    return await _run_meal_analysis(
        update, context, kind="text", input_ref=meal_text, payload=meal_text
    )


async def _offer_model_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    err: ModelUnavailableError,
    *,
    kind: str,
    input_ref: str,
    payload: str,
) -> int:
    """A model went missing mid-analysis: stash the input and show a picker so the
    owner can switch models and have the analysis retried automatically."""
    context.user_data["pending_analysis"] = {
        "kind": kind,
        "input_ref": input_ref,
        "payload": payload,
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
    )


@subscription_required
async def confirm_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Final confirmation of the meal."""
    if update.message.text == "Да":
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

    return application
