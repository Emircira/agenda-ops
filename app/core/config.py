from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Temel Ayarlar
    PROJECT_NAME: str = "AgendaOps"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "secret"
    DEBUG: bool = True
    TIMEZONE: str = "Europe/Istanbul"
    
    # Database Ayarları
    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str
    
    # Redis
    REDIS_URL: str
    
    # --- İŞTE SORUNU ÇÖZEN KISIM ---
    # .env dosyasında olup burada olmayan her şeyi buraya "Optional" olarak ekliyoruz.
    # Böylece Pydantic "Bu nedir?" diye kızmıyor.
    PYTHONPATH: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    YOUTUBE_API_KEY: Optional[str] = None
    RAPID_API_KEY: Optional[str] = None
    RAPID_API_HOST: Optional[str] = None

    # Config Ayarları
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore" # Ne olur ne olmaz diye bunu da ekledik
    )

settings = Settings()