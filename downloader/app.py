import asyncio
import os
import re
import tempfile
from pathlib import Path
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


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or not POST_PATH.fullmatch(parsed.path):
        raise HTTPException(status_code=400, detail="Enter a valid public Instagram Reel or post URL.")
    return value


@app.get("/health")
def health():
    return {"status": "ok", "engine": "yt-dlp"}


@app.post("/api/download")
async def download_video(payload: DownloadRequest):
    source_url = validate_url(str(payload.url))
    temp_dir = tempfile.mkdtemp(prefix="downloadino-")
    output_template = str(Path(temp_dir) / "download.%(ext)s")
    options = {
        "outtmpl": output_template,
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 1,
        "fragment_retries": 1,
        "max_filesize": MAX_FILE_BYTES,
        "restrictfilenames": True,
    }

    def extract_and_download():
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=True)
            path = Path(downloader.prepare_filename(info))
            if not path.exists():
                mp4_path = path.with_suffix(".mp4")
                if mp4_path.exists():
                    path = mp4_path
            if not path.exists() or not path.is_file():
                raise RuntimeError("The extractor did not produce a video file.")
            if path.stat().st_size <= 0 or path.stat().st_size > MAX_FILE_BYTES:
                raise RuntimeError("The video is empty or exceeds the 250 MB limit.")
            return path, str(info.get("id") or "instagram-video")

    try:
        path, media_id = await asyncio.to_thread(extract_and_download)
    except yt_dlp.utils.DownloadError as exc:
        cleanup(temp_dir)
        detail = str(exc).lower()
        if "private" in detail or "login" in detail or "sign in" in detail:
            raise HTTPException(status_code=422, detail="Instagram requires login or the post is private. Only accessible public posts are supported.") from exc
        raise HTTPException(status_code=422, detail="Instagram did not provide an accessible video. Try another public post later.") from exc
    except HTTPException:
        cleanup(temp_dir)
        raise
    except Exception as exc:
        cleanup(temp_dir)
        raise HTTPException(status_code=502, detail="The video could not be downloaded from Instagram. Please try again.") from exc

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"downloadino-{media_id}.mp4",
        background=BackgroundTask(cleanup, temp_dir),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def cleanup(path: str):
    import shutil
    shutil.rmtree(path, ignore_errors=True)
