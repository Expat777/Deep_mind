from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения, читаются из .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # База данных
    DATABASE_URL: str = "postgresql+psycopg2://datamind:datamind@localhost:5432/datamind"

    # LLM (DeepSeek, OpenAI-совместимый API)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # Selectel Object Storage (S3)
    SELECTEL_ENDPOINT: str = "https://s3.selectel.ru"
    SELECTEL_ACCESS_KEY: str = ""
    SELECTEL_SECRET_KEY: str = ""
    SELECTEL_BUCKET: str = "datamind-files"


settings = Settings()
