import shutil
import tempfile
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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


@dataclass
class Participant:
    client_id: str
    name: str
    role: str
    websocket: WebSocket


@dataclass
class JamSession:
    code: str
    host_id: str | None = None
    clients: dict[str, Participant] = field(default_factory=dict)
    ready: set[str] = field(default_factory=set)
    state: dict = field(
        default_factory=lambda: {
            "type": "state",
            "video_id": None,
            "title": "",
            "channel": "",
            "position": 0.0,
            "playing": False,
            "updated_at": time.time(),
        }
    )


class JamManager:
    def __init__(self) -> None:
        self.sessions: dict[str, JamSession] = {}
        self.lock = asyncio.Lock()

    async def connect(
        self,
        code: str,
        client_id: str,
        name: str,
        role: str,
        websocket: WebSocket,
    ) -> JamSession | None:
        await websocket.accept()
        async with self.lock:
            session = self.sessions.get(code)
            if role == "host":
                if session is None:
                    session = JamSession(code=code, host_id=client_id)
                    self.sessions[code] = session
                session.host_id = client_id
            elif session is None:
                await websocket.send_json({"type": "error", "message": "Nie znaleziono sesji Jam."})
                await websocket.close()
                return None

            session.clients[client_id] = Participant(client_id, name, role, websocket)
            await websocket.send_json(session.state)

        await self.broadcast_participants(session)
        return session

    async def disconnect(self, session: JamSession, client_id: str) -> None:
        async with self.lock:
            session.clients.pop(client_id, None)
            session.ready.discard(client_id)
        await self.broadcast_participants(session)

    async def broadcast(
        self,
        session: JamSession,
        payload: dict,
        exclude: set[str] | None = None,
    ) -> None:
        exclude = exclude or set()
        dead = []
        for client_id, participant in list(session.clients.items()):
            if client_id in exclude:
                continue
            try:
                await participant.websocket.send_text(json.dumps(payload))
            except Exception:
                dead.append(client_id)
        for client_id in dead:
            session.clients.pop(client_id, None)

    async def broadcast_participants(self, session: JamSession) -> None:
        await self.broadcast(
            session,
            {
                "type": "participants",
                "participants": [
                    {
                        "client_id": item.client_id,
                        "name": item.name,
                        "role": item.role,
                        "ready": item.client_id in session.ready,
                    }
                    for item in session.clients.values()
                ],
            },
        )

    async def handle_message(self, session: JamSession, client_id: str, payload: dict) -> None:
        participant = session.clients.get(client_id)
        if participant is None:
            return

        msg_type = payload.get("type")
        if msg_type == "load" and participant.role == "host":
            session.ready = {client_id}
            session.state.update(
                {
                    "type": "state",
                    "video_id": payload.get("video_id"),
                    "title": payload.get("title", ""),
                    "channel": payload.get("channel", ""),
                    "position": 0.0,
                    "playing": False,
                    "updated_at": time.time(),
                }
            )
            await self.broadcast(session, payload)
            await self.broadcast_participants(session)
            return

        if msg_type == "sync" and participant.role == "host":
            session.state.update(
                {
                    "position": float(payload.get("position", 0.0)),
                    "playing": bool(payload.get("playing", False)),
                    "updated_at": time.time(),
                }
            )
            await self.broadcast(session, payload, exclude={client_id})
            return

        if msg_type == "ready":
            session.ready.add(client_id)
            await self.broadcast_participants(session)
            await self.broadcast(session, {"type": "ready", "client_id": client_id}, exclude={client_id})
            return

        if msg_type == "chat":
            await self.broadcast(
                session,
                {
                    "type": "chat",
                    "name": participant.name,
                    "message": payload.get("message", ""),
                    "sent_at": time.time(),
                },
            )


jam_manager = JamManager()


def _cleanup(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/")
def root() -> dict:
    return {
        "name": "Frog Mobile API",
        "ok": True,
        "endpoints": ["/api/health", "/api/search", "/api/download/{video_id}", "/ws/{code}"],
    }


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


@app.websocket("/ws/{code}")
async def websocket_endpoint(
    websocket: WebSocket,
    code: str,
    client_id: str,
    name: str = "Guest",
    role: str = "guest",
) -> None:
    session = await jam_manager.connect(code.upper(), client_id, name, role, websocket)
    if session is None:
        return
    try:
        while True:
            payload = json.loads(await websocket.receive_text())
            await jam_manager.handle_message(session, client_id, payload)
    except WebSocketDisconnect:
        await jam_manager.disconnect(session, client_id)
    except Exception:
        await jam_manager.disconnect(session, client_id)
