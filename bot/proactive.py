from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import cast

from telegram.ext import Application

from bot.memory import MemoryStore
from bot.openai_client import DianaClient


logger = logging.getLogger(__name__)

_SILENCE_THRESHOLD_HOURS = 24
_CHECK_INTERVAL_SECONDS = 3600  # check every hour

_CONVERSATION_STARTERS = [
    "yo",
    "hey",
    "u good?",
    "what's up",
    "been a while",
    "still alive?",
    "thinking about something",
    "bored",
    "hey stranger",
]


async def proactive_loop(application: Application) -> None:
    await asyncio.sleep(60)  # small startup delay before first check

    while True:
        try:
            await _check_and_ping(application)
        except Exception:
            logger.exception("Proactive loop error")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


async def _check_and_ping(application: Application) -> None:
    memory = cast(MemoryStore, application.bot_data["memory"])
    diana = cast(DianaClient, application.bot_data["diana"])

    user_ids = await memory.get_all_user_ids()
    threshold = datetime.now(timezone.utc) - timedelta(hours=_SILENCE_THRESHOLD_HOURS)

    for user_id in user_ids:
        last_msg_time = await memory.get_last_user_message_time(user_id)
        if last_msg_time is None:
            continue

        last_dt = datetime.fromisoformat(last_msg_time)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)

        if last_dt < threshold:
            starter = random.choice(_CONVERSATION_STARTERS)
            try:
                await application.bot.send_message(chat_id=user_id, text=starter)
                await memory.add_message(user_id, "assistant", starter)
                logger.info("Sent proactive message to user %s: %s", user_id, starter)
                # Only ping once per cycle — avoid spamming multiple users in the same second
                await asyncio.sleep(random.uniform(1.0, 3.0))
            except Exception:
                logger.warning("Could not send proactive message to user %s", user_id)
