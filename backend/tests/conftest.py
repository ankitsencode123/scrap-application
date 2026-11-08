"""Test DB + TestClient. Uses a local throw-away Postgres via `pgserver` for fast, isolated tests."""

import os
import tempfile
from pathlib import Path
from dotenv import load_dotenv

# Preload .env so test overrides stick
_base_dir = Path(__file__).resolve().parents[1]
if (_base_dir / ".env").exists():
    load_dotenv(_base_dir / ".env")

_tmp = tempfile.mkdtemp(prefix="scrapmart-test-")
import pgserver  # noqa: E402

_pg = pgserver.get_server(os.path.join(_tmp, "pg"), cleanup_mode="stop")
os.environ["DATABASE_URL"] = _pg.get_uri()
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["SHARD_DIRECTORY_SHARDS"] = "0"
os.environ["SHARD_URLS"] = ""
os.environ["SHARD_DB_PASSWORD"] = ""
os.environ["LOCAL_UPLOAD_DIR"] = os.path.join(_tmp, "uploads")
os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long-for-tests"
os.environ["CURSOR_SECRET"] = "test-cursor-secret-at-least-16-bytes"
os.environ["MAP_REDIS_URL"] = ""
os.environ["REDIS_URL"] = ""
os.environ["REDIS_CACHE_URL"] = ""
os.environ["REDIS_FALLBACK_URL"] = ""
os.environ.pop("S3_BUCKET", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

API = "/api/v1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
