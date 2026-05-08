import json
import os
import sys
from pathlib import Path


APP_NAME = "Frog"
APP_DIR = Path(os.environ.get("FROG_HOME", Path.home() / ".frog"))
CACHE_DIR = APP_DIR / "cache"
DB_PATH = APP_DIR / "frog.sqlite3"
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CACHE_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_JAM_HOST = "0.0.0.0"
DEFAULT_JAM_PORT = 8765


def ensure_app_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def bundled_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).joinpath(*parts)
    return Path(__file__).resolve().parent.joinpath(*parts)


def ffmpeg_path() -> str:
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if getattr(sys, "frozen", False):
        return str(bundled_path("bin", exe_name))

    local = bundled_path("bin", exe_name)
    if local.exists():
        return str(local)
    return "ffmpeg"


def ffprobe_path() -> str:
    exe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    if getattr(sys, "frozen", False):
        return str(bundled_path("bin", exe_name))

    local = bundled_path("bin", exe_name)
    if local.exists():
        return str(local)
    return "ffprobe"


def configure_ffmpeg_environment() -> None:
    ffmpeg = ffmpeg_path()
    ffprobe = ffprobe_path()
    ffmpeg_dir = Path(ffmpeg).parent if ffmpeg != "ffmpeg" else None
    ffprobe_dir = Path(ffprobe).parent if ffprobe != "ffprobe" else None
    path_parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]

    for folder in (ffmpeg_dir, ffprobe_dir):
        if folder and folder.exists():
            folder_text = str(folder)
            if folder_text not in path_parts:
                path_parts.insert(0, folder_text)

    if path_parts:
        os.environ["PATH"] = os.pathsep.join(path_parts)


def load_json_config() -> dict:
    ensure_app_dirs()
    if not CONFIG_PATH.exists():
        return {"cache_limit_bytes": DEFAULT_CACHE_LIMIT_BYTES}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"cache_limit_bytes": DEFAULT_CACHE_LIMIT_BYTES}


def save_json_config(values: dict) -> None:
    ensure_app_dirs()
    CONFIG_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "0 B"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"
