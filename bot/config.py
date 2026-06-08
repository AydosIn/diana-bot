from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    openai_api_key: str
    openai_model: str
    database_path: Path
    max_history_messages: int
    persona_path: Path


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Add it to .env.")

    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to .env.")

    database_path = Path(os.getenv("DATABASE_PATH", "database.db"))
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path

    return Settings(
        telegram_bot_token=telegram_bot_token,
        openai_api_key=openai_api_key,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        database_path=database_path,
        max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "20")),
        persona_path=BASE_DIR / "diana_persona.md",
    )
