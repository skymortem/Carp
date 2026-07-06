from pydantic_settings import BaseSettings
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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()