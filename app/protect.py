from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Optional

import httpx

from .camera_image import product_image_url
from .logs import log
from .settings import ROOT, Settings
from .thumbs import NO_INTEGRATION_KEY, is_jpeg, mock_jpeg, thumb_mtime, write_thumb

PRIVATE = "/proxy/protect/api"
INTEGRATION = "/proxy/protect/integration/v1"
MOCK_PATH = ROOT / "data" / "mock-console.json"
# LIVE bank: wait after goto before Integration HQ snapshot (lab).
POV_SETTLE_S = 6.0


class ProtectError(Exception):
    def __init__(self, message: str, status_code: int = 502, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


class ProtectClient:
    """UniFi Protect client using Max's live-proven private console paths."""

    def __init__(
        self,
        settings: Settings,
        transport: Any = None,
        thumb_settle_s: float = POV_SETTLE_S,
        sleep: Any = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._cookies: httpx.Cookies = httpx.Cookies()
        self._csrf = ""
        self._logged_in = False
        self._mock = _load_mock()
        self.thumb_settle_s = float(thumb_settle_s)
        self._sleep = sleep or asyncio.sleep
        self._thumb_locks: dict[str, asyncio.Lock] = {}
        # Mock overlay for smartDetectSettings writes (live uses Protect PATCH).
        self._auto_tracking: dict[str, dict[str, Any]] = {}

    def _http(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "verify": self.settings.unifi_verify_tls,
            "timeout": 20.0,
            "follow_redirects": True,
            "cookies": self._cookies,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _private_headers(self) -> dict[str, str]:
        # Private list/PATCH/start/stop: TOKEN cookie + x-csrf-token only. Never X-API-KEY.
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._csrf:
            headers["x-csrf-token"] = self._csrf
        return headers

    def _remember_auth(self, response: httpx.Response) -> None:
        self._cookies.update(response.cookies)
        csrf = response.headers.get("x-csrf-token") or response.cookies.get("csrf_token")
        if csrf:
            self._csrf = csrf

    async def login(self, client: httpx.AsyncClient) -> None:
        if not (self.settings.unifi_user and self.settings.unifi_pass):
            raise ProtectError(
                "Set UNIFI_HOST, UNIFI_USER, and UNIFI_PASS. Private Protect login is required to list and PATCH patrols.",
                400,
            )
        response = await client.post(
            f"{self.settings.base_url}/api/auth/login",
            json={"username": self.settings.unifi_user, "password": self.settings.unifi_pass},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        self._remember_auth(response)
        if response.status_code >= 400:
            raise ProtectError(
                f"UniFi OS POST /api/auth/login failed ({response.status_code}).",
                401,
                _safe_body(response),
            )
        self._logged_in = True
        log.add("OK", "UniFi OS login (TOKEN + x-csrf-token)")

    async def private(
        self,
        method: str,
        path: str,
        extra_headers: Optional[dict[str, str]] = None,
        relogin_on_403: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        if not self.settings.unifi_host:
            raise ProtectError("UNIFI_HOST is not set.", 400)
        url = f"{self.settings.base_url}{PRIVATE}{path}"
        headers = self._private_headers()
        if extra_headers:
            headers.update(extra_headers)
        async with self._http() as client:
            if not self._logged_in:
                await self.login(client)
            response = await client.request(method, url, headers=headers, **kwargs)
            self._remember_auth(response)
            if response.status_code == 401 or (response.status_code == 403 and relogin_on_403):
                self._logged_in = False
                await self.login(client)
                headers = self._private_headers()
                if extra_headers:
                    headers.update(extra_headers)
                response = await client.request(method, url, headers=headers, **kwargs)
                self._remember_auth(response)
        return response

    async def integration(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self.settings.unifi_api_key:
            raise ProtectError("Integration fallback needs UNIFI_API_KEY.", 400)
        url = f"{self.settings.base_url}{INTEGRATION}{path}"
        headers = {"Accept": "application/json", "X-API-KEY": self.settings.unifi_api_key}
        async with self._http() as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def integration_snapshot(self, camera_id: str) -> bytes:
        """LIVE POV: Integration HQ snapshot. Private /api/cameras/{id}/snapshot is stuck after goto."""
        if not self.settings.unifi_api_key:
            raise ProtectError(NO_INTEGRATION_KEY, 400)
        url = f"{self.settings.base_url}{INTEGRATION}/cameras/{camera_id}/snapshot"
        headers = {"Accept": "image/jpeg", "X-API-KEY": self.settings.unifi_api_key}
        async with self._http() as client:
            response = await client.get(
                url,
                headers=headers,
                params={"highQuality": "true"},
                timeout=30.0,
            )
        if response.status_code >= 400 or not is_jpeg(response.content):
            raise ProtectError(
                f"GET /proxy/protect/integration/v1/cameras/{camera_id}/snapshot failed ({response.status_code}).",
                502,
                _safe_body(response) if response.headers.get("content-type", "").startswith("application/json") else None,
            )
        return bytes(response.content)

    async def fetch_stock_snapshot(self, camera_id: str, slot: int, snapshot_hash: str = "") -> bytes:
        """Default POV icon: private GET .../ptz/snapshot/{slot}. No goto. No Integration key."""
        if not self.live:
            return mock_jpeg(f"stock:{camera_id}:{int(slot)}")
        path = f"/cameras/{camera_id}/ptz/snapshot/{int(slot)}"
        kwargs: dict[str, Any] = {}
        if snapshot_hash and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", snapshot_hash):
            kwargs["params"] = {"hash": snapshot_hash}
        response = await self.private(
            "GET",
            path,
            extra_headers={"Accept": "image/jpeg"},
            **kwargs,
        )
        if response.status_code >= 400 or not is_jpeg(response.content):
            raise ProtectError(
                f"GET /proxy/protect/api/cameras/{camera_id}/ptz/snapshot/{int(slot)} failed ({response.status_code}).",
                404 if response.status_code == 404 else 502,
            )
        return bytes(response.content)

    def _thumb_lock(self, camera_id: str) -> asyncio.Lock:
        lock = self._thumb_locks.get(camera_id)
        if lock is None:
            lock = asyncio.Lock()
            self._thumb_locks[camera_id] = lock
        return lock

    async def goto_preset(self, camera_id: str, slot: int) -> httpx.Response:
        """Private session goto. No body. Do not use Integration for the POV bank."""
        return await self.private("POST", f"/cameras/{camera_id}/ptz/goto/{int(slot)}")

    async def iter_refresh_preset_thumbs(self, camera_id: str, slots: list[int]) -> AsyncIterator[dict[str, Any]]:
        """Yield after each slot is cached so the UI can paint thumbs one pose at a time.

        Sequence per slot: goto → settle ~6s → Integration HQ → write cache → yield.
        Stop once at the start. Never goto all slots then dump images.
        """
        unique_slots: list[int] = []
        seen: set[int] = set()
        for raw in slots:
            try:
                slot = int(raw)
            except (TypeError, ValueError):
                continue
            if slot < 0 or slot in seen:
                continue
            seen.add(slot)
            unique_slots.append(slot)

        async with self._thumb_lock(camera_id):
            results: list[dict[str, Any]] = []
            if not self.live:
                for slot in unique_slots:
                    yield {"event": "capturing", "slot": slot, "cameraId": camera_id}
                    write_thumb(camera_id, slot, mock_jpeg(f"{camera_id}:{slot}"))
                    item = {
                        "event": "slot",
                        "slot": slot,
                        "ok": True,
                        "thumbMtime": thumb_mtime(camera_id, slot),
                    }
                    results.append({"slot": slot, "ok": True})
                    log.add("WRN", f"Mock POV icon cached for {camera_id} slot {slot}")
                    yield item
                yield {
                    "event": "done",
                    "ok": True,
                    "cameraId": camera_id,
                    "movedCamera": False,
                    "mode": "mock",
                    "slots": results,
                }
                return

            if not self.settings.unifi_api_key:
                raise ProtectError(NO_INTEGRATION_KEY, 400)

            if not unique_slots:
                yield {
                    "event": "done",
                    "ok": True,
                    "cameraId": camera_id,
                    "movedCamera": False,
                    "mode": "live",
                    "slots": results,
                }
                return

            stop_res = await self.private("POST", f"/cameras/{camera_id}/ptz/patrol/stop", json={})
            if stop_res.status_code >= 400:
                log.add(
                    "WRN",
                    f"Patrol stop before POV bank returned {stop_res.status_code}; continuing",
                )

            for slot in unique_slots:
                yield {"event": "capturing", "slot": slot, "cameraId": camera_id}
                goto_res = await self.goto_preset(camera_id, slot)
                if goto_res.status_code == 404:
                    item = {
                        "event": "slot",
                        "slot": slot,
                        "ok": False,
                        "skipped": True,
                        "error": f"Preset slot {slot} is missing.",
                    }
                    results.append({"slot": slot, "ok": False, "skipped": True, "error": item["error"]})
                    log.add("WRN", f"POV skip {camera_id} slot {slot} (missing)")
                    yield item
                    continue
                if goto_res.status_code >= 400:
                    item = {
                        "event": "slot",
                        "slot": slot,
                        "ok": False,
                        "error": f"PTZ goto slot {slot} failed ({goto_res.status_code}).",
                    }
                    results.append({"slot": slot, "ok": False, "error": item["error"]})
                    log.add("ERR", f"POV goto failed {camera_id} slot {slot} ({goto_res.status_code})")
                    yield item
                    continue
                if self.thumb_settle_s > 0:
                    await self._sleep(self.thumb_settle_s)
                try:
                    jpeg = await self.integration_snapshot(camera_id)
                except ProtectError as exc:
                    item = {"event": "slot", "slot": slot, "ok": False, "error": exc.message}
                    results.append({"slot": slot, "ok": False, "error": exc.message})
                    log.add("ERR", f"POV snapshot failed {camera_id} slot {slot}: {exc.message}")
                    yield item
                    continue
                write_thumb(camera_id, slot, jpeg)
                item = {
                    "event": "slot",
                    "slot": slot,
                    "ok": True,
                    "thumbMtime": thumb_mtime(camera_id, slot),
                }
                results.append({"slot": slot, "ok": True})
                log.add("OK", f"Cached POV icon {camera_id} slot {slot}")
                yield item

            yield {
                "event": "done",
                "ok": any(row.get("ok") for row in results) or not results,
                "cameraId": camera_id,
                "movedCamera": True,
                "mode": "live",
                "slots": results,
            }

    async def refresh_preset_thumbs(self, camera_id: str, slots: list[int]) -> dict[str, Any]:
        done: dict[str, Any] = {}
        async for event in self.iter_refresh_preset_thumbs(camera_id, slots):
            if event.get("event") == "done":
                done = {key: value for key, value in event.items() if key != "event"}
        return done

    @property
    def live(self) -> bool:
        return bool(self.settings.unifi_host and self.settings.unifi_user and self.settings.unifi_pass)

    async def list_cameras(self) -> list[dict[str, Any]]:
        if not self.live:
            log.add("WRN", "Protect credentials missing — serving mock cameras")
            cameras = []
            for item in self._mock.get("cameras", []):
                raw = dict(item)
                overlay = self._auto_tracking.get(str(raw.get("id")))
                if overlay:
                    sds = dict(raw.get("smartDetectSettings") or {})
                    sds.update(auto_tracking_patch_body(overlay)["smartDetectSettings"])
                    raw["smartDetectSettings"] = sds
                cameras.append(normalize_camera(raw))
            return cameras

        response = await self.private("GET", "/cameras")
        if response.status_code >= 400:
            raise ProtectError(
                f"GET /proxy/protect/api/cameras failed ({response.status_code}).",
                502,
                _safe_body(response),
            )
        cameras = []
        for item in _as_list(response.json()):
            flags = item.get("featureFlags") if isinstance(item.get("featureFlags"), dict) else {}
            if flags.get("isPtz") is True:
                cameras.append(normalize_camera(item))
        log.add("INF", f"Loaded {len(cameras)} PTZ cameras (featureFlags.isPtz)")
        return cameras

    async def patrol_count(self, camera_id: str) -> int:
        """Patrol list length only. Live: one GET .../ptz/patrol. No presets, no active."""
        if not self.live:
            camera = _mock_camera(self._mock, camera_id)
            if not camera:
                raise ProtectError(f"Mock camera {camera_id} not found.", 404)
            count = nested_patrol_count(camera)
            return 0 if count is None else count

        patrol_res = await self.private("GET", f"/cameras/{camera_id}/ptz/patrol")
        if patrol_res.status_code >= 400:
            raise ProtectError(
                f"GET /proxy/protect/api/cameras/{camera_id}/ptz/patrol failed ({patrol_res.status_code}).",
                502,
                _safe_body(patrol_res),
            )
        return len(_as_list(patrol_res.json()))

    async def _raw_camera(self, camera_id: str) -> dict[str, Any]:
        if not self.live:
            camera = _mock_camera(self._mock, camera_id)
            if not camera:
                raise ProtectError(f"Mock camera {camera_id} not found.", 404)
            overlay = self._auto_tracking.get(str(camera_id))
            if overlay:
                camera = dict(camera)
                sds = dict(camera.get("smartDetectSettings") or {})
                sds.update(auto_tracking_patch_body(overlay)["smartDetectSettings"])
                camera["smartDetectSettings"] = sds
            return camera

        # LIVE: GET /proxy/protect/api/cameras/{id} → smartDetectSettings
        # (autoTrackingObjectTypes, autoTrackingWithZoom). Ignore objectTypes.
        response = await self.private("GET", f"/cameras/{camera_id}")
        if response.status_code == 404:
            listed = await self.private("GET", "/cameras")
            if listed.status_code >= 400:
                raise ProtectError(
                    f"GET /proxy/protect/api/cameras failed ({listed.status_code}).",
                    502,
                    _safe_body(listed),
                )
            for item in _as_list(listed.json()):
                if str(item.get("id")) == str(camera_id):
                    return item if isinstance(item, dict) else {}
            raise ProtectError(f"Camera {camera_id} not found.", 404)
        if response.status_code >= 400:
            raise ProtectError(
                f"GET /proxy/protect/api/cameras/{camera_id} failed ({response.status_code}).",
                502,
                _safe_body(response),
            )
        body = response.json()
        return body if isinstance(body, dict) else {}

    async def get_camera_settings(self, camera_id: str) -> dict[str, Any]:
        raw = await self._raw_camera(camera_id)
        tracking = normalize_auto_tracking(raw)
        overlay = self._auto_tracking.get(str(camera_id))
        if overlay and not self.live:
            tracking = {**tracking, **overlay}
        return {
            "cameraId": str(camera_id),
            "autoTracking": tracking,
            "mode": "live" if self.live else "mock",
        }

    async def patch_camera_settings(self, camera_id: str, tracking: dict[str, Any]) -> dict[str, Any]:
        raw = await self._raw_camera(camera_id)
        current = normalize_auto_tracking(raw)
        next_tracking = {
            "person": bool(tracking["person"]) if "person" in tracking else current["person"],
            "vehicle": bool(tracking["vehicle"]) if "vehicle" in tracking else current["vehicle"],
            "zoomWhileTrack": bool(tracking["zoomWhileTrack"]) if "zoomWhileTrack" in tracking else current["zoomWhileTrack"],
        }
        payload = auto_tracking_patch_body(next_tracking)

        if not self.live:
            self._auto_tracking[str(camera_id)] = next_tracking
            camera = _mock_camera(self._mock, camera_id)
            if camera is not None:
                sds = dict(camera.get("smartDetectSettings") or {})
                sds.update(payload["smartDetectSettings"])
                camera["smartDetectSettings"] = sds
            log.add("WRN", f"Mock camera settings {camera_id}: {next_tracking}")
            return {"cameraId": str(camera_id), "autoTracking": next_tracking, "mode": "mock"}

        # LIVE (lab): PATCH /proxy/protect/api/cameras/{id}
        # Body is only autoTrackingObjectTypes + autoTrackingWithZoom. Do not send objectTypes
        # (those are alert detections, not auto-track).
        response = await self.private("PATCH", f"/cameras/{camera_id}", json=payload)
        if response.status_code >= 400:
            raise ProtectError(
                f"PATCH /proxy/protect/api/cameras/{camera_id} failed ({response.status_code}).",
                502,
                _safe_body(response),
            )
        log.add("OK", f"Patched camera auto-tracking {camera_id}: {next_tracking}")
        return {"cameraId": str(camera_id), "autoTracking": next_tracking, "mode": "live"}

    async def set_camera_ssh(self, camera_id: str, enabled: bool) -> dict[str, Any]:
        """PATCH /proxy/protect/api/cameras/{id} {"isSshEnabled": bool}. Body never logged."""
        response = await self.private("PATCH", f"/cameras/{camera_id}", json={"isSshEnabled": bool(enabled)})
        echoed = None
        try:
            data = response.json()
            if isinstance(data, dict) and "isSshEnabled" in data:
                echoed = bool(data["isSshEnabled"])
        except Exception:
            echoed = None
        ok = 200 <= response.status_code < 300 and echoed in (None, bool(enabled))
        log.add("OK" if ok else "ERR", f"Camera {camera_id} SSH {'on' if enabled else 'off'} ({response.status_code})")
        return {"ok": ok, "status": response.status_code, "isSshEnabled": echoed}

    async def camera_recovery_code(self, camera_id: str) -> str:
        """GET /proxy/protect/api/devices/password/camera/{id} -> {"password": ...}.

        Same call Protect's own page makes for Manual Recovery. Uses the session the app
        already holds. A 403 does NOT trigger another login (avoids UniFi OS 429s).
        The value is returned to the caller only; never logged or stored.
        """
        response = await self.private("GET", f"/devices/password/camera/{camera_id}", relogin_on_403=False)
        if response.status_code == 403:
            log.add("ERR", f"Camera {camera_id} recovery code: 403")
            raise ProtectError("PatrolCraft account lacks permission to read the camera recovery code.", 403)
        if response.status_code >= 400:
            log.add("ERR", f"Camera {camera_id} recovery code: {response.status_code}")
            raise ProtectError(f"Protect did not return the camera recovery code ({response.status_code}).", 502)
        try:
            data = response.json()
        except Exception:
            data = None
        code = data.get("password") if isinstance(data, dict) else None
        if not isinstance(code, str) or not code.strip():
            raise ProtectError("Protect returned no recovery code for this camera.", 502)
        log.add("OK", f"Camera {camera_id} recovery code read (200)")
        return code.strip()

    async def list_patrols(self, camera_id: str) -> list[dict[str, Any]]:
        if not self.live:
            camera = _mock_camera(self._mock, camera_id)
            if not camera:
                raise ProtectError(f"Mock camera {camera_id} not found.", 404)
            presets = {int(p["slot"]): normalize_preset(p, camera_id) for p in camera.get("presets", [])}
            # Mock file nests presets under each patrol already
            return [normalize_patrol(p, presets, camera_id) for p in camera.get("patrols", [])]

        patrol_res = await self.private("GET", f"/cameras/{camera_id}/ptz/patrol")
        if patrol_res.status_code >= 400:
            raise ProtectError(
                f"GET /proxy/protect/api/cameras/{camera_id}/ptz/patrol failed ({patrol_res.status_code}).",
                502,
                _safe_body(patrol_res),
            )
        preset_res = await self.private("GET", f"/cameras/{camera_id}/ptz/preset")
        presets_raw = _as_list(preset_res.json()) if preset_res.status_code < 400 else []
        presets_by_slot = {_preset_slot(item): normalize_preset(item, camera_id) for item in presets_raw}

        active_slot = None
        active_res = await self.private("GET", f"/cameras/{camera_id}/ptz/patrol/active")
        if active_res.status_code < 400:
            active_body = active_res.json()
            if isinstance(active_body, dict) and active_body.get("slot") is not None:
                active_slot = active_body.get("slot")

        patrols = []
        for item in _as_list(patrol_res.json()):
            patrol = normalize_patrol(item, presets_by_slot, camera_id)
            patrol["active"] = active_slot is not None and int(patrol["slot"]) == int(active_slot)
            patrols.append(patrol)
        log.add("INF", f"Camera {camera_id}: {len(patrols)} patrols")
        return patrols

    async def patch_patrol(self, camera_id: str, patrol_id: str, body: dict[str, Any]) -> dict[str, Any]:
        patrols = await self.list_patrols(camera_id)
        patrol = _find_patrol(patrols, patrol_id)
        if not patrol:
            raise ProtectError(f"Patrol {patrol_id} not found.", 404)

        dwell = body.get("presetDurationSeconds", patrol["presetDurationSeconds"])
        speed = body.get("presetMovementSpeed", patrol["presetMovementSpeed"])
        if dwell is None or int(dwell) < 0 or int(dwell) > 127:
            raise ProtectError("Dwell (presetDurationSeconds) must be 0–127.", 400)
        if speed is None or int(speed) < 0 or int(speed) > 1000:
            raise ProtectError("Movement speed (presetMovementSpeed) must be 0–1000 (usable ~200–1000).", 400)

        preset_slots = [int(p["slot"]) for p in patrol.get("presets") or []]
        payload = {
            "presets": preset_slots,
            "presetDurationSeconds": int(dwell),
            "presetMovementSpeed": int(speed),
        }

        if not self.live:
            patrol["presetDurationSeconds"] = payload["presetDurationSeconds"]
            patrol["presetMovementSpeed"] = payload["presetMovementSpeed"]
            log.add("WRN", f"Mock PATCH slot {patrol['slot']}: {payload}")
            return payload

        slot = int(patrol["slot"])
        response = await self.private("PATCH", f"/cameras/{camera_id}/ptz/patrol/{slot}", json=payload)
        if response.status_code >= 400:
            raise ProtectError(
                f"PATCH /proxy/protect/api/cameras/{camera_id}/ptz/patrol/{slot} failed ({response.status_code}).",
                502,
                _safe_body(response),
            )
        log.add("OK", f"Patched patrol slot {slot}: {payload}")
        return payload

    async def start_patrol(self, camera_id: str, patrol_id: str) -> dict[str, Any]:
        slot = await self._slot(camera_id, patrol_id)
        if not self.live:
            log.add("WRN", f"Mock start camera {camera_id} slot {slot}")
            return {"ok": True, "slot": slot, "mode": "mock"}

        path = f"/cameras/{camera_id}/ptz/patrol/start/{slot}"
        response = await self.private("POST", path, json={})
        if response.status_code >= 400 and self.settings.unifi_api_key:
            log.add("WRN", "Private start failed; trying Integration fallback")
            response = await self.integration("POST", f"/cameras/{camera_id}/ptz/patrol/start/{slot}")
        if response.status_code >= 400:
            raise ProtectError(
                f"POST ptz/patrol/start/{slot} failed ({response.status_code}).",
                502,
                _safe_body(response),
            )
        log.add("OK", f"Started patrol slot {slot} on {camera_id}")
        return {"ok": True, "slot": slot}

    async def stop_patrol(self, camera_id: str, patrol_id: str) -> dict[str, Any]:
        if not self.live:
            log.add("WRN", f"Mock stop camera {camera_id}")
            return {"ok": True, "mode": "mock"}

        response = await self.private("POST", f"/cameras/{camera_id}/ptz/patrol/stop", json={})
        if response.status_code >= 400 and self.settings.unifi_api_key:
            log.add("WRN", "Private stop failed; trying Integration fallback")
            response = await self.integration("POST", f"/cameras/{camera_id}/ptz/patrol/stop")
        if response.status_code >= 400:
            raise ProtectError(
                f"POST ptz/patrol/stop failed ({response.status_code}).",
                502,
                _safe_body(response),
            )
        log.add("OK", f"Stopped patrol on {camera_id}")
        return {"ok": True}

    async def _slot(self, camera_id: str, patrol_id: str) -> int:
        if patrol_id.isdigit():
            return int(patrol_id)
        patrols = await self.list_patrols(camera_id)
        patrol = _find_patrol(patrols, patrol_id)
        if not patrol:
            raise ProtectError(f"Patrol {patrol_id} not found on camera {camera_id}.", 404)
        return int(patrol["slot"])


def _load_mock() -> dict[str, Any]:
    if not MOCK_PATH.exists():
        return {"cameras": []}
    return json.loads(MOCK_PATH.read_text())


def _mock_camera(mock: dict[str, Any], camera_id: str) -> Optional[dict[str, Any]]:
    for camera in mock.get("cameras", []):
        if str(camera.get("id")) == str(camera_id):
            return camera
    return None


def _find_patrol(patrols: list[dict[str, Any]], patrol_id: str) -> Optional[dict[str, Any]]:
    for patrol in patrols:
        if str(patrol.get("id")) == str(patrol_id) or str(patrol.get("slot")) == str(patrol_id):
            return patrol
    return None


def _safe_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        text = response.text
        return text[:400] if text else None


def _as_list(payload: Any) -> list:
    return payload if isinstance(payload, list) else []


def _as_host(value: Any) -> str:
    if isinstance(value, dict):
        return _as_host(value.get("host") or value.get("ip") or value.get("address") or "")
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "undefined"}:
        return ""
    return text


def camera_ip(item: dict[str, Any]) -> str:
    """Protect camera IP: host, then connectionHost, then ip, then last-seen connection."""
    for key in ("host", "connectionHost", "ip"):
        text = _as_host(item.get(key))
        if text:
            return text
    for nested_key in ("connection", "lastConnection", "network", "lastSeenConnection"):
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            text = camera_ip(nested)
            if text:
                return text
    return ""


def format_mac(mac: Any) -> str:
    """Protect stores MAC as 12 hex chars; show AA:BB:CC:DD:EE:FF. Empty stays empty."""
    raw = str(mac or "").strip()
    if not raw:
        return ""
    compact = raw.replace(":", "").replace("-", "").replace(".", "").upper()
    if len(compact) == 12 and all(ch in "0123456789ABCDEF" for ch in compact):
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return raw


def normalize_camera(item: dict[str, Any]) -> dict[str, Any]:
    type_name = item.get("type") or item.get("marketName") or item.get("model") or "Camera"
    flags = item.get("featureFlags") if isinstance(item.get("featureFlags"), dict) else {}
    is_ptz = flags.get("isPtz") is True or item.get("isPtz") is True
    host = camera_ip(item)
    camera = {
        "id": str(item.get("id") or item.get("mac") or ""),
        "name": item.get("name") or "Untitled",
        "type": type_name,
        "marketName": item.get("marketName") or "",
        "platform": item.get("platform") or "",
        "state": item.get("state") or ("CONNECTED" if item.get("isConnected") else "UNKNOWN"),
        "isPtz": is_ptz,
        "mac": format_mac(item.get("mac")),
        "host": host,
        "ip": host,
        "connectionHost": _as_host(item.get("connectionHost")),
        "firmwareVersion": item.get("firmwareVersion") or item.get("firmware") or "",
        "housingColor": item.get("housingColor") or item.get("color") or "",
        "imageOverride": item.get("imageOverride") or "",
        "autoTracking": normalize_auto_tracking(item),
    }
    camera["productImage"] = product_image_url(camera)
    count = nested_patrol_count(item)
    if count is not None:
        camera["patrolCount"] = count
    return camera


def nested_patrol_count(item: dict[str, Any]) -> Optional[int]:
    """Count from a nested patrols list (mock console) or an explicit patrolCount."""
    raw = item.get("patrolCount")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(0, raw)
    patrols = item.get("patrols")
    if isinstance(patrols, list):
        return len(patrols)
    return None


def normalize_auto_tracking(item: dict[str, Any]) -> dict[str, bool]:
    """LIVE: smartDetectSettings.autoTrackingObjectTypes + autoTrackingWithZoom.

    Ignore smartDetectSettings.objectTypes (alert detections, not auto-track).
    Person ON = \"person\" in autoTrackingObjectTypes. Vehicle ON = \"vehicle\".
    Both off = []. Zoom-while-track = autoTrackingWithZoom.
    """
    sds = item.get("smartDetectSettings") if isinstance(item.get("smartDetectSettings"), dict) else {}
    types = [str(value).lower() for value in (sds.get("autoTrackingObjectTypes") or [])]
    nested = item.get("autoTracking") if isinstance(item.get("autoTracking"), dict) else {}
    if "autoTrackingObjectTypes" in sds or types:
        person = "person" in types
        vehicle = "vehicle" in types
    else:
        person = bool(nested.get("person"))
        vehicle = bool(nested.get("vehicle"))
    if "autoTrackingWithZoom" in sds:
        zoom = bool(sds.get("autoTrackingWithZoom"))
    else:
        zoom = bool(nested.get("zoomWhileTrack"))
    return {"person": person, "vehicle": vehicle, "zoomWhileTrack": zoom}


def auto_tracking_patch_body(tracking: dict[str, Any]) -> dict[str, Any]:
    """LIVE PATCH body. Both off → autoTrackingObjectTypes []. Never send objectTypes."""
    types: list[str] = []
    if tracking.get("person"):
        types.append("person")
    if tracking.get("vehicle"):
        types.append("vehicle")
    return {
        "smartDetectSettings": {
            "autoTrackingObjectTypes": types,
            "autoTrackingWithZoom": bool(tracking.get("zoomWhileTrack")),
        }
    }


def _preset_slot(item: dict[str, Any]) -> int:
    try:
        return int(item.get("slot") if item.get("slot") is not None else 0)
    except (TypeError, ValueError):
        return 0


def stock_snapshot_path(camera_id: str, slot: int, snapshot: Any = "") -> str:
    """Relative Protect preset snapshot path, under /proxy/protect/api."""
    raw = str(snapshot or "").strip()
    if raw.startswith("/proxy/protect/api"):
        raw = raw[len("/proxy/protect/api") :] or ""
    if "://" in raw or raw.startswith("//"):
        raw = ""
    if raw.startswith("/cameras/") and "/ptz/snapshot/" in raw:
        return raw
    return f"/cameras/{camera_id}/ptz/snapshot/{int(slot)}"


def normalize_preset(item: dict[str, Any], camera_id: str = "") -> dict[str, Any]:
    ptz = item.get("ptz") if isinstance(item.get("ptz"), dict) else {}
    slot = _preset_slot(item)
    snapshot = str(item.get("snapshot") or "")
    if camera_id:
        snapshot = stock_snapshot_path(camera_id, slot, snapshot)
    return {
        "id": str(item.get("id") or item.get("slot") or ""),
        "slot": slot,
        "name": item.get("name") or f"Preset {slot}",
        "pan": ptz.get("pan", item.get("pan")),
        "tilt": ptz.get("tilt", item.get("tilt")),
        "zoom": ptz.get("zoom", item.get("zoom")),
        "focus": ptz.get("focus", item.get("focus")),
        "snapshot": snapshot,
    }


def normalize_patrol(
    item: dict[str, Any],
    presets_by_slot: dict[int, dict[str, Any]],
    camera_id: str = "",
) -> dict[str, Any]:
    # Live: patrol.presets is ordered preset slot numbers. Mock nests preset objects.
    preset_refs = item.get("presets") or []
    presets: list[dict[str, Any]] = []
    if preset_refs and all(isinstance(ref, dict) for ref in preset_refs):
        presets = [normalize_preset(ref, camera_id) for ref in preset_refs]
    else:
        for ref in preset_refs:
            try:
                slot = int(ref)
            except (TypeError, ValueError):
                continue
            fallback = {
                "id": str(slot),
                "slot": slot,
                "name": f"Slot {slot}",
                "pan": None,
                "tilt": None,
                "zoom": None,
                "focus": None,
                "snapshot": stock_snapshot_path(camera_id, slot) if camera_id else "",
            }
            presets.append(presets_by_slot.get(slot, fallback))
    return {
        "id": str(item.get("id") or item.get("slot") or ""),
        "name": item.get("name") or f"Patrol {item.get('slot', '')}",
        "slot": int(item.get("slot") or 0),
        "presetDurationSeconds": item.get("presetDurationSeconds", 0),
        "presetMovementSpeed": item.get("presetMovementSpeed", 400),
        "presets": presets,
        "presetSlots": [int(p["slot"]) for p in presets],
        "active": False,
    }
