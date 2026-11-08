"""Section 10: application (app, CORS, lifespan, routers).  Run: uvicorn app.main:app --reload"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api
from app.core.config import log, settings
from app.core.cursor_pagination import install_cursor_handlers
from app.core.shard_router import (
    close_router,
    get_router,
    init_router,
    install_fastapi_handlers,
)
from app.core.storage import LocalStorage, storage

# Make sure every model is registered on Base.metadata before create_all().
from app.db import models  # noqa: F401
from app.db.base import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    from sqlalchemy import text

    router = init_router()
    if settings.AUTO_CREATE_TABLES:
        router.create_all(Base.metadata)

    # Ensure added columns and tables exist on existing shards
    for s in router.shards:
        try:
            with s.engine.begin() as conn:
                for col_sql in (
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS pickup_time_slot VARCHAR(80);",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS floor_access VARCHAR(80);",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS is_negotiable BOOLEAN DEFAULT TRUE;",
                    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS packaging_type VARCHAR(80);",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);",
                    """
                    CREATE TABLE IF NOT EXISTS chat_read_states (
                        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                        reader_id UUID NOT NULL,
                        peer_id UUID NOT NULL,
                        last_read_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        PRIMARY KEY (listing_id, reader_id, peer_id)
                    );
                    """,
                ):
                    try:
                        conn.execute(text(col_sql))
                    except Exception:
                        pass
        except Exception as exc:
            log.warning("schema update warning for shard %s: %s", s.id, exc)

    from app.api.v1 import chat_ws

    await chat_ws.startup()

    log.info("ScrapMart API started (env=%s, shards=%s)", settings.ENV, router.shard_ids)
    yield
    await chat_ws.shutdown()
    close_router()


app = FastAPI(
    title="ScrapMart API",
    description="Marketplace connecting scrap sellers with local collectors.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url=None,
)

from app.api.v1.map_system import install_map_system

install_fastapi_handlers(app)
install_cursor_handlers(app)
install_map_system(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$" if settings.ENV != "production" else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api)

@app.get("/media/{key:path}", tags=["Media"])
def get_media_file(key: str):
    from fastapi.responses import Response

    item = storage.get(key)
    if not item:
        raise HTTPException(404, "Media file not found")
    data, content_type = item
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.api_route("/health", methods=["GET", "HEAD"], tags=["Meta"])
def health():
    return {
        "status": "ok",
        "shards": len(get_router().shard_ids),
        "redis_configured": bool(settings.MAP_REDIS_URL or settings.REDIS_URL),
    }


@app.api_route("/", methods=["GET", "HEAD"], tags=["Meta"])
def root_health():
    return {
        "status": "ok",
        "service": "ScrapMart API",
        "version": "1.0.0",
    }



@app.get("/redis/status", tags=["Meta"])
def redis_status():
    from app.core.redis_client import get_redis_health

    return get_redis_health()


@app.get("/shards/status", tags=["Meta"])
def shards_status():
    return get_router().report()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=settings.ENV != "production")

