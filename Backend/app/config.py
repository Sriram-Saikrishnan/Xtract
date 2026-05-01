from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Gemini
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-1.5-flash"

    # Database
    DATABASE_URL: str

    # Auth
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    # Batch processing
    BATCH_SIZE: int = 10
    BATCH_DELAY_SECONDS: int = 65
    MAX_RETRIES: int = 3
    RETRY_WAIT_SECONDS: int = 90
    CONFIDENCE_THRESHOLD: float = 0.75

    # File handling
    UPLOAD_DIR: str = "/tmp/billscan/uploads"
    OUTPUT_DIR: str = "/tmp/billscan/outputs"
    MAX_FILE_SIZE_MB: int = 10
    AUTO_DELETE_HOURS: int = 2
    ALLOWED_EXTENSIONS: List[str] = ["jpg", "jpeg", "png", "pdf", "heic"]

    # Server — set ALLOWED_ORIGINS in .env to include your Vercel frontend URL
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024


settings = Settings()
