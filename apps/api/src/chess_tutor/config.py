"""Runtime settings. Everything comes from environment variables or a .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./chess_tutor.db"
    stockfish_path: str | None = None
    engine_depth: int = 16
    engine_multipv: int = 3
    engine_threads: int = 2
    engine_hash_mb: int = 256

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"

    lichess_token: str | None = None
    chesscom_user_agent: str = "chess-tutor-ai (https://github.com/rixile9999/chess-tutor-ai)"

    maia_enabled: bool = True
    default_rating: int = 1500


@lru_cache
def get_settings() -> Settings:
    return Settings()
