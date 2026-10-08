import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    token: str
    app_id: int | None
    guild_id: int | None
    command_prefix: str
    bot_language: str


def _to_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return int(value)


def load_config() -> BotConfig:
    load_dotenv()

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN is required. Set it in your .env file.")

    app_id = _to_optional_int(os.getenv("DISCORD_APP_ID"))
    guild_id = _to_optional_int(os.getenv("DISCORD_GUILD_ID"))
    command_prefix = os.getenv("DISCORD_COMMAND_PREFIX", "!").strip() or "!"
    bot_language = os.getenv("DISCORD_BOT_LANGUAGE", "en").strip().lower() or "en"
    if bot_language not in {"en", "vi"}:
        raise ValueError("DISCORD_BOT_LANGUAGE must be 'en' or 'vi'.")

    return BotConfig(
        token=token,
        app_id=app_id,
        guild_id=guild_id,
        command_prefix=command_prefix,
        bot_language=bot_language,
    )
