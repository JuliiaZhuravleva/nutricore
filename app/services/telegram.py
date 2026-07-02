from datetime import datetime, UTC
import logging
import json
from typing import Dict, Optional
import httpx
from sqlalchemy.orm import Session
from app.crud.crud_meal import crud_meal
from app.crud.crud_subscription import crud_subscription
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.meal import MealCreate

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

from app.core.config import settings
from app.services.openai_service import OpenAIService

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

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
        file_url = file.file_path
        
        try:
            # Analyze the food image using OpenAI
            nutrition_info = await telegram_service.openai_service.analyze_food_image(file_url)
            context.user_data['current_meal']['nutrition'] = nutrition_info
            
            await update.message.reply_text(
                f"Я проанализировал фото. Вот что я нашел:\n"
                f"Продукты: {', '.join(nutrition_info['foods'])}\n"
                f"Калории: {nutrition_info['calories']} ккал\n"
                f"Белки: {nutrition_info['protein']}г\n"
                f"Жиры: {nutrition_info['fats']}г\n"
                f"Углеводы: {nutrition_info['carbs']}г\n"
                f"Примерная порция: {nutrition_info['portion']}\n\n"
                f"Всё верно? (Да/Нет)"
            )
        except Exception as e:
            logger.error(f"Error analyzing food image: {e}")
            await update.message.reply_text(
                "Извините, не удалось проанализировать фото. Пожалуйста, опишите приём пищи текстом."
            )
            return ADDING_MEAL
            
    else:
        meal_text = update.message.text
        try:
            logger.info(f"Processing meal text: {meal_text}")
            # Analyze the food text using OpenAI
            nutrition_info = await telegram_service.openai_service.analyze_food_entry(meal_text)
            logger.info(f"Got nutrition info from OpenAI: {nutrition_info}")
            logger.info(f"Type of nutrition_info: {type(nutrition_info)}")
            
            # Если nutrition_info пришло как строка, пробуем распарсить JSON
            if isinstance(nutrition_info, str):
                logger.info("nutrition_info is a string, parsing as JSON")
                nutrition_info = json.loads(nutrition_info)
            
            context.user_data['current_meal']['nutrition'] = nutrition_info
            context.user_data['current_meal']['description'] = meal_text
            
            try:
                foods_list = nutrition_info.get('foods', [])
                if isinstance(foods_list, str):
                    foods_list = [foods_list]
                
                await update.message.reply_text(
                    f"Я проанализировал ваш приём пищи. Вот что получилось:\n"
                    f"Продукты: {', '.join(foods_list)}\n"
                    f"Калории: {nutrition_info['calories']} ккал\n"
                    f"Белки: {nutrition_info['protein']}г\n"
                    f"Жиры: {nutrition_info['fats']}г\n"
                    f"Углеводы: {nutrition_info['carbs']}г\n"
                    f"Порция: {nutrition_info['portion']}\n\n"
                    f"Всё верно? (Да/Нет)"
                )
            except Exception as format_error:
                logger.error(f"Error formatting response: {str(format_error)}", exc_info=True)
                logger.error(f"Nutrition info that caused error: {nutrition_info}")
                logger.error(f"Type of nutrition_info: {type(nutrition_info)}")
                if isinstance(nutrition_info, dict):
                    logger.error(f"Foods field: {nutrition_info.get('foods')}")
                    logger.error(f"Type of foods field: {type(nutrition_info.get('foods'))}")
                raise
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
            logger.error(f"Error saving meal: {e}")
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

async def grant_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant subscription to user (admin only)"""
    admin_id = update.effective_user.id
    logger.info(f"Grant subscription request from admin {admin_id}")
    
    # Check if user is admin
    if admin_id not in settings.admin_ids:
        logger.warning(f"Unauthorized grant attempt from user {admin_id}")
        await update.message.reply_text("⛔️ У вас нет прав для выполнения этой команды")
        return
    
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

async def consult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Relay a health question to the my-health hub copilot and show its answer.

    Thin relay: no medical logic, no medical storage, and — importantly — no OpenAI
    call on this path (OpenAI stays for food parsing only). The hub owns all medical
    reasoning and the mental-health guardrails.
    """
    question = " ".join(context.args) if context.args else ""
    if not question:
        await update.message.reply_text("Задай вопрос так: /consult <вопрос>")
        return

    if not settings.MYHEALTH_CONSULT_URL or not settings.CONSULT_TOKEN:
        await update.message.reply_text("Консультации сейчас недоступны.")
        return

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                settings.MYHEALTH_CONSULT_URL,
                json={"question": question},
                headers={"X-Consult-Token": settings.CONSULT_TOKEN},
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        logger.error(f"Consult relay failed: {e}", exc_info=True)
        await update.message.reply_text("Не удалось получить ответ. Попробуй позже.")
        return

    # Crisis path FIRST — deterministic, from the hub, before the model answer.
    # Never hardcode psyche logic or a crisis number here; surface crisis_hint verbatim.
    if data.get("crisis_hint"):
        await update.message.reply_text(f"⚠️ {data['crisis_hint']}")
    await update.message.reply_text(
        (data.get("answer") or "Пустой ответ.")
        + "\n\n(описательно, не медицинская консультация)"
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
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

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