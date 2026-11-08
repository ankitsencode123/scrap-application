"""Section 1: settings (environment-driven configuration)."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Ensure environment variables from backend/.env and root .env are loaded
_base_dir = Path(__file__).resolve().parents[2]
if (_base_dir / ".env").exists():
    load_dotenv(_base_dir / ".env")
if (_base_dir.parent / ".env").exists():
    load_dotenv(_base_dir.parent / ".env")
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scrapmart")


# =============================================================================
# 1. SETTINGS
# =============================================================================
def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def normalize_redis_url(url: str | None) -> str:
    """Normalize Redis connection strings, ensuring TLS for Upstash/SSL endpoints."""
    if not url:
        return ""
    url = url.strip().strip("'\"")
    if "-u " in url:
        url = url.split("-u ")[-1].strip().split()[0]
    if url.startswith("redis://") and ("upstash.io" in url or "ssl=true" in url.lower() or "tls" in url.lower()):
        url = "rediss://" + url[len("redis://"):]
    return url


class Settings:
    def __init__(self) -> None:
        self.ENV = os.getenv("ENV", "development")
        self.API_PREFIX = os.getenv("API_PREFIX", "/api/v1")

        db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/scrapmart")
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        self.DATABASE_URL = db_url

        chat_db = os.getenv("CHAT_DATABASE_URL", "postgresql://postgres.khdbvhcdvtqmreatcssb:74397643438420717330@aws-0-us-east-1.pooler.supabase.com:5432/postgres")
        if chat_db.startswith("postgres://"):
            chat_db = chat_db.replace("postgres://", "postgresql+psycopg2://", 1)
        elif chat_db.startswith("postgresql://") and not chat_db.startswith("postgresql+psycopg2://"):
            chat_db = chat_db.replace("postgresql://", "postgresql+psycopg2://", 1)
        self.CHAT_DATABASE_URL = chat_db

        self.JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
        self.JWT_ALGORITHM = "HS256"
        self.ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
        self.REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "30"))
        self.CURSOR_SECRET = os.getenv("CURSOR_SECRET", self.JWT_SECRET)
        if self.ENV == "production" and self.JWT_SECRET == "dev-secret-change-me":
            raise RuntimeError("JWT_SECRET must be set in production")

        self.CORS_ORIGINS = [
            o.strip()
            for o in os.getenv(
                "CORS_ORIGINS",
                "http://localhost:3000,http://localhost:5173,http://localhost:8080,http://localhost:8081",
            ).split(",")
            if o.strip()
        ]
        self.GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
        self.AUTO_CREATE_TABLES = _env_bool("AUTO_CREATE_TABLES", True)

        self.MIN_LISTING_IMAGES = int(os.getenv("MIN_LISTING_IMAGES", "1"))
        self.MAX_LISTING_IMAGES = int(os.getenv("MAX_LISTING_IMAGES", "6"))
        self.MAX_IMAGE_BYTES = int(float(os.getenv("MAX_IMAGE_MB", "8")) * 1024 * 1024)
        self.MASK_ADDRESS_FOR_PUBLIC = _env_bool("MASK_ADDRESS_FOR_PUBLIC", True)

        self.S3_BUCKET = os.getenv("S3_BUCKET", "")
        self.S3_REGION = os.getenv("S3_REGION", "ap-south-1")
        self.S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None
        self.S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID") or None
        self.S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY") or None
        self.S3_PUBLIC_BASE_URL = os.getenv("S3_PUBLIC_BASE_URL", "").rstrip("/")

        self.SUPABASE_URL = os.getenv("SUPABASE_URL", "https://khdbvhcdvtqmreatcssb.supabase.co").rstrip("/")
        self.SUPABASE_KEY = os.getenv("SUPABASE_KEY", os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
        self.SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "scrapmart-media")

        render_url = os.getenv("RENDER_EXTERNAL_URL", "")
        configured_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if configured_base and "localhost" not in configured_base:
            self.PUBLIC_BASE_URL = configured_base
        elif render_url:
            self.PUBLIC_BASE_URL = render_url.rstrip("/")
        elif self.ENV == "production":
            self.PUBLIC_BASE_URL = "https://scrap-application.onrender.com"
        else:
            self.PUBLIC_BASE_URL = configured_base or "http://localhost:8000"
        self.LOCAL_UPLOAD_DIR = os.getenv("LOCAL_UPLOAD_DIR", "./uploads")

        # Redis configurations
        self.REDIS_URL = normalize_redis_url(os.getenv("REDIS_URL", ""))
        self.MAP_REDIS_URL = normalize_redis_url(os.getenv("MAP_REDIS_URL") or self.REDIS_URL)
        self.REDIS_CACHE_URL = normalize_redis_url(os.getenv("REDIS_CACHE_URL") or self.REDIS_URL)
        self.REDIS_FALLBACK_URL = normalize_redis_url(os.getenv("REDIS_FALLBACK_URL", ""))


settings = Settings()

EARTH_RADIUS_KM = 6371.0088

