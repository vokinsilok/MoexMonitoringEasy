from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str = ""
    BACKEND_API_BASE_URL: str = "http://backend:8000"
    BACKEND_API_TIMEOUT_SECONDS: int = 20
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    TBANK_MONITOR_EVENTS_QUEUE_KEY: str = "tbank:monitor:events"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
