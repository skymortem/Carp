from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # База данных
    database_url: str = "sqlite+aiosqlite:///./carp.db"

    # JWT для веб-аутентификации
    secret_key: str = "change-me-to-something-secret-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 часа

    # StarLine API credentials (твои appId и secret)
    starline_app_id: Optional[str] = None
    starline_app_secret: Optional[str] = None

    # Логин/пароль от StarLine Online (твои личные)
    starline_login: Optional[str] = None
    starline_password: Optional[str] = None

    # ID устройства StarLine (узнаётся автоматически)
    starline_device_id: Optional[str] = None

    # Cron: интервал сбора данных (в минутах)
    collect_interval_minutes: int = 60

    # Telegram-бот: вопрос о цене литра после заправки.
    # Токен выдаёт @BotFather, chat_id владельца бот узнаёт из первого сообщения.
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # Минимальный прирост топлива, который считается заправкой (литры).
    # Ниже порога — шум датчика уровня, а не заправка.
    fuel_fill_min_litres: float = 5.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

def persist_settings(values: dict[str, str]):
    """Атомарно обновить локальный .env; файл доступен только владельцу."""
    import os
    import tempfile
    from pathlib import Path
    from dotenv import set_key

    path = Path(settings.model_config.get('env_file') or '.env').resolve()
    fd, temporary = tempfile.mkstemp(prefix='.carp-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            if path.exists():
                output.write(path.read_text(encoding='utf-8'))
        for name, value in values.items():
            set_key(temporary, name.upper(), value)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        for name, value in values.items():
            setattr(settings, name.lower(), value)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if not settings.secret_key or settings.secret_key == 'change-me-to-something-secret-in-production':
    import secrets
    persist_settings({'secret_key': secrets.token_hex(32)})
