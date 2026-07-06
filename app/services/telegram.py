from datetime import datetime, UTC
import base64
import logging
import json
import time
from typing import Dict, Optional
import httpx
from sqlalchemy.orm import Session
from app.crud.crud_meal import crud_meal
from app.crud.crud_ai_call_log import crud_ai_call_log
from app.crud.crud_subscription import crud_subscription
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.meal import MealCreate
from app.schemas.ai_call_log import AiCallLogCreate

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    TypeHandler,
    filters,
)

from app.core.config import settings
from app.services.access_control import access_gate, admin_required
from app.services.consult_service import consult_service
from app.services.openai_service import OpenAIService

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
) = range(6)

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
            text = ("🔒 Эта функция доступна только для пользователей с подпиской.\n\n"
                   "Базовая версия бота позволяет:\n"
                   "• Узнать о возможностях бота\n"
                   "• Связаться с администратором\n"
                   "• Получить подписку\n\n"
                   "Нажмите кнопку 'Получить подписку' чтобы узнать больше!")
            await update.message.reply_text(text, reply_markup=subscription_keyboard)
            return SUBSCRIPTION_INQUIRY
        return await func(update, context)
    return wrapper

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    if not await check_subscription(update.effective_user.id):
        text = ("👋 Привет! Я NutriCore бот - ваш персональный помощник в отслеживании питания и здоровья!\n\n"
               "🤖 Что я умею:\n"
               "• Анализировать приемы пищи с помощью AI\n"
               "• Отслеживать калории и нутриенты\n"
               "• Интегрироваться с Mi Scale и Samsung Health\n"
               "• Предоставлять детальную аналитику\n\n"
               "💎 Для доступа к этим функциям нужна подписка.")
        await update.message.reply_text(text, reply_markup=subscription_keyboard)
        return SUBSCRIPTION_INQUIRY
    else:
        text = ("✅ У вас активная подписка! Выберите действие:")
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
        context.user_data["meal_time"] = datetime.now(UTC)  # For now, default to current time
        context.user_data["meal_type"] = text

    await update.message.reply_text(
        "Отлично! Теперь опиши, что ты ел(а), или отправь фото.\n\n"
        "Можешь также отправить и то, и другое.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )
    return ADDING_MEAL

def _parse_nutrition(raw):
    """OpenAI analysis result → dict with a guaranteed list `foods`.

    Both analysis methods return the raw JSON string; the model may also give
    `foods` as a bare string. This normalizes both so callers get a dict.
    """
    data = json.loads(raw) if isinstance(raw, str) else raw
    foods = data.get('foods', [])
    data['foods'] = [foods] if isinstance(foods, str) else (foods or [])
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


def _record_ai_call(**fields):
    """Best-effort debug row for one OpenAI analysis call.

    Never raises — a logging failure must not break the meal flow.
    """
    try:
        with SessionLocal() as db:
            crud_ai_call_log.create(db, AiCallLogCreate(**fields))
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to record ai_call_log: %s", exc)


async def _analyze_and_log(coro, *, kind, input_ref, telegram_id):
    """Await an OpenAI analysis coroutine, persist a debug row, return parsed dict.

    Records `status="ok"` with the raw + parsed result on success, or
    `status="error"` (and re-raises) on any analysis / parse failure.
    """
    model = telegram_service.openai_service.model
    started = time.perf_counter()
    raw = None
    try:
        raw = await coro
        parsed = _parse_nutrition(raw)
    except Exception as exc:
        _record_ai_call(
            telegram_id=telegram_id, kind=kind, input_ref=input_ref, model=model,
            raw_response=raw if isinstance(raw, str) else None, parsed_result=None,
            status="error", error=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
    _record_ai_call(
        telegram_id=telegram_id, kind=kind, input_ref=input_ref, model=model,
        raw_response=raw if isinstance(raw, str) else None, parsed_result=parsed,
        status="ok", error=None,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return parsed


@subscription_required
async def process_meal_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the meal description or photo."""
    user = update.message.from_user
    
    # Initialize current_meal if it doesn't exist
    if 'current_meal' not in context.user_data:
        context.user_data['current_meal'] = {}
    
    if update.message.photo:
        # Get the largest photo (best quality)
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        try:
            # Send the image to OpenAI as a base64 data URL rather than Telegram's
            # file URL — that URL embeds the bot token (api.telegram.org/file/bot<TOKEN>/…)
            # and we don't want to hand it to a third party. It also avoids relying on
            # OpenAI being able to fetch from api.telegram.org.
            image_bytes = await file.download_as_bytearray()
            image_data_url = (
                "data:image/jpeg;base64,"
                + base64.b64encode(bytes(image_bytes)).decode()
            )

            # Analyze the food image using OpenAI (recorded to ai_call_logs)
            nutrition_info = await _analyze_and_log(
                telegram_service.openai_service.analyze_food_image(image_data_url),
                kind="image",
                input_ref=photo.file_id,
                telegram_id=update.effective_user.id,
            )
            draft = {
                'nutrition': nutrition_info,
                'description': ', '.join(nutrition_info['foods']) or "Фото приёма пищи",
                # Telegram file_id so the saved meal keeps a photo reference
                # (re-fetchable; cheaper than storing the bytes in the DB).
                'photos': [photo.file_id],
            }

            await update.message.reply_text(
                _nutrition_reply(
                    nutrition_info, "Я проанализировал фото. Вот что я нашел:"
                )
            )
            # Commit to the draft only after the reply succeeded — a failed
            # analysis or reply leaves no half-written state in user_data.
            context.user_data['current_meal'].update(draft)
        except Exception as e:
            logger.error(f"Error analyzing food image: {e}")
            await update.message.reply_text(
                "Извините, не удалось проанализировать фото. Пожалуйста, опишите приём пищи текстом."
            )
            return ADDING_MEAL
            
    else:
        meal_text = update.message.text
        try:
            logger.debug("Processing meal text: %s", meal_text)
            # Analyze the food text using OpenAI (recorded to ai_call_logs)
            nutrition_info = await _analyze_and_log(
                telegram_service.openai_service.analyze_food_entry(meal_text),
                kind="text",
                input_ref=meal_text,
                telegram_id=update.effective_user.id,
            )
            draft = {'nutrition': nutrition_info, 'description': meal_text}

            await update.message.reply_text(
                _nutrition_reply(
                    nutrition_info, "Я проанализировал ваш приём пищи. Вот что получилось:"
                )
            )
            # Commit only after a successful reply — no partial draft on failure.
            context.user_data['current_meal'].update(draft)
        except Exception as e:
            logger.error(f"Error analyzing food text: {str(e)}", exc_info=True)
            await update.message.reply_text(
                "Извините, произошла ошибка при анализе. Пожалуйста, попробуйте еще раз или опишите иначе."
            )
            return ADDING_MEAL
    
    return CONFIRMING_MEAL

@subscription_required
async def confirm_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Final confirmation of the meal."""
    if update.message.text == "Да":
        try:
            # Get meal data from context
            current_meal = context.user_data.get('current_meal', {})
            nutrition = current_meal.get('nutrition', {})
            
            # Create meal object
            meal_in = MealCreate(
                description=current_meal.get('description'),
                meal_time=context.user_data.get('meal_time', datetime.now(UTC)),
                calories=nutrition.get('calories'),
                proteins=nutrition.get('protein'),
                fats=nutrition.get('fats'),
                carbohydrates=nutrition.get('carbs'),
                nutrients=nutrition,
                photos=current_meal.get('photos', []),
                ai_analysis=nutrition
            )
            
            # Get or create user and save meal
            with SessionLocal() as db:
                # Get or create user
                user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
                if not user:
                    user = User(
                        telegram_id=update.effective_user.id,
                        username=update.effective_user.username
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

async def handle_subscription_inquiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription related questions"""
    text = update.message.text
    
    if text == "❓ О боте":
        response = ("🤖 NutriCore - это умный бот для отслеживания питания и здоровья.\n\n"
                   "✨ С подпиской вы получите:\n"
                   "• AI-анализ питания\n"
                   "• Отслеживание калорий и нутриентов\n"
                   "• Интеграцию с Mi Scale\n"
                   "• Интеграцию с Samsung Health\n"
                   "• Детальную аналитику\n"
                   "• Персональные рекомендации")
    
    elif text == "💎 Получить подписку":
        response = ("💫 Подписка открывает доступ ко всем функциям бота!\n\n"
                   "Стоимость:\n"
                   "• 1 месяц - X руб\n"
                   "• 3 месяца - Y руб\n"
                   "• 12 месяцев - Z руб\n\n"
                   "Для оформления нажмите 'Связаться с админом'")
    
    elif text == "📱 Связаться с админом":
        admin_username = settings.TELEGRAM_ADMIN_USERNAME
        response = f"По вопросам подписки обращайтесь к администратору: {admin_username}"
    
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
        
        logger.info(f"Attempting to grant subscription: user_id={user_id}, months={months}, admin_id={admin_id}")
        
        db = SessionLocal()
        subscription = crud_subscription.create_subscription(
            db, user_id, admin_id, months
        )
        db.close()
        
        if subscription:
            logger.info(f"Successfully granted subscription to user {user_id}")
            await update.message.reply_text(f"✅ Подписка выдана пользователю {user_id} на {months} месяцев")
        else:
            logger.error(f"Failed to create subscription: user_id={user_id}, months={months}, admin_id={admin_id}")
            await update.message.reply_text("❌ Ошибка при выдаче подписки")
            
    except ValueError as ve:
        logger.error(f"Invalid command format: {update.message.text}", exc_info=True)
        await update.message.reply_text("❌ Неверный формат команды. Используйте: /grant_sub user_id months")
    except Exception as e:
        logger.error(f"Unexpected error while granting subscription: {str(e)}", exc_info=True)
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
            SUBSCRIPTION_INQUIRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subscription_inquiry)
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^❓ О боте$"), start),
            MessageHandler(filters.Regex("^💎 Получить подписку$"), handle_subscription_inquiry),
        ],
    )

    application.add_handler(conv_handler)
    
    # Add subscription management commands
    application.add_handler(CommandHandler("grant_sub", grant_subscription))

    # Consult relay → my-health hub
    application.add_handler(CommandHandler("consult", consult))

    return application
