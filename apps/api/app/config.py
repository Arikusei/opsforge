from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def default_knowledge_dir() -> str:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "knowledge"
        if candidate.exists():
            return str(candidate)
    return str(Path.cwd() / "knowledge")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "demo"
    app_secret: str = "change-me-in-production"
    database_url: str = "sqlite:///./data/opsforge.db"
    knowledge_dir: str = default_knowledge_dir()

    llm_provider: str = "auto"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1200

    agent_native_tools: bool = True
    agent_max_iterations: int = 6

    trace_persist: bool = True
    trace_webhook_url: str = ""
    # "model:in_usd_per_1m,out_usd_per_1m;model2:..." — переопределяет встроенный прайс
    llm_price_overrides: str = ""

    crm_base_url: str = ""
    crm_access_token: str = ""
    teamchat_api_token: str = ""
    teamchat_bot_token: str = ""
    phonepbx_api_key: str = ""

    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_access_key: str = "opsforge"
    s3_secret_key: str = "opsforge-secret"
    s3_bucket: str = "opsforge-knowledge"

    webhook_secret: str = "opsforge-webhook-dev"
    n8n_host: str = "localhost"
    n8n_port: int = 5678


@lru_cache
def get_settings() -> Settings:
    return Settings()
