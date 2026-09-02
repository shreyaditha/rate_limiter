from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT Authentication
    jwt_secret: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Redis configuration
    redis_url: str = "redis://localhost:6379/0"

    # Rate limiting
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 60
    # "closed" rejects traffic (503) when Redis is unavailable; "open" allows it through.
    rate_limit_fail_mode: str = "closed"

    # Upstream service endpoints
    # To add your own upstream services, add new URL settings here and wire them in router.py
    example_upstream: str = "http://localhost:8001"

    log_level: str = "INFO"

    @property
    def rate_limit_window_ms(self) -> int:
        return self.rate_limit_window_seconds * 1000

    @property
    def fail_closed(self) -> bool:
        return self.rate_limit_fail_mode.strip().lower() != "open"


@lru_cache
def get_settings() -> Settings:
    return Settings()
