import io
import json
import os
import re
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
# Render sits behind one trusted reverse proxy; use its forwarded client address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://amirrezakiyani.github.io")
CORS(app, resources={r"/api/*": {"origins": [ALLOWED_ORIGIN]}})

WINDOW_SECONDS = 60
MAX_REQUESTS = 5
MAX_MEDIA_BYTES = 100 * 1024 * 1024
hits = defaultdict(deque)


def valid_instagram_url(raw: str) -> bool:
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in {
            "instagram.com", "www.instagram.com", "m.instagram.com"
        }:
            return False
        if parsed.port not in (None, 80, 443):
            return False
        parts = [part for part in parsed.path.split("/") if part]
        # Do not accept profile pages or bulk/profile downloads.
        return len(parts) >= 2 and parts[0].lower() in {"p", "reel", "tv"}
    except (TypeError, ValueError):
        return False


def allowed(ip: str) -> bool:
    now = time.time()
    bucket = hits[ip]
    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= MAX_REQUESTS:
        return False
    bucket.append(now)
    return True


def yt_info(url: str) -> dict:
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--no-playlist",
        "--skip-download",
        "--no-warnings",
        "--socket-timeout", "20",
        "--retries", "1",
        "--user-agent", "Mozilla/5.0 (compatible; Downloadino/1.0)",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:] or "extractor failed")
    return json.loads(result.stdout)


def safe_name(info: dict, ext: str) -> str:
    title = re.sub(r"[^\w\- آ-ی]", " ", str(info.get("title") or "instagram-media"), flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title).strip()[:80] or "instagram-media"
    return f"{title}.{ext}"


def pick_format(info: dict, audio_only: bool = False, kind: str = "video") -> tuple[str, str]:
    if audio_only:
        audio = info.get("url")
        if not audio:
            raise RuntimeError("audio unavailable")
        return audio, "mp3"
    requested = kind.lower()
    if requested == "image":
        requested = "photo"
    if requested == "photo":
        direct = info.get("thumbnail") or info.get("url")
        if not direct:
            raise RuntimeError("image unavailable")
        return direct, "jpg"
    formats = info.get("formats") or []
    candidates = [f for f in formats if f.get("url") and f.get("vcodec") != "none"]
    candidates.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    if candidates:
        chosen = candidates[0]
        return chosen["url"], chosen.get("ext") or "mp4"
    direct = info.get("url")
    if not direct:
        raise RuntimeError("video unavailable")
    return direct, info.get("ext") or "mp4"


def fetch_bytes(url: str) -> bytes:
    data = bytearray()
    with requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=(15, 60), stream=True) as response:
        response.raise_for_status()
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_MEDIA_BYTES:
            raise RuntimeError("file too large")
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) > MAX_MEDIA_BYTES:
                raise RuntimeError("file too large")
    return bytes(data)


def download_video_with_audio(url: str) -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "media.%(ext)s"
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--socket-timeout", "20",
            "--retries", "1",
            "--user-agent", "Mozilla/5.0 (compatible; Downloadino/1.0)",
            "-f", "bestvideo*+bestaudio/best",
            "--merge-output-format", "mp4",
            "-o", str(output),
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-800:] or "video download failed")
        files = [p for p in Path(folder).glob("media.*") if p.is_file()]
        if not files:
            raise RuntimeError("video file unavailable")
        media = files[0]
        if media.stat().st_size > MAX_MEDIA_BYTES:
            raise RuntimeError("file too large")
        return media.read_bytes(), "mp4"


def safe_log_text(value: str) -> str:
    return re.sub(r"https?://\S+", "[url redacted]", value)[:240]


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "درخواست بیش از اندازه بزرگ است."}), 413


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "downloadino-instagram-api"})


@app.post("/api/download")
def download():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "درخواست JSON معتبر نیست."}), 400
    url = str(payload.get("url") or "").strip()
    kind = str(payload.get("kind") or request.args.get("kind") or "video").lower()
    if len(url) > 2048 or not valid_instagram_url(url):
        return jsonify({"error": "لینک عمومیِ یک پست یا ریل معتبر اینستاگرام وارد کنید."}), 400
    if kind not in {"video", "photo", "image", "audio", "music", "mp3"}:
        return jsonify({"error": "نوع فایل پشتیبانی نمی‌شود."}), 400
    if not allowed(request.remote_addr or "unknown"):
        return jsonify({"error": "تعداد درخواست‌ها زیاد است؛ یک دقیقه بعد دوباره امتحان کنید."}), 429
    try:
        info = yt_info(url)
        if kind in {"audio", "music", "mp3"}:
            source, _ = pick_format(info, audio_only=True, kind=kind)
            with tempfile.TemporaryDirectory() as folder:
                input_path = Path(folder) / "source"
                output_path = Path(folder) / "audio.mp3"
                input_path.write_bytes(fetch_bytes(source))
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-acodec", "libmp3lame", "-b:a", "192k", str(output_path)],
                    capture_output=True, timeout=90, check=True,
                )
                if output_path.stat().st_size > MAX_MEDIA_BYTES:
                    raise RuntimeError("file too large")
                return send_file(io.BytesIO(output_path.read_bytes()), mimetype="audio/mpeg", as_attachment=True, download_name=safe_name(info, "mp3"))
        if kind not in {"photo", "image"}:
            data, ext = download_video_with_audio(url)
            mime = "video/mp4"
        else:
            source, ext = pick_format(info, audio_only=False, kind=kind)
            data = fetch_bytes(source)
            mime = "image/jpeg"
        return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True, download_name=safe_name(info, ext))
    except subprocess.TimeoutExpired:
        return jsonify({"error": "پردازش طول کشید؛ دوباره امتحان کنید."}), 504
    except Exception as exc:
        app.logger.warning("download failed (%s): %s", type(exc).__name__, safe_log_text(str(exc)))
        return jsonify({"error": "این لینک قابل استخراج نیست یا حساب خصوصی است. فقط محتوای عمومی و مجاز پشتیبانی می‌شود."}), 422


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
