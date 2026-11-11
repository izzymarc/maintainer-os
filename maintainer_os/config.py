from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MaintainerOS"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://maintainer:maintainer@localhost:5432/maintainer"
    redis_url: str = "redis://localhost:6379/0"
    github_webhook_secret: str = "development-only-change-me"
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MAINTAINEROS_")


settings = Settings()
