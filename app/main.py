from __future__ import annotations

import hmac

import asyncio
import json
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .logs import log
from .camera_ssh import apply_points_to_camera
from .patrol_points import annotate_patrol_points, live_set_points_body, read_points, save_patrol_points
from .protect import ProtectClient, ProtectError, _find_patrol
from .settings import ROOT, Settings, settings
from .thumbs import annotate_patrols, read_thumb

VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "0.0.0"
app = FastAPI(title="PatrolCraft", version=VERSION)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret,
    same_site="lax",
    https_only=False,
    session_cookie="patrolcraft_session",
)

app.mount("/css", StaticFiles(directory=ROOT / "css"), name="css")
app.mount("/js", StaticFiles(directory=ROOT / "js"), name="js")
app.mount("/pages", StaticFiles(directory=ROOT / "pages"), name="pages")
if (ROOT / "img").exists():
    app.mount("/img", StaticFiles(directory=ROOT / "img"), name="img")
if (ROOT / "vendor" / "yestechie-ui-kit").exists():
    app.mount("/kit", StaticFiles(directory=ROOT / "vendor" / "yestechie-ui-kit"), name="kit")


def get_settings() -> Settings:
    return settings


def get_protect() -> ProtectClient:
    client = getattr(app.state, "protect", None)
    if client is None:
        client = ProtectClient(settings)
        app.state.protect = client
    return client


def require_user(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return str(user)


class LoginBody(BaseModel):
    username: str
    password: str


class ConfigBody(BaseModel):
    host: str = ""
    port: int = 443
    site: str = "default"
    apiKey: str = ""
    username: str = ""
    password: str = ""
    verifyTls: bool = False
    deviceName: str = "PatrolCraft"
    timezone: str = "America/Los_Angeles"


class PatrolPatchBody(BaseModel):
    presetDurationSeconds: Optional[int] = Field(default=None, ge=0, le=127)
    presetMovementSpeed: Optional[int] = Field(default=None, ge=0, le=1000)


class AutoTrackingBody(BaseModel):
    person: Optional[bool] = None
    vehicle: Optional[bool] = None
    zoomWhileTrack: Optional[bool] = None


class CameraSettingsBody(BaseModel):
    autoTracking: AutoTrackingBody


class PatrolPointBody(BaseModel):
    slot: int
    presetDurationSeconds: Optional[int] = Field(default=None, ge=0, le=127)
    presetMovementSpeed: Optional[int] = Field(default=None, ge=0, le=1000)
    pan: Optional[int] = None
    tilt: Optional[int] = None
    zoom: Optional[int] = None
    focus: Optional[int] = None


class PatrolPointsBody(BaseModel):
    points: list[PatrolPointBody]


@app.exception_handler(ProtectError)
async def protect_error_handler(_request: Request, exc: ProtectError) -> JSONResponse:
    log.add("ERR", exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "detail": exc.detail},
    )


@app.get("/")
async def root(request: Request) -> RedirectResponse:
    if request.session.get("user"):
        return RedirectResponse("/pages/app.html#/patrols", status_code=302)
    return RedirectResponse("/pages/login.html", status_code=302)


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "app": "PatrolCraft",
        "version": VERSION,
        "protect": {
            "configured": settings.configured,
            "mode": "live" if settings.configured else "mock",
            "host": settings.unifi_host or None,
            "apiKeySet": bool(settings.unifi_api_key),
        },
    }


@app.get("/api/version")
async def version() -> dict:
    return {"ok": True, "app": "PatrolCraft", "version": VERSION}


@app.get("/logout")
async def logout_get(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/pages/login.html", status_code=302)


@app.post("/api/login")
async def login(body: LoginBody, request: Request) -> dict:
    if not settings.login_ready:
        log.add("WRN", "Sign-in refused: APP_USER / APP_PASS are not set in .env")
        raise HTTPException(status_code=503, detail="Login is not set up. Re-run the installer.")
    user_ok = hmac.compare_digest(body.username.encode(), settings.app_user.encode())
    pass_ok = hmac.compare_digest(body.password.encode(), settings.app_pass.encode())
    if not (user_ok and pass_ok):
        log.add("WRN", f"Failed login for {body.username}")
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user"] = body.username
    log.add("OK", f"Signed in as {body.username}")
    return {"ok": True, "username": body.username}


@app.post("/api/logout")
async def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(user: str = Depends(require_user)) -> dict:
    return {"username": user}


@app.get("/api/config")
async def get_config(_user: str = Depends(require_user)) -> dict:
    return settings.public_dict()


@app.put("/api/config")
async def put_config(body: ConfigBody, _user: str = Depends(require_user)) -> dict:
    settings.apply_form(body.model_dump())
    app.state.protect = ProtectClient(settings)
    log.add("OK", "Saved UniFi connection settings (local file, not committed)")
    return settings.public_dict()


async def _with_patrol_counts(protect: Any, cameras: list) -> list:
    """First-paint tile counts: attach patrolCount without selecting each camera."""

    async def one(camera: dict) -> None:
        if isinstance(camera.get("patrolCount"), int) and not isinstance(camera.get("patrolCount"), bool):
            return
        camera_id = str(camera.get("id") or "")
        if not camera_id:
            camera["patrolCount"] = 0
            return
        try:
            count_fn = getattr(protect, "patrol_count", None)
            if callable(count_fn):
                camera["patrolCount"] = int(await count_fn(camera_id))
            else:
                patrols = await protect.list_patrols(camera_id)
                camera["patrolCount"] = len(patrols or [])
        except Exception:
            camera["patrolCount"] = 0

    if cameras:
        await asyncio.gather(*(one(camera) for camera in cameras))
    return cameras


@app.get("/api/cameras")
async def cameras(_user: str = Depends(require_user), protect: ProtectClient = Depends(get_protect)) -> dict:
    cameras = await _with_patrol_counts(protect, await protect.list_cameras())
    return {"cameras": cameras, "mode": "live" if settings.configured else "mock", "apiKeySet": bool(settings.unifi_api_key)}


@app.get("/api/cameras/{camera_id}/patrols")
async def camera_patrols(
    camera_id: str,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> dict:
    patrols = await protect.list_patrols(camera_id)
    patrols = annotate_patrols(camera_id, patrols)
    return {"cameraId": camera_id, "patrols": annotate_patrol_points(camera_id, patrols)}


@app.patch("/api/cameras/{camera_id}/patrols/{patrol_id}")
async def patch_patrol(
    camera_id: str,
    patrol_id: str,
    body: PatrolPatchBody,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> dict:
    return await protect.patch_patrol(camera_id, patrol_id, body.model_dump(exclude_none=True))


@app.get("/api/cameras/{camera_id}/settings")
async def get_camera_settings(
    camera_id: str,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> dict:
    return await protect.get_camera_settings(camera_id)


@app.patch("/api/cameras/{camera_id}/settings")
async def patch_camera_settings(
    camera_id: str,
    body: CameraSettingsBody,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> dict:
    return await protect.patch_camera_settings(camera_id, body.autoTracking.model_dump(exclude_none=True))


def _tour_values(patrol: dict) -> tuple[int, int]:
    try:
        tour_dwell = int(patrol.get("presetDurationSeconds") or 0)
    except (TypeError, ValueError):
        tour_dwell = 0
    try:
        tour_speed = int(patrol.get("presetMovementSpeed") or 400)
    except (TypeError, ValueError):
        tour_speed = 400
    return tour_dwell, tour_speed


@app.put("/api/cameras/{camera_id}/patrols/{patrol_id}/points")
async def put_patrol_points(
    camera_id: str,
    patrol_id: str,
    body: PatrolPointsBody,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> dict:
    patrols = await protect.list_patrols(camera_id)
    patrol = _find_patrol(patrols, patrol_id)
    if not patrol:
        raise HTTPException(status_code=404, detail=f"Patrol {patrol_id} not found.")
    tour_dwell, tour_speed = _tour_values(patrol)
    result = save_patrol_points(
        camera_id,
        int(patrol["slot"]),
        [point.model_dump() for point in body.points],
        tour_dwell=tour_dwell,
        tour_speed=tour_speed,
    )
    log.add("OK", f"Saved patrol points sidecar camera {camera_id} slot {patrol['slot']}")
    return result


@app.post("/api/cameras/{camera_id}/patrols/{patrol_id}/apply-points")
async def apply_patrol_points(
    camera_id: str,
    patrol_id: str,
    body: PatrolPointsBody,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> JSONResponse:
    """Write per-preset dwell/speed to the camera: SSH on via Protect, ubnt_ipc_cli set, SSH off."""
    patrols = await protect.list_patrols(camera_id)
    patrol = _find_patrol(patrols, patrol_id)
    if not patrol:
        raise HTTPException(status_code=404, detail=f"Patrol {patrol_id} not found.")
    form_points = [point.model_dump() for point in body.points]
    points = live_set_points_body(form_points)["points"]
    result = await apply_points_to_camera(protect, camera_id, int(patrol["slot"]), points)
    if result.get("ok"):
        # Keep what went to the camera, so Edit fields and Start use the same values.
        tour_dwell, tour_speed = _tour_values(patrol)
        saved = save_patrol_points(camera_id, int(patrol["slot"]), form_points, tour_dwell=tour_dwell, tour_speed=tour_speed)
        result["points"] = saved["points"]
        log.add("OK", f"Applied and saved per-preset points camera {camera_id} slot {patrol['slot']}")
        return JSONResponse(status_code=200, content=result)
    log.add("ERR", f"Apply failed camera {camera_id} slot {patrol['slot']}: {result.get('error')}")
    return JSONResponse(status_code=502, content=result)


@app.post("/api/cameras/{camera_id}/patrols/{patrol_id}/start")
async def start_patrol(
    camera_id: str,
    patrol_id: str,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> dict:
    """Protect start, then re-write the saved per-preset dwell/speed (Protect's start
    restores its own tour-wide values). No saved values: Protect start only."""
    started = await protect.start_patrol(camera_id, patrol_id)
    slot = int(started.get("slot", 0))
    sidecar = read_points(camera_id, slot)
    if not sidecar or not sidecar.get("points"):
        started["pointsWritten"] = False
        return started
    points = live_set_points_body(sidecar["points"])["points"]
    written = await apply_points_to_camera(protect, camera_id, slot, points)
    started["pointsWritten"] = bool(written.get("ok"))
    if written.get("mode"):
        started["pointsMode"] = written["mode"]
    if not written.get("ok"):
        started["pointsError"] = written.get("error") or "Saved dwell and speed were not written."
        log.add("ERR", f"Start camera {camera_id} slot {slot}: points not written: {started['pointsError']}")
    else:
        log.add("OK", f"Start camera {camera_id} slot {slot}: saved points written")
    return started


@app.post("/api/cameras/{camera_id}/patrols/{patrol_id}/stop")
async def stop_patrol(
    camera_id: str,
    patrol_id: str,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> dict:
    return await protect.stop_patrol(camera_id, patrol_id)


@app.get("/api/cameras/{camera_id}/presets/{slot}/thumb")
async def preset_thumb(
    camera_id: str,
    slot: int,
    snapshot_hash: Optional[str] = Query(default=None, alias="hash"),
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> Response:
    data = read_thumb(camera_id, slot)
    if not data:
        data = await protect.fetch_stock_snapshot(camera_id, slot, snapshot_hash or "")
    if not data:
        raise HTTPException(status_code=404, detail="No POV icon for this preset.")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


def _preset_slots(patrols: list, patrol_id: Optional[str] = None) -> list:
    chosen = patrols
    if patrol_id is not None:
        patrol = _find_patrol(patrols, patrol_id)
        if not patrol:
            raise HTTPException(status_code=404, detail=f"Patrol {patrol_id} not found.")
        chosen = [patrol]
    slots = []
    seen = set()
    for patrol in chosen:
        for preset in patrol.get("presets") or []:
            try:
                slot = int(preset.get("slot"))
            except (TypeError, ValueError):
                continue
            if slot in seen:
                continue
            seen.add(slot)
            slots.append(slot)
    return slots


async def _stream_refresh(protect: ProtectClient, camera_id: str, slots: list) -> StreamingResponse:
    agen = protect.iter_refresh_preset_thumbs(camera_id, slots).__aiter__()
    try:
        first = await agen.__anext__()
    except StopAsyncIteration:
        first = None

    async def gen():
        if first is not None:
            yield json.dumps(first) + "\n"
        try:
            async for event in agen:
                yield json.dumps(event) + "\n"
        except ProtectError as exc:
            yield json.dumps({"event": "error", "ok": False, "error": exc.message}) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cameras/{camera_id}/thumbs/refresh")
async def refresh_camera_thumbs(
    camera_id: str,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> StreamingResponse:
    patrols = await protect.list_patrols(camera_id)
    return await _stream_refresh(protect, camera_id, _preset_slots(patrols))


@app.post("/api/cameras/{camera_id}/patrols/{patrol_id}/thumbs/refresh")
async def refresh_patrol_thumbs(
    camera_id: str,
    patrol_id: str,
    _user: str = Depends(require_user),
    protect: ProtectClient = Depends(get_protect),
) -> StreamingResponse:
    patrols = await protect.list_patrols(camera_id)
    return await _stream_refresh(protect, camera_id, _preset_slots(patrols, patrol_id))


@app.get("/api/logs")
async def get_logs(_user: str = Depends(require_user)) -> dict:
    return {"lines": log.list()}


@app.post("/api/logs/clear")
async def clear_logs(_user: str = Depends(require_user)) -> dict:
    log.clear()
    return {"ok": True}


