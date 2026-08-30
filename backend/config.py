from __future__ import annotations

import json
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # GEMINI_API_KEY accepts a JSON list (recommended) or a comma-separated list.
    # Example: ["key-1", "key-2", "key-3"]
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"
    gemini_models: str = ""
    max_output_tokens: int = 65536
    top_p: float = 0.95
    thinking_level: str = "medium"
    cors_origins: str = "http://localhost:5173"
    db_path: str = "../database/bidlens.db"
    ai_retries: int = 2
    ai_retry_delay_seconds: float = 0.5
    ai_max_workers: int = 8
    ai_key_wait_seconds: float = 60.0
    log_level: str = "INFO"
    bidlens_username: str = "admin"
    bidlens_password: str = ""
    auth_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def api_keys(self) -> list[str]:
        raw = (self.gemini_api_key or "").strip()
        if not raw:
            return []

        values: list[str] = []
        # Preferred format: ["key1", "key2"]
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    values = [str(item).strip().strip('"\'') for item in parsed]
            except json.JSONDecodeError:
                # Fall through to comma parsing for forgiving env-file handling.
                pass

        if not values:
            values = [part.strip().strip('"\'') for part in raw.split(",")]

        # Preserve order, remove blanks and duplicate keys.
        deduped: list[str] = []
        seen: set[str] = set()
        for key in values:
            if key and key not in seen:
                deduped.append(key)
                seen.add(key)
        return deduped

    def model_priority(self) -> list[str]:
        raw = (self.gemini_models or self.gemini_model or "").strip()
        if not raw:
            return []
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    raw_items = [str(x) for x in parsed]
                else:
                    raw_items = []
            except json.JSONDecodeError:
                raw_items = raw[1:-1].split(",")
        else:
            raw_items = raw.split(",")

        models: list[str] = []
        for item in raw_items:
            name = item.strip().strip('"\'')
            if name and name not in models:
                models.append(name)
        return models

    def worker_count(self, task_count: int) -> int:
        configured = max(1, int(self.ai_max_workers))
        return max(1, min(configured, max(1, task_count)))


settings = Settings()
