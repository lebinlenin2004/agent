from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-secret-change-me-dev-only-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Vacancy Research Agent"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://vacancy:vacancy@localhost:5432/vacancy"
    secret_key: SecretStr = SecretStr(_DEV_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    allow_registration: bool = True
    cors_origins: str = "http://localhost:5173"
    auth_rate_limit_per_minute: int = 10

    redis_url: str = "redis://localhost:6379/0"
    celery_enabled: bool = False

    search_provider: str = "mock"  # mock | http
    search_api_url: str | None = None
    search_api_key: SecretStr | None = None
    permitted_portals: str = ""
    user_agent: str = "VacancyResearchBot/1.0"
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 2
    per_domain_delay_seconds: float = 1.0
    max_pages_per_company: int = 12
    max_response_bytes: int = 2_000_000
    use_playwright: bool = False
    recheck_interval_hours: int = 72
    allow_private_network_fetch: bool = False  # SSRF guard; only for local testing

    llm_provider: str = "mock"  # mock | anthropic
    llm_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_api_url: str = "https://api.anthropic.com/v1/messages"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def portal_domains(self) -> set[str]:
        return {d.strip().lower() for d in self.permitted_portals.split(",") if d.strip()}

    @model_validator(mode="after")
    def _check_production(self):
        if self.environment == "production":
            key = self.secret_key.get_secret_value()
            if key == _DEV_SECRET or len(key) < 32:
                raise ValueError("SECRET_KEY must be set to a random value of 32+ characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
