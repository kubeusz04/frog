import base64
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

import yt_dlp
from pytube import YouTube

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
        "nocheckcertificate": True,
    }
    cookiefile = _cookies_file()
    if cookiefile:
        options["cookiefile"] = cookiefile
    return options


def _read_secret_value(name: str, filename: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value

    secret_file = Path("/etc/secrets") / filename
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip()
    return ""


def _youtube_extractor_args(clients: list[str]) -> dict:
    args: dict[str, list[str]] = {"player_client": clients}
    po_token = _read_secret_value("FROG_YTDLP_PO_TOKEN", "youtube-po-token.txt")
    visitor_data = _read_secret_value("FROG_YTDLP_VISITOR_DATA", "youtube-visitor-data.txt")

    if po_token:
        args["po_token"] = [
            f"web.gvs+{po_token}",
            f"mweb.gvs+{po_token}",
            f"android.gvs+{po_token}",
            f"ios.gvs+{po_token}",
        ]
    if visitor_data:
        args["visitor_data"] = [visitor_data]

    return {"youtube": args}


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

        # Try Pytube first (video download often more stable)
        pytube_error = None
        try:
            if progress_cb:
                progress_cb({"status": "downloading", "percent": 10.0})
            
            yt = YouTube(YOUTUBE_WATCH.format(video_id=video_id))
            
            # Get best video stream
            stream = yt.streams.filter(progressive=False, file_extension="mp4").order_by("resolution").desc().first()
            
            if stream:
                if progress_cb:
                    progress_cb({"status": "downloading", "percent": 30.0})
                
                temp_video = self.cache_dir / f"{video_id}_temp.mp4"
                stream.download(output_path=self.cache_dir, filename=temp_video.name)
                
                if progress_cb:
                    progress_cb({"status": "converting", "percent": 70.0})
                
                # Convert video to MP3 using ffmpeg
                cmd = [
                    ffmpeg_path(),
                    "-i", str(temp_video),
                    "-vn",  # no video
                    "-acodec", "libmp3lame",
                    "-q:a", "5",  # quality
                    str(mp3_path),
                ]
                subprocess.run(cmd, check=True, capture_output=True)
                temp_video.unlink(missing_ok=True)
                
                if progress_cb:
                    progress_cb({"status": "finished", "percent": 100.0})
                
                return {
                    "video_id": video_id,
                    "title": yt.title,
                    "channel": yt.author or "",
                    "duration": yt.length,
                    "file_path": str(mp3_path),
                    "file_size": os.path.getsize(mp3_path),
                    "downloaded_at": time.time(),
                }
        except Exception as exc:
            pytube_error = str(exc)
            # Fall back to yt-dlp
            pass

        # Fallback: use yt-dlp (audio download with multiple fallback formats)
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
        base_options = {
            **_yt_dlp_base_options(),
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
        attempts = [
            {
                "format": "bestaudio[protocol^=http]/bestaudio/best[protocol^=http]/best",
            },
            {
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "extractor_args": _youtube_extractor_args(["web", "web_safari", "mweb", "android", "ios"]),
            },
            {
                "format": "ba/b",
                "extractor_args": _youtube_extractor_args(["tv", "tv_simply", "web_embedded", "mweb", "ios", "android"]),
            },
            {
                "format": "best",
                "extractor_args": _youtube_extractor_args(["tv", "tv_simply", "web", "web_safari", "android", "ios"]),
            },
            {
                "format": "bestaudio/best",
                "extractor_args": {
                    "youtube": {
                        **_youtube_extractor_args(["default", "mweb", "web_safari"])["youtube"],
                        "formats": ["incomplete"],
                    }
                },
            },
        ]

        last_error = pytube_error or ""
        info = None
        for attempt in attempts:
            options = {**base_options, **attempt}
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(YOUTUBE_WATCH.format(video_id=video_id), download=True)
                break
            except Exception as exc:
                last_error = str(exc)
                for leftover in self.cache_dir.glob(f"{video_id}*"):
                    if leftover.suffix.lower() != ".mp3":
                        leftover.unlink(missing_ok=True)

        if info is None:
            message = last_error
            if "Sign in to confirm" in message or "not a bot" in message:
                message = (
                    "YouTube blocks this hosting IP and requires a signed-in session. "
                    "Add YouTube cookies as a Render Secret File named youtube-cookies.txt or run the backend on a VPS/local machine."
                )
            elif "Requested format is not available" in message:
                message = (
                    "YouTube did not expose a downloadable audio format for this track from the current server. "
                    "Try another track, refresh cookies, or move the backend to a VPS/local machine with a less restricted IP."
                )
            raise DownloadError(message)

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
