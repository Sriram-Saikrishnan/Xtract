from pydantic_settings import BaseSettings
from typing import Dict, List, Optional


class Settings(BaseSettings):
    # Gemini
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Database
    DATABASE_URL: str

    # Auth
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    # Supabase Storage
    SUPABASE_URL: str           # https://xxxx.supabase.co  (Project Settings → API → Project URL)
    SUPABASE_SERVICE_KEY: str   # Project Settings → API → service_role

    # Batch processing
    BATCH_SIZE: int = 10
    BATCH_DELAY_SECONDS: int = 65
    MAX_RETRIES: int = 3
    RETRY_WAIT_SECONDS: int = 90
    CONFIDENCE_THRESHOLD: float = 0.75
    GEMINI_TIMEOUT_SECONDS: int = 120
    STARTUP_RECOVERY_STALE_MINUTES: int = 10
    MAX_FILES_PER_UPLOAD: int = 150

    # Gemini quota (Tier 1 billing: 4000 RPM, unlimited RPD — 90% safety buffer applied)
    GEMINI_RPM_CAP: int = 3600
    GEMINI_DAILY_CAP_ENABLED: bool = False
    GEMINI_RPD_CAP: int = 450

    # Concurrency caps (sized for 512MB Render instance + 15-connection Supabase PgBouncer pool)
    MAX_CONCURRENT_EXTRACTIONS: int = 6
    MAX_CONCURRENT_DB_WRITES: int = 6

    # AI observability — USD per 1M tokens, used to estimate llm_calls.cost_usd.
    # Thinking tokens are billed at the output rate. Override via env as JSON.
    GEMINI_PRICING: Dict[str, Dict[str, float]] = {
        "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cached": 0.125},
        "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached": 0.03},
        "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cached": 0.01},
    }

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


# Thinking support per model family, matched by longest name prefix.
#   off:     budget sent when a blueprint has thinking OFF (the model's minimum
#            if it can't fully disable thinking, e.g. 2.5 Pro → 128)
#   default: the budget the model uses when no thinking_config is sent
# None (or a model not listed) = no thinking support → no thinking_config, shown as "N/A".
# "On" is always -1 (dynamic).
GEMINI_THINKING_LIMITS: Dict[str, Optional[Dict[str, int]]] = {
    "gemini-2.5-pro": {"off": 128, "default": -1},
    "gemini-2.5-flash": {"off": 0, "default": -1},
    "gemini-2.5-flash-lite": {"off": 0, "default": 0},
    "gemini-2.0-flash": None,
}
