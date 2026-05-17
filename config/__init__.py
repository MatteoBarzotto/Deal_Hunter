"""Configuration loading: YAML settings + .env secrets."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / "config" / ".env"
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"

load_dotenv(_ENV_PATH)


class Secrets(BaseSettings):
    """API keys and URLs loaded from environment / .env file."""

    allegro_client_id: str = ""
    allegro_client_secret: str = ""
    # User-Agent for Allegro requests. MUST match the registered app name exactly.
    # Format: "AppName/Version (+https://url-with-info)"
    # https://developer.allegro.pl/tutorials/informacje-podstawowe-b21569boAI1#user-agent
    allegro_user_agent: str = "DealHunter/0.1 (+https://github.com/deal-hunter)"

    # Azure OpenAI (https://oai.azure.com/ or https://ai.azure.com/)
    # Endpoint looks like: https://<resource>.openai.azure.com/
    # Deployment is the name you gave when deploying the model in Azure.
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-12-01-preview"

    discord_webhook_url: str = ""
    database_url: str = f"sqlite:///{_PROJECT_ROOT / 'deal_hunter.db'}"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=_ENV_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class CategoryConfig(BaseModel):
    name: str
    max_price_pln: float | None = None
    min_price_pln: float | None = None
    id: str | None = None
    url: str | None = None


class PlatformConfig(BaseModel):
    enabled: bool = True
    limit_per_scan: int = 50
    categories: list[CategoryConfig] = Field(default_factory=list)


class Settings(BaseModel):
    scan_interval_minutes: int = 15
    price_drop_threshold_pct: float = 0.30
    min_history_samples: int = 5
    ai_score_threshold: int = 7
    # Default model name (overridden by AZURE_OPENAI_DEPLOYMENT at runtime).
    ai_model: str = "gpt-5-mini"
    ai_max_tokens: int = 1024
    offer_freshness_hours: int = 24
    allegro: PlatformConfig = Field(default_factory=PlatformConfig)
    olx: PlatformConfig = Field(default_factory=PlatformConfig)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings.yaml. Cached for app lifetime."""
    with _SETTINGS_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Settings(**data)


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    return Secrets()


PROJECT_ROOT = _PROJECT_ROOT
