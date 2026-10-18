from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The competition salt is supplied by the grader at process start. Never
    # fall back to a value committed to the repository.
    DAY1_SALT: str
    DAY2_SALT: str = ""
    JWT_SECRET: str = ""
    DATABASE_URL: str = ""
    REDIS_URL: str = ""
    MINIO_ENDPOINT: str = ""
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "nascon-receipts"
    MINIO_SECURE: bool = False
    MEDIAMTX_API_ENDPOINT: str = ""
    CORS_ORIGINS: str = "http://localhost,http://127.0.0.1"
    CORS_ORIGIN_REGEX: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    PORT: int = 8080
    HOST: str = "0.0.0.0"

    @field_validator("DAY1_SALT")
    @classmethod
    def require_day1_salt(cls, value: str) -> str:
        if not value:
            raise ValueError("DAY1_SALT must be provided in the environment")
        return value


settings = Settings()
