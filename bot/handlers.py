from __future__ import annotations

import asyncio
import logging
import random
from typing import cast

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.memory import MemoryStore
from bot.openai_client import DianaClient


logger = logging.getLogger(__name__)

# Typing speed: ~55 words per minute is natural for a casual texter.
_WORDS_PER_SECOND = 55 / 60
_MIN_DELAY = 0.4
_MAX_DELAY = 3.5


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None:
        return

    message = update.effective_message
    if message is None or message.text is None:
        return

    user = update.effective_user

    allowed_ids = context.application.bot_data.get("allowed_user_ids")
    if allowed_ids and user.id not in allowed_ids:
        return

    text = message.text.strip()
    if not text:
        return

    memory = cast(MemoryStore, context.application.bot_data["memory"])
    diana = cast(DianaClient, context.application.bot_data["diana"])
    max_history = cast(int, context.application.bot_data["max_history_messages"])

    await memory.upsert_user(
        telegram_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    if text == "/start":
        await _send_with_delay(context, update.effective_chat.id, message, "hey")
        await memory.add_message(user.id, "assistant", "hey")
        return

    # Daily rate limit check.
    daily_limit = context.application.bot_data.get("daily_message_limit", 50)
    if daily_limit > 0:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        count = await memory.count_user_messages_since(user.id, since)
        if count >= daily_limit:
            await _send_with_delay(
                context, update.effective_chat.id, message,
                "hey i need a break. talk to me tomorrow yeah?"
            )
            return

    history = await memory.get_recent_messages(telegram_user_id=user.id, limit=max_history)
    user_facts = await memory.get_user_facts(user.id)

    await memory.add_message(user.id, "user", text)

    # ~30% chance Diana reacts to the message before typing.
    if random.random() < 0.30:
        asyncio.create_task(
            _react_to_message(diana, context, update.effective_chat.id, message, text)
        )

    try:
        parts = await diana.reply(
            user_message=text,
            history=history,
            user_facts=user_facts,
        )
    except Exception:
        logger.exception("OpenAI reply failed for user_id=%s", user.id)
        parts = ["my brain lagged. text me again"]

    full_reply = " ".join(parts)

    # ~20% chance Diana replies with a voice message (only when reply is long enough).
    total_words = len(full_reply.split())
    if total_words >= 4 and random.random() < 0.20:
        await _send_voice_reply(diana, context, update.effective_chat.id, message, full_reply)
    else:
        for i, part in enumerate(parts):
            # 1 in 50 chance: introduce a typo on the first part, then self-correct.
            if i == 0 and random.randint(1, 50) == 1 and len(part.split()) >= 2:
                typo_part = _make_typo(part)
                await _send_with_delay(context, update.effective_chat.id, message, typo_part)
                await asyncio.sleep(random.uniform(1.2, 2.5))
                correction = _correction_for(part)
                await _send_with_delay(context, update.effective_chat.id, message, correction)
            else:
                await _send_with_delay(context, update.effective_chat.id, message, part)

    await memory.add_message(user.id, "assistant", full_reply)

    # Extract and store new facts about the user in the background.
    asyncio.create_task(
        _extract_and_store_facts(diana, memory, user.id, text, history, user_facts)
    )


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_message is None:
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )
    await asyncio.sleep(random.uniform(_MIN_DELAY, _MAX_DELAY))
    await update.effective_message.reply_text("i can only read text rn")


async def _send_with_delay(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message,
    text: str,
) -> None:
    word_count = len(text.split())
    base_delay = word_count / _WORDS_PER_SECOND
    jitter = random.uniform(-0.3, 0.6)
    delay = max(_MIN_DELAY, min(base_delay + jitter, _MAX_DELAY))
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(delay)
    await message.reply_text(text)


async def _react_to_message(
    diana: DianaClient,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message,
    text: str,
) -> None:
    emoji = await diana.pick_reaction(text)
    if not emoji:
        return
    try:
        from telegram import ReactionTypeEmoji
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        logger.debug("Could not set reaction — possibly unsupported in this chat type")


async def _send_voice_reply(
    diana: DianaClient,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message,
    text: str,
) -> None:
    stop_indicator = asyncio.Event()
    indicator_task = asyncio.create_task(
        _keep_record_voice(context, chat_id, stop_indicator)
    )

    try:
        audio = await diana.generate_voice(text)
    finally:
        stop_indicator.set()
        indicator_task.cancel()

    if audio is None:
        await message.reply_text(text)
        return

    try:
        await context.bot.send_voice(chat_id=chat_id, voice=audio)
    except Exception:
        logger.exception("Telegram rejected voice message — falling back to text")
        await message.reply_text(text)


async def _keep_record_voice(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    stop: asyncio.Event,
) -> None:
    """Refresh the 'recording voice' status every 4s until stopped."""
    while not stop.is_set():
        try:
            await context.bot.send_chat_action(
                chat_id=chat_id, action=ChatAction.RECORD_VOICE
            )
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=4.0)
        except asyncio.TimeoutError:
            pass


def _make_typo(text: str) -> str:
    """Swap two adjacent characters in a random word to create a realistic typo."""
    words = text.split()
    # Pick a word long enough to swap chars in.
    candidates = [(i, w) for i, w in enumerate(words) if len(w) >= 4]
    if not candidates:
        return text
    idx, word = random.choice(candidates)
    pos = random.randint(0, len(word) - 2)
    typo_word = word[:pos] + word[pos + 1] + word[pos] + word[pos + 2:]
    words[idx] = typo_word
    return " ".join(words)


def _correction_for(correct_text: str) -> str:
    """Return a casual self-correction message."""
    corrections = ["*" + correct_text, correct_text + " lol typo", correct_text]
    return random.choice(corrections)


async def _extract_and_store_facts(
    diana: DianaClient,
    memory: MemoryStore,
    user_id: int,
    user_message: str,
    history,
    existing_facts: list[str],
) -> None:
    new_facts = await diana.extract_facts(
        user_message=user_message,
        history=history,
        existing_facts=existing_facts,
    )
    for fact in new_facts:
        await memory.add_user_fact(user_id, fact)
        logger.info("Stored fact for user %s: %s", user_id, fact)
