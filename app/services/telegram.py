
from datetime import datetime
import logging
from typing import Dict, Optional

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
        context.user_data["meal_time"] = datetime.now()
    else:
        # Here we'll need to add logic to parse different meal times
        context.user_data["meal_time"] = datetime.now()
        context.user_data["meal_type"] = text

    await update.message.reply_text(
        "Отлично! Теперь опиши, что ты ел(а), или отправь фото.\n\n"
        "Можешь также отправить и то, и другое.",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )
    return ADDING_MEAL

async def process_meal_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the meal description or photo."""
    if update.message.text == "Отмена":
        await update.message.reply_text(
            "Добавление приема пищи отменено. Выберите действие:",
            reply_markup=main_keyboard,
        )
        return CHOOSING_ACTION

    # Store the input in context
    if update.message.text:
        context.user_data["meal_description"] = update.message.text
    elif update.message.photo:
        # Get the largest photo size
        photo = update.message.photo[-1]
        context.user_data["meal_photo"] = photo.file_id

    # Here we'll add integration with OpenAI for analysis
    # For now, just confirm the input
    confirmation_text = "Подтверди прием пищи:\n\n"
    if "meal_type" in context.user_data:
        confirmation_text += f"Тип: {context.user_data['meal_type']}\n"
    if "meal_description" in context.user_data:
        confirmation_text += f"Описание: {context.user_data['meal_description']}\n"
    if "meal_photo" in context.user_data:
        confirmation_text += "Фото: ✅\n"

    await update.message.reply_text(
        confirmation_text + "\nВсё верно?",
        reply_markup=ReplyKeyboardMarkup(
            [["Да", "Нет"]], resize_keyboard=True
        ),
    )
    return CONFIRMING_MEAL

async def confirm_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Final confirmation of the meal."""
    if update.message.text == "Да":
        # Here we'll add logic to save the meal to the database
        await update.message.reply_text(
            "Прием пищи сохранен! 👍",
            reply_markup=main_keyboard,
        )
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
                MessageHandler(filters.Regex("^(Да|Нет)$"), confirm_meal),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    return application