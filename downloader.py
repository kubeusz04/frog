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
            "quiet": True,
            "skip_download": True,
            "extract_flat": True,
            "default_search": "ytsearch",
            "noplaylist": True,
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
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
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
            raise DownloadError(str(exc)) from exc

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
