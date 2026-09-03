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
    """Threads and Hash change what a search returns, so they are part of the engine's cache
    identity (services.analysis.cache_name). Only a single-threaded search is reproducible;
    tests pin this to 1 so their expectations are stable."""
    engine_hash_mb: int = 256
    engine_pool_size: int = 2
    """Engine processes that may run at once. A whole-game analysis holds one for its duration."""
    engine_wait_seconds: float = 30.0
    """How long a caller waits for a free engine before the request is refused with 503."""

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"

    chat_claude_command: str = "claude"
    """Command that starts Claude Code for the position chat (shell words). The chat runs the
    CLI headless (`claude -p`) so a Claude subscription login is used, not an API key; tests
    point this at a fake that replays recorded output."""
    chat_model: str = "opus"
    """`--model` for the chat: an alias (opus, sonnet) or a full model id."""
    chat_max_turns: int = 16
    """Tool-call rounds one answer may take before Claude Code stops it."""
    chat_timeout_seconds: float = 300.0
    chat_concurrency: int = 2
    """Claude Code processes that may run at once."""
    chat_mcp_url: str = "http://127.0.0.1:8000/mcp/"
    """Where the Claude Code subprocess reaches this server's chess tools (the MCP mount)."""
    chat_workdir: str | None = None
    """Working directory for the Claude Code subprocess; default ~/.cache/chess-tutor/chat.
    Its sessions (the conversation memory) are stored under this path by Claude Code."""
    chat_max_depth: int = 18
    """Deepest engine search a chat tool call may ask for."""

    lichess_token: str | None = None
    chesscom_user_agent: str = "chess-tutor-ai (https://github.com/rixile9999/chess-tutor-ai)"

    maia_enabled: bool = True
    default_rating: int = 1500


@lru_cache
def get_settings() -> Settings:
    return Settings()
