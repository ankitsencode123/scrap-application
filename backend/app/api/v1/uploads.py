"""Image upload endpoints."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.core.security import CurrentUser, SellerUser
from app.core.storage import process_image, storage
from app.db.base import DB
from app.db.models import User
from app.schemas import UploadedImage

uploads_router = APIRouter(prefix="/uploads", tags=["Uploads"])


# ---------------------------------------------------------------- UPLOADS ---
@uploads_router.post("/images", response_model=list[UploadedImage], status_code=201)
def upload_images(seller: SellerUser, files: Annotated[list[UploadFile], File(description="1-6 scrap photos")]):
    """Upload scrap photos to cloud storage in parallel. Use the returned URLs in POST /listings."""
    if not files:
        raise HTTPException(400, "No files provided")
    if len(files) > settings.MAX_LISTING_IMAGES:
        raise HTTPException(400, f"Upload at most {settings.MAX_LISTING_IMAGES} images at a time")

    # Read raw bytes first (synchronous I/O from request buffer)
    items_to_process: list[tuple[str, bytes]] = []
    for f in files:
        raw = f.file.read(settings.MAX_IMAGE_BYTES + 1)
        if len(raw) > settings.MAX_IMAGE_BYTES:
            raise HTTPException(413, f"{f.filename}: image exceeds {settings.MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        items_to_process.append((f.filename or "photo.jpg", raw))

    def _process(item: tuple[str, bytes]) -> tuple[bytes, str, str]:
        _, raw = item
        data = process_image(raw)
        key = f"listings/{seller.id}/{uuid.uuid4().hex}.jpg"
        return (data, key, "image/jpeg")

    # In-memory image processing in parallel
    if len(items_to_process) == 1:
        processed_items = [_process(items_to_process[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(len(items_to_process), 6)) as executor:
            processed_items = list(executor.map(_process, items_to_process))

    # Batch save all images in a single database round-trip
    urls = storage.save_batch(processed_items)
    return [
        UploadedImage(url=url, key=key)
        for url, (_, key, _) in zip(urls, processed_items)
    ]


@uploads_router.post("/avatar", response_model=UploadedImage, status_code=201)
def upload_avatar(
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="Profile photo")],
    db: DB,
):
    """Upload profile photo for both Seller and Collector and update avatar_url."""
    raw = file.file.read(settings.MAX_IMAGE_BYTES + 1)
    if len(raw) > settings.MAX_IMAGE_BYTES:
        raise HTTPException(413, f"Image exceeds {settings.MAX_IMAGE_BYTES // (1024 * 1024)} MB")

    data = process_image(raw, max_edge=512)
    key = f"avatars/{user.id}/{uuid.uuid4().hex}.jpg"
    url = storage.save(data, key, "image/jpeg")

    # Update user's avatar_url in the corresponding shard
    s_db = db.for_id(user.id)
    u = s_db.get(User, user.id)
    if u:
        u.avatar_url = url
        s_db.commit()

    return UploadedImage(url=url, key=key)
