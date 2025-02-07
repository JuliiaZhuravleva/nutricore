from datetime import datetime, UTC
import logging
import json
from typing import Dict, Optional
from sqlalchemy.orm import Session
from app.crud.crud_meal import crud_meal
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
) = range(5)

# Keyboard layouts
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу тебе отслеживать питание и достигать твоих целей. "
        "Выбери действие:",
        reply_markup=main_keyboard,
    )
    return CHOOSING_ACTION

async def add_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the meal addition process."""
    await update.message.reply_text(
        "Когда был прием пищи?",
        reply_markup=time_keyboard,
    )
    return ADDING_MEAL_TIME

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
                user = db.query(User).filter(User.telegram_id == str(update.effective_user.id)).first()
                if not user:
                    user = User(
                        telegram_id=str(update.effective_user.id),
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

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show user statistics."""
    await update.message.reply_text(
        "Эта функция пока в разработке 🚧",
        reply_markup=main_keyboard,
    )
    return CHOOSING_ACTION

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
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🍽 Добавить прием пищи$"), add_meal),
            MessageHandler(filters.Regex("^📊 Статистика$"), show_statistics),
        ],
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
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    return application