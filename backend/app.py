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

app = Flask(__name__)
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://amirrezakiyani.github.io")
CORS(app, resources={r"/api/*": {"origins": [ALLOWED_ORIGIN, "http://localhost:*"]}})

WINDOW_SECONDS = 60
MAX_REQUESTS = 5
hits = defaultdict(deque)


def valid_instagram_url(raw: str) -> bool:
    try:
        parsed = urlparse(raw)
        return parsed.scheme in {"http", "https"} and parsed.hostname.lower() in {
            "instagram.com", "www.instagram.com", "m.instagram.com"
        }
    except Exception:
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
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    response.raise_for_status()
    if len(response.content) > 100 * 1024 * 1024:
        raise RuntimeError("file too large")
    return response.content


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "downloadino-experimental"})


@app.post("/api/download")
def download():
    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url") or "").strip()
    kind = str(payload.get("kind") or request.args.get("kind") or "video").lower()
    if not valid_instagram_url(url):
        return jsonify({"error": "لینک معتبر عمومی اینستاگرام وارد کنید."}), 400
    if not allowed(request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")):
        return jsonify({"error": "تعداد درخواست‌ها زیاد است؛ یک دقیقه بعد دوباره امتحان کنید."}), 429
    try:
        info = yt_info(url)
        if kind in {"audio", "music", "mp3"}:
            source, _ = pick_format(info, audio_only=True, kind=kind)
            with tempfile.TemporaryDirectory() as folder:
                input_path = Path(folder) / "source"
                output_path = Path(folder) / "audio.mp3"
                input_path.write_bytes(fetch_bytes(source))
                subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-vn", "-acodec", "libmp3lame", "-b:a", "192k", str(output_path)], capture_output=True, timeout=90, check=True)
                return send_file(io.BytesIO(output_path.read_bytes()), mimetype="audio/mpeg", as_attachment=True, download_name=safe_name(info, "mp3"))
        source, ext = pick_format(info, audio_only=False, kind=kind)
        data = fetch_bytes(source)
        mime = "image/jpeg" if kind in {"photo", "image"} else "video/mp4"
        return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True, download_name=safe_name(info, ext))
    except subprocess.TimeoutExpired:
        return jsonify({"error": "پردازش طول کشید؛ دوباره امتحان کنید."}), 504
    except Exception as exc:
        app.logger.warning("download failed: %s", exc)
        return jsonify({"error": "این لینک قابل استخراج نیست یا حساب خصوصی است. فقط محتوای عمومی و مجاز پشتیبانی می‌شود."}), 422


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
