import base64
import os
import re
import time
from pathlib import Path
from typing import Callable

import yt_dlp

from config import CACHE_DIR, ensure_app_dirs, ffmpeg_path


YOUTUBE_WATCH = "https://www.youtube.com/watch?v={video_id}"


class DownloadError(RuntimeError):
    pass


def _cookies_file() -> str | None:
    runtime_dir = Path(os.environ.get("FROG_RUNTIME_DIR", "/tmp/frog"))
    runtime_dir.mkdir(parents=True, exist_ok=True)

    cookies_file = os.environ.get("FROG_YTDLP_COOKIES_FILE", "").strip()
    if cookies_file and Path(cookies_file).exists():
        source = Path(cookies_file)
        target = runtime_dir / "youtube-cookies.txt"
        if source.resolve() != target.resolve():
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return str(target)

    render_secret_file = Path("/etc/secrets/youtube-cookies.txt")
    if render_secret_file.exists():
        target = runtime_dir / "youtube-cookies.txt"
        target.write_text(render_secret_file.read_text(encoding="utf-8"), encoding="utf-8")
        return str(target)

    cookies_text = os.environ.get("FROG_YTDLP_COOKIES_TEXT", "").strip()
    cookies_b64 = os.environ.get("FROG_YTDLP_COOKIES_BASE64", "").strip()
    if not cookies_text and not cookies_b64:
        return None

    if cookies_b64:
        try:
            cookies_text = base64.b64decode(cookies_b64).decode("utf-8")
        except Exception as exc:
            raise DownloadError("Nie udalo sie odczytac FROG_YTDLP_COOKIES_BASE64.") from exc

    cookie_path = runtime_dir / "youtube-cookies.txt"
    cookie_path.write_text(cookies_text, encoding="utf-8")
    return str(cookie_path)


def _yt_dlp_base_options() -> dict:
    options = {
        "quiet": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
    }
    cookiefile = _cookies_file()
    if cookiefile:
        options["cookiefile"] = cookiefile
    return options


def _safe_text(value: str | None) -> str:
    return value or ""


def _extract_video_id(entry: dict) -> str:
    video_id = entry.get("id") or entry.get("url") or ""
    match = re.search(r"([A-Za-z0-9_-]{11})", video_id)
    return match.group(1) if match else video_id


class YouTubeDownloader:
    def __init__(self, cache_dir: Path = CACHE_DIR):
        ensure_app_dirs()
        self.cache_dir = cache_dir

    def search(self, query: str, limit: int = 10) -> list[dict]:
        query = query.strip()
        if not query:
            return []

        options = {
            **_yt_dlp_base_options(),
            "skip_download": True,
            "extract_flat": True,
            "default_search": "ytsearch",
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

        results = []
        for entry in data.get("entries") or []:
            video_id = _extract_video_id(entry)
            if not video_id:
                continue
            results.append(
                {
                    "video_id": video_id,
                    "title": _safe_text(entry.get("title")),
                    "channel": _safe_text(entry.get("uploader") or entry.get("channel")),
                    "duration": entry.get("duration"),
                    "webpage_url": entry.get("webpage_url")
                    or YOUTUBE_WATCH.format(video_id=video_id),
                }
            )
        return results

    def download(
        self,
        video_id: str,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        ensure_app_dirs()
        mp3_path = self.cache_dir / f"{video_id}.mp3"
        if mp3_path.exists():
            if progress_cb:
                progress_cb({"status": "finished", "percent": 100.0})
            return self._track_from_existing(video_id, mp3_path)

        def hook(status: dict) -> None:
            if not progress_cb:
                return
            if status.get("status") == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
                downloaded = status.get("downloaded_bytes") or 0
                percent = (downloaded / total * 100.0) if total else 0.0
                progress_cb(
                    {
                        "status": "downloading",
                        "percent": percent,
                        "downloaded": downloaded,
                        "total": total,
                    }
                )
            elif status.get("status") == "finished":
                progress_cb({"status": "converting", "percent": 99.0})

        outtmpl = str(self.cache_dir / "%(id)s.%(ext)s")
        options = {
            **_yt_dlp_base_options(),
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "progress_hooks": [hook],
            "ffmpeg_location": ffmpeg_path(),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(YOUTUBE_WATCH.format(video_id=video_id), download=True)
        except Exception as exc:
            message = str(exc)
            if "Sign in to confirm" in message or "not a bot" in message:
                message = (
                    "YouTube blokuje IP hostingu i wymaga zalogowanej sesji. "
                    "Dodaj cookies do Render jako FROG_YTDLP_COOKIES_BASE64 albo uruchom backend na VPS/lokalnie."
                )
            raise DownloadError(message) from exc

        if not mp3_path.exists():
            candidates = sorted(self.cache_dir.glob(f"{video_id}*.mp3"))
            if not candidates:
                raise DownloadError("Nie znaleziono pliku MP3 po konwersji.")
            mp3_path = candidates[0]
            target = self.cache_dir / f"{video_id}.mp3"
            if mp3_path != target:
                mp3_path.replace(target)
                mp3_path = target

        if progress_cb:
            progress_cb({"status": "finished", "percent": 100.0})

        return {
            "video_id": video_id,
            "title": info.get("title") or video_id,
            "channel": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration"),
            "file_path": str(mp3_path),
            "file_size": os.path.getsize(mp3_path),
            "downloaded_at": time.time(),
        }

    def _track_from_existing(self, video_id: str, mp3_path: Path) -> dict:
        return {
            "video_id": video_id,
            "title": video_id,
            "channel": "",
            "duration": None,
            "file_path": str(mp3_path),
            "file_size": os.path.getsize(mp3_path),
            "downloaded_at": time.time(),
        }
