from __future__ import annotations

import io
import logging
from pathlib import Path

from openai import AsyncOpenAI

from bot.memory import ChatMessage


logger = logging.getLogger(__name__)

# Telegram's supported message reaction emojis (as of 2024).
TELEGRAM_REACTION_EMOJIS = [
    "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔",
    "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩",
    "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳",
    "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡", "🍌", "🏆",
    "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈",
    "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈",
    "😇", "😨", "🤝", "✍", "🤗", "🫡", "🎅", "🎄",
    "☃", "💅", "🤪", "🗿", "🆒", "💘", "🙉", "🦄",
    "😘", "💊", "🙊", "😎", "👾", "🤷", "😡",
]

_REACTION_PROMPT = """
Pick one emoji reaction to this Telegram message that a chill, casual girl would send.
The reaction should feel natural and match the vibe of the message.

Message: {message}

Rules:
- Reply with ONLY the emoji. Nothing else. No words, no punctuation.
- Pick from this list only: {emoji_list}
- If the message is neutral or boring, reply: NONE
""".strip()

_FACT_EXTRACTION_PROMPT = """
You are a fact extractor. Read the latest user message and the conversation context below.
Extract any new personal facts about the USER (not diana) worth remembering long-term.
Things like: their name, age, job, city, relationship status, hobbies, preferences, important life events.

Rules:
- Return one fact per line. Plain text. No bullets, no numbers.
- Only include facts clearly stated or strongly implied by the user.
- Do NOT extract facts already listed in the existing facts.
- If there are no new facts worth remembering, return exactly: NONE

Existing known facts:
{existing_facts}

Conversation:
{conversation}

Latest user message: {user_message}
""".strip()


class DianaClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        persona_path: Path,
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.persona_path = persona_path

    async def reply(
        self,
        user_message: str,
        history: list[ChatMessage],
        user_facts: list[str],
    ) -> list[str]:
        messages = self._build_messages(
            user_message=user_message,
            history=history,
            user_facts=user_facts,
        )
        request_kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.9,
            "frequency_penalty": 0.6,
            "presence_penalty": 0.4,
        }
        request_kwargs.update(self._token_limit_kwargs(self.model, 280))

        response = await self.client.chat.completions.create(**request_kwargs)

        content = response.choices[0].message.content
        if not content:
            return ["idk"]

        return self._split_reply(content)

    async def generate_voice(self, text: str) -> io.BytesIO | None:
        """Generate a voice note from text using OpenAI TTS. Returns OGG/Opus bytes."""
        try:
            async with self.client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="nova",
                input=text,
                response_format="opus",
            ) as response:
                raw = await response.read()

            if not raw:
                logger.warning("TTS returned empty audio")
                return None

            audio_bytes = io.BytesIO(raw)
            audio_bytes.name = "voice.ogg"
            return audio_bytes
        except Exception:
            logger.exception("TTS generation failed — falling back to text")
            return None

    async def pick_reaction(self, user_message: str) -> str | None:
        prompt = _REACTION_PROMPT.format(
            message=user_message,
            emoji_list=" ".join(TELEGRAM_REACTION_EMOJIS),
        )
        request_kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }
        request_kwargs.update(self._token_limit_kwargs(self.model, 10))

        try:
            response = await self.client.chat.completions.create(**request_kwargs)
            raw = (response.choices[0].message.content or "").strip()
            if raw.upper() == "NONE" or not raw:
                return None
            # Take only the first "word" in case the model adds extras.
            candidate = raw.split()[0]
            if candidate in TELEGRAM_REACTION_EMOJIS:
                return candidate
            return None
        except Exception:
            logger.exception("Reaction pick failed — skipping")
            return None

    async def extract_facts(
        self,
        user_message: str,
        history: list[ChatMessage],
        existing_facts: list[str],
    ) -> list[str]:
        conversation_text = "\n".join(
            f"{m.role}: {m.content}" for m in history[-6:]
        )
        existing_text = "\n".join(existing_facts) if existing_facts else "none"

        prompt = _FACT_EXTRACTION_PROMPT.format(
            existing_facts=existing_text,
            conversation=conversation_text,
            user_message=user_message,
        )

        request_kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        request_kwargs.update(self._token_limit_kwargs(self.model, 150))

        try:
            response = await self.client.chat.completions.create(**request_kwargs)
            raw = (response.choices[0].message.content or "").strip()
            if raw.upper() == "NONE" or not raw:
                return []
            return [line.strip() for line in raw.splitlines() if line.strip()]
        except Exception:
            logger.exception("Fact extraction failed — skipping")
            return []

    def _build_messages(
        self,
        user_message: str,
        history: list[ChatMessage],
        user_facts: list[str],
    ) -> list[dict[str, str]]:
        persona_template = self.persona_path.read_text(encoding="utf-8").strip()

        if user_facts:
            facts_text = "\n".join(f"- {f}" for f in user_facts)
        else:
            facts_text = "nothing known yet about this user."

        persona = persona_template.replace("{user_facts}", facts_text)

        messages = [{"role": "system", "content": persona}]
        for item in history:
            messages.append({"role": item.role, "content": item.content})
        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _token_limit_kwargs(model: str, limit: int) -> dict[str, int]:
        model_name = model.lower()
        uses_completion_tokens = (
            model_name.startswith("gpt-5")
            or model_name.startswith("o1")
            or model_name.startswith("o3")
            or model_name.startswith("o4")
        )
        if uses_completion_tokens:
            return {"max_completion_tokens": limit}
        return {"max_tokens": limit}

    @staticmethod
    def _split_reply(reply: str) -> list[str]:
        cleaned = reply.strip()
        cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
        parts = [p.strip().lower() for p in cleaned.split("|||") if p.strip()]
        return parts if parts else ["idk"]
