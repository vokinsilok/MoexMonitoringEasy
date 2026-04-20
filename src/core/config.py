from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASS: str
    DB_NAME: str

    REDIS_HOST: str
    REDIS_PORT: int

    TBANK_INVEST_TOKEN: str = ""
    TBANK_ACCOUNT_ID: str = ""
    TBANK_INVEST_BASE_URL: str = "https://invest-public-api.tbank.ru"
    TBANK_INVEST_TIMEOUT_SECONDS: int = 10
    TBANK_INVEST_SSL_VERIFY: bool = True
    TBANK_INVEST_CA_BUNDLE_PATH: str | None = None

    TASKIQ_QUEUE_NAME: str = "moex_tasks"
    TBANK_SYNC_INTERVAL_SECONDS: int = 120
    TBANK_MONITOR_CHECK_INTERVAL_SECONDS: int = 30
    TBANK_MONITOR_EVENTS_QUEUE_KEY: str = "tbank:monitor:events"
    TBANK_CLOSED_STATUS_REFRESH_SECONDS: int = 1800

    @field_validator("TBANK_SYNC_INTERVAL_SECONDS", mode="before")
    @classmethod
    def validate_tbank_sync_interval(cls, value):
        if value in (None, ""):
            return 120
        return value

    @field_validator("TBANK_MONITOR_CHECK_INTERVAL_SECONDS", mode="before")
    @classmethod
    def validate_tbank_monitor_check_interval(cls, value):
        if value in (None, ""):
            return 30
        return value

    @field_validator("TBANK_CLOSED_STATUS_REFRESH_SECONDS", mode="before")
    @classmethod
    def validate_tbank_closed_status_refresh(cls, value):
        if value in (None, ""):
            return 1800
        return value

    @property
    def redis_url(self):
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"

    @property
    def db_url(self):
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
