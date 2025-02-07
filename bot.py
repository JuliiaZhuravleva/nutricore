import logging
import sys
from app.services.telegram import create_bot_application

# Set up root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Create console handler with formatting
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)

# Remove any existing handlers and add our console handler
root_logger.handlers.clear()
root_logger.addHandler(console_handler)

# Get logger for this module
logger = logging.getLogger(__name__)

def main() -> None:
    """Start the bot."""
    try:
        logger.info("Starting bot application...")
        # Create the Application and pass it your bot's token.
        app = create_bot_application()
        logger.info("Starting polling...")
        # Run the bot until the user presses Ctrl-C
        app.run_polling(allowed_updates=["message", "callback_query"])
    except Exception as e:
        logger.error(f"Error starting bot: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
