import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from downloader import DownloadError, YouTubeDownloader


app = FastAPI(title="Frog Mobile API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


def _cleanup(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/search")
def search(payload: SearchRequest) -> dict:
    query = payload.query.strip()
    if not query:
        return {"results": []}

    limit = max(1, min(payload.limit, 20))
    try:
        results = YouTubeDownloader().search(query, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"results": results}


@app.get("/api/download/{video_id}")
def download(video_id: str, background_tasks: BackgroundTasks) -> FileResponse:
    if not video_id or len(video_id) > 32:
        raise HTTPException(status_code=400, detail="Nieprawidlowy video_id.")

    temp_dir = Path(tempfile.mkdtemp(prefix="frog-mobile-"))
    downloader = YouTubeDownloader(cache_dir=temp_dir)
    try:
        track = downloader.download(video_id)
    except DownloadError as exc:
        _cleanup(temp_dir)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        _cleanup(temp_dir)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    file_path = Path(track["file_path"])
    if not file_path.exists():
        _cleanup(temp_dir)
        raise HTTPException(status_code=500, detail="Nie znaleziono pobranego pliku.")

    background_tasks.add_task(_cleanup, temp_dir)
    filename = f"{video_id}.mp3"
    headers = {
        "X-Frog-Video-Id": video_id,
        "X-Frog-Title": quote(str(track.get("title") or video_id)),
        "X-Frog-Channel": quote(str(track.get("channel") or "")),
    }
    return FileResponse(
        file_path,
        media_type="audio/mpeg",
        filename=filename,
        headers=headers,
        background=background_tasks,
    )
