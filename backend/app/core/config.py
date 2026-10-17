import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DAY1_SALT: str = os.environ.get("DAY1_SALT", "T15D1b255e2e8e2e")
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "postgresql://nascon:nascon_secure_password@postgres:5432/nascon_league")
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    PORT: int = int(os.environ.get("PORT", "8080"))
    HOST: str = os.environ.get("HOST", "0.0.0.0")

settings = Settings()
