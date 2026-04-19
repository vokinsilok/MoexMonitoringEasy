from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str = ""
    BACKEND_API_BASE_URL: str = "http://backend:8000"
    BACKEND_API_TIMEOUT_SECONDS: int = 20
    MONITOR_POLL_INTERVAL_SECONDS: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
