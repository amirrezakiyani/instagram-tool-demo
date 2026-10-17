import asyncio
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask

app = FastAPI(title="Downloadino Media Downloader", docs_url=None, redoc_url=None)
ALLOWED_HOSTS = {"instagram.com", "www.instagram.com"}
POST_PATH = re.compile(r"^/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+/?$")
MAX_FILE_BYTES = 250 * 1024 * 1024


class DownloadRequest(BaseModel):
    url: HttpUrl
    format: Literal["video", "audio", "image"] = "video"


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or not POST_PATH.fullmatch(parsed.path):
        raise HTTPException(status_code=400, detail="Enter a valid public Instagram Reel or post URL.")
    return value


@app.get("/health")
def health():
    return {"status": "ok", "engine": "yt-dlp", "formats": ["video", "audio", "image"]}


@app.post("/api/download")
async def download_media(payload: DownloadRequest):
    source_url = validate_url(str(payload.url))
    temp_dir = tempfile.mkdtemp(prefix="downloadino-")
    output_template = str(Path(temp_dir) / "download.%(ext)s")
    options = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 25,
        "retries": 1,
        "fragment_retries": 1,
        "max_filesize": MAX_FILE_BYTES,
        "restrictfilenames": True,
    }

    if payload.format == "video":
        options.update({
            "format": "best[ext=mp4]/best",
            "merge_output_format": "mp4",
        })
        expected_ext, media_type = ".mp4", "video/mp4"
    elif payload.format == "audio":
        options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        expected_ext, media_type = ".mp3", "audio/mpeg"
    else:
        options.update({
            "skip_download": True,
            "writethumbnail": True,
            "postprocessors": [{
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
            }],
        })
        expected_ext, media_type = ".jpg", "image/jpeg"

    def extract_and_download():
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=True)
            media_id = str(info.get("id") or "instagram-media")
            candidates = list(Path(temp_dir).glob("*" + expected_ext))
            if not candidates and payload.format == "video":
                candidates = list(Path(temp_dir).glob("download.*"))
                candidates = [p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
            if not candidates:
                raise RuntimeError("The requested media file was not produced.")
            path = candidates[0]
            if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > MAX_FILE_BYTES:
                raise RuntimeError("The media file is empty or exceeds the 250 MB limit.")
            return path, media_id

    try:
        path, media_id = await asyncio.to_thread(extract_and_download)
    except yt_dlp.utils.DownloadError as exc:
        cleanup(temp_dir)
        detail = str(exc).lower()
        if "private" in detail or "login" in detail or "sign in" in detail:
            raise HTTPException(status_code=422, detail="Instagram requires login or the post is private. Only accessible public posts are supported.") from exc
        if payload.format == "image":
            message = "Instagram did not provide an accessible thumbnail for this post."
        else:
            message = "Instagram did not provide accessible media. Try another public post later."
        raise HTTPException(status_code=422, detail=message) from exc
    except HTTPException:
        cleanup(temp_dir)
        raise
    except Exception as exc:
        cleanup(temp_dir)
        raise HTTPException(status_code=502, detail="The requested media could not be prepared. Please try again.") from exc

    ext = {"video": "mp4", "audio": "mp3", "image": "jpg"}[payload.format]
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"downloadino-{media_id}.{ext}",
        background=BackgroundTask(cleanup, temp_dir),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def cleanup(path: str):
    shutil.rmtree(path, ignore_errors=True)
