import asyncio
import base64
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.websockets import WebSocketDisconnect, WebSocketState


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

logger = logging.getLogger("novnc_gateway")


class Settings:
    def __init__(self) -> None:
        self.targets_file = Path(os.getenv("VNC_TARGETS_FILE", "data/targets.json"))
        self.novnc_root = Path(os.getenv("NOVNC_ROOT", "/usr/share/novnc"))
        self.app_username = os.getenv("APP_USERNAME", "")
        self.app_password = os.getenv("APP_PASSWORD", "")

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_username and self.app_password)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clean_string(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def validate_host_value(value: str) -> str:
    if any(char.isspace() for char in value):
        raise ValueError("host must not contain whitespace")
    return value


class TargetBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(5900, ge=1, le=65535)
    description: str = Field("", max_length=240)

    @field_validator("name", "host", "description", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        return clean_string(value)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        return validate_host_value(value)


class TargetCreate(TargetBase):
    password: str = Field("", max_length=256)


class TargetUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    host: str | None = Field(None, min_length=1, max_length=253)
    port: int | None = Field(None, ge=1, le=65535)
    description: str | None = Field(None, max_length=240)
    password: str | None = Field(None, max_length=256)

    @field_validator("name", "host", "description", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        return clean_string(value)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_host_value(value)


class TargetRecord(TargetBase):
    id: str
    created_at: datetime
    updated_at: datetime
    password: str = Field("", max_length=256)


class Target(TargetBase):
    id: str
    created_at: datetime
    updated_at: datetime
    has_password: bool = False


class ProbeResult(BaseModel):
    ok: bool
    message: str


class AppConfig(BaseModel):
    auth_enabled: bool


class TargetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def list_targets(self) -> list[TargetRecord]:
        async with self._lock:
            return self._load_targets()

    async def get_target(self, target_id: str) -> TargetRecord | None:
        async with self._lock:
            return self._find_target(target_id)

    async def create_target(self, payload: TargetCreate) -> TargetRecord:
        async with self._lock:
            targets = self._load_targets()
            now = utc_now()
            target = TargetRecord(
                id=uuid4().hex,
                created_at=now,
                updated_at=now,
                **payload.model_dump(),
            )
            targets.append(target)
            self._save_targets(targets)
            return target

    async def update_target(self, target_id: str, payload: TargetUpdate) -> TargetRecord | None:
        async with self._lock:
            targets = self._load_targets()
            for index, target in enumerate(targets):
                if target.id != target_id:
                    continue
                update_data = payload.model_dump(exclude_unset=True)
                if "password" in update_data and update_data["password"] is None:
                    update_data["password"] = ""
                updated = target.model_copy(update={**update_data, "updated_at": utc_now()})
                targets[index] = updated
                self._save_targets(targets)
                return updated
            return None

    async def delete_target(self, target_id: str) -> bool:
        async with self._lock:
            targets = self._load_targets()
            remaining = [target for target in targets if target.id != target_id]
            if len(remaining) == len(targets):
                return False
            self._save_targets(remaining)
            return True

    def _find_target(self, target_id: str) -> TargetRecord | None:
        for target in self._load_targets():
            if target.id == target_id:
                return target
        return None

    def _load_targets(self) -> list[TargetRecord]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            raw_targets = json.load(handle)
        if not isinstance(raw_targets, list):
            raise ValueError(f"{self.path} must contain a JSON array")
        return [TargetRecord.model_validate(item) for item in raw_targets]

    def _save_targets(self, targets: list[TargetRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = [target.model_dump(mode="json") for target in targets]
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_path, self.path)


settings = Settings()
store = TargetStore(settings.targets_file)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The portal opens TCP connections to any host the container can reach, so an
    # unauthenticated instance can be used as a port scanner / TCP tunnel. Warn
    # loudly when it is left without HTTP Basic auth.
    if not settings.auth_enabled:
        logger.warning(
            "HTTP Basic auth is disabled. Set APP_USERNAME and APP_PASSWORD to "
            "protect the portal: anyone who can reach it can add targets and open "
            "connections to any host the container can reach."
        )
    yield


app = FastAPI(title="noVNC LAN Gateway", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/novnc", StaticFiles(directory=settings.novnc_root, html=True, check_dir=False), name="novnc")


def public_target(target: TargetRecord) -> Target:
    return Target(
        id=target.id,
        name=target.name,
        host=target.host,
        port=target.port,
        description=target.description,
        created_at=target.created_at,
        updated_at=target.updated_at,
        has_password=bool(target.password),
    )


def verify_basic_auth(header_value: str | None) -> bool:
    if not settings.auth_enabled:
        return True
    if not header_value:
        return False
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(username, settings.app_username) and secrets.compare_digest(
        password,
        settings.app_password,
    )


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if request.url.path == "/health" or verify_basic_auth(request.headers.get("authorization")):
        return await call_next(request)
    return Response(
        "Authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="noVNC LAN Gateway"'},
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", response_model=AppConfig)
async def app_config() -> AppConfig:
    # Lets the frontend warn when the portal is exposed without authentication.
    return AppConfig(auth_enabled=settings.auth_enabled)


@app.get("/api/targets", response_model=list[Target])
async def list_targets() -> list[Target]:
    return [public_target(target) for target in await store.list_targets()]


@app.post("/api/targets", response_model=Target, status_code=201)
async def create_target(payload: TargetCreate) -> Target:
    return public_target(await store.create_target(payload))


@app.put("/api/targets/{target_id}", response_model=Target)
async def update_target(target_id: str, payload: TargetUpdate) -> Target:
    target = await store.update_target(target_id, payload)
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return public_target(target)


@app.delete("/api/targets/{target_id}", status_code=204)
async def delete_target(target_id: str) -> Response:
    deleted = await store.delete_target(target_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Target not found")
    return Response(status_code=204)


@app.get("/api/targets/{target_id}/probe", response_model=ProbeResult)
async def probe_target(target_id: str) -> ProbeResult:
    target = await store.get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port),
            timeout=3,
        )
    # TimeoutError is an OSError subclass on 3.11+, so it must be caught first.
    except asyncio.TimeoutError:
        return ProbeResult(ok=False, message="Connection timed out")
    except OSError as exc:
        return ProbeResult(ok=False, message=str(exc))
    writer.close()
    with suppress(Exception):
        await writer.wait_closed()
    return ProbeResult(ok=True, message=f"Connected to {target.host}:{target.port}")


@app.get("/viewer/{target_id}", include_in_schema=False)
async def viewer(target_id: str) -> RedirectResponse:
    target = await store.get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    query = urlencode(
        {
            "autoconnect": "1",
            "resize": "scale",
            "path": f"/api/vnc/{target.id}",
        },
        quote_via=quote,
    )
    fragment = urlencode({"password": target.password}, quote_via=quote) if target.password else ""
    location = f"/novnc/vnc.html?{query}"
    if fragment:
        location = f"{location}#{fragment}"
    return RedirectResponse(
        url=location,
        status_code=307,
    )


@app.websocket("/api/vnc/{target_id}")
async def vnc_proxy(websocket: WebSocket, target_id: str) -> None:
    if not verify_basic_auth(websocket.headers.get("authorization")):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    target = await store.get_target(target_id)
    if target is None:
        await websocket.close(code=1008, reason="Unknown target")
        return

    try:
        reader, writer = await asyncio.open_connection(target.host, target.port)
    except OSError:
        await websocket.close(code=1011, reason="Could not connect to target")
        return

    async def websocket_to_tcp() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            payload = message.get("bytes")
            if payload is None and message.get("text") is not None:
                payload = message["text"].encode("latin-1")
            if not payload:
                continue
            writer.write(payload)
            await writer.drain()

    async def tcp_to_websocket() -> None:
        while True:
            payload = await reader.read(65536)
            if not payload:
                break
            await websocket.send_bytes(payload)

    tasks = {
        asyncio.create_task(websocket_to_tcp()),
        asyncio.create_task(tcp_to_websocket()),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
        if websocket.application_state != WebSocketState.DISCONNECTED:
            with suppress(Exception):
                await websocket.close()
