from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application, MessageHandler, filters

from bot.config import load_settings
from bot.handlers import handle_message, handle_unsupported
from bot.memory import MemoryStore
from bot.openai_client import DianaClient
from bot.proactive import proactive_loop


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def post_init(application: Application) -> None:
    memory = application.bot_data["memory"]
    await memory.initialize()
    asyncio.create_task(proactive_loop(application))


async def post_shutdown(application: Application) -> None:
    memory = application.bot_data["memory"]
    await memory.close()


def build_application() -> Application:
    settings = load_settings()

    memory = MemoryStore(settings.database_path)
    diana = DianaClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        persona_path=settings.persona_path,
    )

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data["memory"] = memory
    application.bot_data["diana"] = diana
    application.bot_data["max_history_messages"] = settings.max_history_messages
    application.bot_data["allowed_user_ids"] = settings.allowed_user_ids
    application.bot_data["daily_message_limit"] = settings.daily_message_limit

    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(~(filters.TEXT | filters.PHOTO), handle_unsupported))

    return application


def main() -> None:
    application = build_application()
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
