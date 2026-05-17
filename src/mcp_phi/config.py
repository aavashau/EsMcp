from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    es_url: str = "http://localhost:9200"
    es_index: str = "healthcare"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    audit_dir: str = "./audit"
    log_level: str = "INFO"


settings = Settings()
