import asyncio
import logging
import sys
from app.services.telegram import create_bot_application

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

def main() -> None:
    """Start the bot."""
    try:
        logger.info("Starting bot application...")
        app = create_bot_application()
        logger.info("Starting polling...")
        app.run_polling(allowed_updates=["message", "callback_query"])
    except Exception as e:
        logger.error(f"Error starting bot: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
