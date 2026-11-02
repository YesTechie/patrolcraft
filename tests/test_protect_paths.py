from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Optional

import httpx
import pytest

from app.protect import ProtectClient, ProtectError
from app.settings import Settings
from app.thumbs import mock_jpeg, read_thumb

NVR = "10.0.0.1"
CAMERA_ID = "64f8c2a91b3e4d001a2b3c4d"
PATROL_MONGO_ID = "65b01c4e8f2a33001c9d0e11"


class FakeNVR:
    """Protect 7.2.105 private paths (lab)."""

    def __init__(self, *, fail_private_start: bool = False, fail_private_stop: bool = False) -> None:
        self.calls: list[dict] = []
        self.csrf = "csrf-live-1"
        self.fail_private_start = fail_private_start
        self.fail_private_stop = fail_private_stop
        self.patched: Optional[dict] = None
        self.last_goto_slot: Optional[int] = None
        self.goto_slots: list[int] = []
        self.camera_patch: Optional[dict] = None
        self.camera = {
            "id": CAMERA_ID,
            "name": "Yard East",
            "type": "UVC G4 PTZ",
            "state": "CONNECTED",
            "mac": "E43883123456",
            "host": "10.0.0.40",
            "featureFlags": {"isPtz": True},
            "smartDetectSettings": {
                "objectTypes": ["person", "vehicle"],
                "autoTrackingObjectTypes": ["person"],
                "autoTrackingWithZoom": False,
            },
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "body": body,
            }
        )
        path = request.url.path
        method = request.method

        if method == "POST" and path == "/api/auth/login":
            if body == {"username": "lab", "password": "labpass"}:
                return httpx.Response(
                    200,
                    json={"unique_id": "u1"},
                    headers={"x-csrf-token": self.csrf, "Set-Cookie": "TOKEN=live-token; Path=/"},
                )
            return httpx.Response(401, json={"error": "bad login"})

        if path.startswith("/proxy/protect/integration/"):
            if method == "GET" and path == f"/proxy/protect/integration/v1/cameras/{CAMERA_ID}/snapshot":
                key = {k.lower(): v for k, v in request.headers.items()}.get("x-api-key")
                if not key:
                    return httpx.Response(401, json={"error": "missing X-API-KEY"})
                if request.url.params.get("highQuality") != "true":
                    return httpx.Response(400, json={"error": "highQuality=true required"})
                slot = self.last_goto_slot if self.last_goto_slot is not None else -1
                return httpx.Response(
                    200,
                    content=mock_jpeg(f"live:{CAMERA_ID}:{slot}"),
                    headers={"content-type": "image/jpeg"},
                )
            if method == "POST" and path.endswith(f"/ptz/patrol/start/0"):
                return httpx.Response(200, json={"ok": True, "via": "integration"})
            if method == "POST" and path.endswith("/ptz/patrol/stop"):
                return httpx.Response(200, json={"ok": True, "via": "integration"})
            return httpx.Response(404, json={"error": "integration not used for this path"})

        if not self._private_ok(request, write=method in {"POST", "PATCH", "PUT", "DELETE"}):
            return httpx.Response(401, json={"error": "missing TOKEN or x-csrf-token"})

        if method == "GET" and path == "/proxy/protect/api/cameras":
            return httpx.Response(
                200,
                json=[
                    self.camera,
                    {
                        "id": "bullet-1",
                        "name": "Front Door",
                        "type": "UVC G5 Bullet",
                        "state": "CONNECTED",
                        "featureFlags": {"isPtz": False},
                    },
                    {
                        "id": "hasptz-only",
                        "name": "Not a PTZ flag",
                        "type": "UVC G4 PTZ",
                        "state": "CONNECTED",
                        "featureFlags": {"hasPtz": True},
                    },
                ],
                headers={"x-csrf-token": self.csrf},
            )

        if method == "GET" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}":
            return httpx.Response(200, json=self.camera, headers={"x-csrf-token": self.csrf})

        if method == "PATCH" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}":
            self.camera_patch = body
            if isinstance(body, dict) and isinstance(body.get("smartDetectSettings"), dict):
                sds = dict(self.camera.get("smartDetectSettings") or {})
                sds.update(body["smartDetectSettings"])
                self.camera["smartDetectSettings"] = sds
            return httpx.Response(200, json=self.camera, headers={"x-csrf-token": self.csrf})

        if method == "GET" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": PATROL_MONGO_ID,
                        "name": "Night Sweep",
                        "slot": 0,
                        "presets": [0, 1, 2],
                        "presetDurationSeconds": 18,
                        "presetMovementSpeed": 400,
                    }
                ],
                headers={"x-csrf-token": self.csrf},
            )

        if method == "GET" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/preset":
            return httpx.Response(
                200,
                json=[
                    {"id": "pre-0", "name": "Home", "slot": 0, "snapshot": f"/cameras/{CAMERA_ID}/ptz/snapshot/0?hash=h0", "ptz": {"pan": 10, "tilt": 20, "zoom": 1, "focus": 50}},
                    {"id": "pre-1", "name": "Dock entrance", "slot": 1, "snapshot": f"/cameras/{CAMERA_ID}/ptz/snapshot/1?hash=h1", "ptz": {"pan": 18420, "tilt": 4120, "zoom": 240, "focus": 88}},
                    {"id": "pre-2", "name": "Slip A", "slot": 2, "snapshot": f"/cameras/{CAMERA_ID}/ptz/snapshot/2?hash=h2", "ptz": {"pan": 26110, "tilt": 2880, "zoom": 410, "focus": 92}},
                ],
                headers={"x-csrf-token": self.csrf},
            )

        if method == "GET" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/active":
            return httpx.Response(200, json={"slot": 0}, headers={"x-csrf-token": self.csrf})

        if method == "PATCH" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/0":
            self.patched = body
            return httpx.Response(200, json=body or {}, headers={"x-csrf-token": self.csrf})

        if method == "POST" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/start/0":
            if self.fail_private_start:
                return httpx.Response(503, json={"error": "private start down"})
            return httpx.Response(200, json={"ok": True})

        if method == "POST" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/stop":
            if self.fail_private_stop:
                return httpx.Response(503, json={"error": "private stop down"})
            return httpx.Response(200, json={"ok": True})

        if method == "POST" and path.startswith(f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/goto/"):
            slot = int(path.rsplit("/", 1)[-1])
            if slot == 99:
                return httpx.Response(404, json={"error": "missing preset"})
            self.last_goto_slot = slot
            self.goto_slots.append(slot)
            return httpx.Response(200, json={"ok": True}, headers={"x-csrf-token": self.csrf})

        if method == "GET" and path == f"/proxy/protect/api/cameras/{CAMERA_ID}/snapshot":
            return httpx.Response(
                200,
                content=mock_jpeg("PRIVATE-STUCK"),
                headers={"content-type": "image/jpeg"},
            )

        if method == "GET" and "/ptz/snapshot/" in path:
            return httpx.Response(
                200,
                content=mock_jpeg("SLOT-STALE"),
                headers={"content-type": "image/jpeg"},
            )

        return httpx.Response(404, json={"error": f"unexpected {method} {path}"})

    def _private_ok(self, request: httpx.Request, *, write: bool) -> bool:
        cookie = request.headers.get("cookie", "")
        if "TOKEN=live-token" not in cookie:
            return False
        if write and request.headers.get("x-csrf-token") != self.csrf:
            return False
        return True

    def paths(self) -> list[tuple[str, str]]:
        return [(c["method"], c["path"]) for c in self.calls]

    def private_calls(self) -> list[dict]:
        return [c for c in self.calls if c["path"].startswith("/proxy/protect/api")]

    def integration_calls(self) -> list[dict]:
        return [c for c in self.calls if c["path"].startswith("/proxy/protect/integration/")]


def _live_settings(**extra: str) -> Settings:
    settings = Settings()
    settings.overlay.update(
        {
            "unifi_host": NVR,
            "unifi_port": "443",
            "unifi_user": "lab",
            "unifi_pass": "labpass",
            "unifi_api_key": extra.get("unifi_api_key", "should-never-go-on-private"),
            "unifi_verify_tls": False,
        }
    )
    settings.reload()
    return settings


def _client(nvr: FakeNVR, thumb_settle_s: float = 0, sleep=None, **extra: str) -> ProtectClient:
    return ProtectClient(
        _live_settings(**extra),
        transport=httpx.MockTransport(nvr.handler),
        thumb_settle_s=thumb_settle_s,
        sleep=sleep,
    )


def test_login_cameras_patrols_patch_start_stop_exact_paths() -> None:
    nvr = FakeNVR()
    client = _client(nvr)

    cameras = asyncio.run(client.list_cameras())
    assert [c["name"] for c in cameras] == ["Yard East"]
    assert cameras[0]["id"] == CAMERA_ID
    assert cameras[0]["mac"] == "E4:38:83:12:34:56"
    assert cameras[0]["ip"] == "10.0.0.40"
    assert cameras[0]["autoTracking"] == {"person": True, "vehicle": False, "zoomWhileTrack": False}

    patrols = asyncio.run(client.list_patrols(CAMERA_ID))
    assert len(patrols) == 1
    patrol = patrols[0]
    assert patrol["name"] == "Night Sweep"
    assert patrol["slot"] == 0
    assert patrol["active"] is True
    assert [p["slot"] for p in patrol["presets"]] == [0, 1, 2]
    assert patrol["presets"][1]["name"] == "Dock entrance"
    assert patrol["presets"][1]["pan"] == 18420
    assert patrol["presets"][1]["snapshot"].startswith(f"/cameras/{CAMERA_ID}/ptz/snapshot/1")

    patched = asyncio.run(
        client.patch_patrol(CAMERA_ID, str(patrol["slot"]), {"presetDurationSeconds": 0, "presetMovementSpeed": 800})
    )
    assert patched == {"presets": [0, 1, 2], "presetDurationSeconds": 0, "presetMovementSpeed": 800}
    assert nvr.patched == patched

    started = asyncio.run(client.start_patrol(CAMERA_ID, "0"))
    assert started["ok"] is True
    stopped = asyncio.run(client.stop_patrol(CAMERA_ID, "0"))
    assert stopped["ok"] is True

    assert ("POST", "/api/auth/login") in nvr.paths()
    login = next(c for c in nvr.calls if c["path"] == "/api/auth/login")
    assert login["body"] == {"username": "lab", "password": "labpass"}

    assert ("GET", "/proxy/protect/api/cameras") in nvr.paths()
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol") in nvr.paths()
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/preset") in nvr.paths()
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/active") in nvr.paths()
    assert ("PATCH", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/0") in nvr.paths()
    assert ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/start/0") in nvr.paths()
    assert ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/stop") in nvr.paths()

    patch_call = next(c for c in nvr.calls if c["method"] == "PATCH")
    assert patch_call["path"].endswith("/ptz/patrol/0")
    assert PATROL_MONGO_ID not in patch_call["path"]
    assert patch_call["body"] == {"presets": [0, 1, 2], "presetDurationSeconds": 0, "presetMovementSpeed": 800}

    stop_call = next(c for c in nvr.calls if c["path"].endswith("/ptz/patrol/stop"))
    assert stop_call["body"] == {}

    for call in nvr.private_calls():
        assert "x-api-key" not in call["headers"]
        assert "TOKEN=live-token" in call["headers"].get("cookie", "")
        if call["method"] in {"PATCH", "POST"}:
            assert call["headers"].get("x-csrf-token") == "csrf-live-1"

    assert nvr.integration_calls() == []


def test_list_and_patch_never_hit_integration() -> None:
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="integration-key")
    asyncio.run(client.list_cameras())
    asyncio.run(client.list_patrols(CAMERA_ID))
    asyncio.run(client.patch_patrol(CAMERA_ID, "0", {"presetDurationSeconds": 12, "presetMovementSpeed": 400}))
    assert nvr.integration_calls() == []
    assert all("/integration/" not in c["path"] for c in nvr.calls)


def test_start_stop_integration_fallback_only_when_private_fails() -> None:
    nvr = FakeNVR(fail_private_start=True, fail_private_stop=True)
    client = _client(nvr, unifi_api_key="integration-key")
    start = asyncio.run(client.start_patrol(CAMERA_ID, "0"))
    stop = asyncio.run(client.stop_patrol(CAMERA_ID, "0"))
    assert start["ok"] is True
    assert stop["ok"] is True
    assert ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/start/0") in nvr.paths()
    assert ("POST", f"/proxy/protect/integration/v1/cameras/{CAMERA_ID}/ptz/patrol/start/0") in nvr.paths()
    assert ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/stop") in nvr.paths()
    assert ("POST", f"/proxy/protect/integration/v1/cameras/{CAMERA_ID}/ptz/patrol/stop") in nvr.paths()


def test_normalize_camera_isptz_only() -> None:
    from app.protect import normalize_camera

    assert normalize_camera({"id": "1", "name": "A", "featureFlags": {"isPtz": True}})["isPtz"] is True
    assert normalize_camera({"id": "2", "name": "B", "type": "UVC G4 PTZ", "featureFlags": {"hasPtz": True}})["isPtz"] is False
    assert normalize_camera({"id": "3", "name": "C", "isPtz": True})["isPtz"] is True


def test_patrol_count_uses_patrol_list_only() -> None:
    nvr = FakeNVR()
    client = _client(nvr)
    assert asyncio.run(client.patrol_count(CAMERA_ID)) == 1
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol") in nvr.paths()
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/preset") not in nvr.paths()
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/active") not in nvr.paths()


def test_list_patrols_does_not_bank_pov(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="integration-key")
    asyncio.run(client.list_patrols(CAMERA_ID))
    assert not any("/ptz/goto/" in path for _, path in nvr.paths())
    assert not any(path.endswith("/snapshot") for _, path in nvr.paths())
    assert not any("/ptz/snapshot/" in path for _, path in nvr.paths())


def test_pov_bank_recipe_stop_goto_integration_hq(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="integration-key")
    result = asyncio.run(client.refresh_preset_thumbs(CAMERA_ID, [0, 1, 2]))
    assert result["ok"] is True
    assert result["movedCamera"] is True
    assert [item["slot"] for item in result["slots"] if item["ok"]] == [0, 1, 2]

    paths = nvr.paths()
    stop = ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/patrol/stop")
    goto0 = ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/goto/0")
    goto1 = ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/goto/1")
    goto2 = ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/goto/2")
    snap = ("GET", f"/proxy/protect/integration/v1/cameras/{CAMERA_ID}/snapshot")
    assert paths.index(stop) < paths.index(goto0)
    assert paths.index(goto0) < paths.index(snap)
    assert nvr.goto_slots == [0, 1, 2]
    assert paths.count(snap) == 3
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/snapshot") not in paths
    assert not any("/ptz/snapshot/" in path for _, path in paths)

    hashes = set()
    for slot in (0, 1, 2):
        data = read_thumb(CAMERA_ID, slot)
        assert data == mock_jpeg(f"live:{CAMERA_ID}:{slot}")
        assert data != mock_jpeg("PRIVATE-STUCK")
        assert data != mock_jpeg("SLOT-STALE")
        hashes.add(hashlib.sha256(data).hexdigest())
    assert len(hashes) == 3

    for call in nvr.calls:
        if call["path"].endswith("/ptz/goto/0") or call["path"].endswith("/ptz/goto/1") or call["path"].endswith("/ptz/goto/2"):
            assert call["body"] is None
            assert "x-api-key" not in call["headers"]
            assert "TOKEN=live-token" in call["headers"].get("cookie", "")
            assert call["headers"].get("x-csrf-token") == "csrf-live-1"
        if call["path"].endswith("/snapshot") and "/integration/" in call["path"]:
            assert call["headers"].get("x-api-key") == "integration-key"
            assert "image/jpeg" in call["headers"].get("accept", "")
            assert call["url"].endswith("highQuality=true") or "highQuality=true" in call["url"]


def test_pov_bank_skips_missing_slot(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="integration-key")
    result = asyncio.run(client.refresh_preset_thumbs(CAMERA_ID, [0, 99, 1]))
    by_slot = {item["slot"]: item for item in result["slots"]}
    assert by_slot[0]["ok"] is True
    assert by_slot[99]["skipped"] is True
    assert by_slot[1]["ok"] is True
    assert read_thumb(CAMERA_ID, 99) is None
    assert ("POST", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/goto/99") in nvr.paths()
    goto99_i = next(i for i, call in enumerate(nvr.calls) if call["path"].endswith("/ptz/goto/99"))
    goto1_i = next(i for i, call in enumerate(nvr.calls) if call["path"].endswith("/ptz/goto/1"))
    between = nvr.calls[goto99_i + 1 : goto1_i]
    assert not any(c["path"].endswith("/snapshot") for c in between)


def test_pov_bank_requires_integration_key_before_move() -> None:
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="")
    with pytest.raises(ProtectError) as exc:
        asyncio.run(client.refresh_preset_thumbs(CAMERA_ID, [0, 1]))
    assert "Integration API key" in exc.value.message
    assert exc.value.status_code == 400
    assert nvr.paths() == []


def test_pov_bank_settles_after_goto(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    nvr = FakeNVR()
    client = _client(nvr, thumb_settle_s=6.0, sleep=fake_sleep, unifi_api_key="integration-key")
    asyncio.run(client.refresh_preset_thumbs(CAMERA_ID, [0, 1]))
    assert slept == [6.0, 6.0]


def test_refresh_yields_each_slot_before_the_next(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="integration-key")
    events = []

    async def collect():
        async for event in client.iter_refresh_preset_thumbs(CAMERA_ID, [0, 1]):
            events.append((event.get("event"), event.get("slot"), event.get("ok")))

    asyncio.run(collect())
    assert events[0] == ("capturing", 0, None)
    assert events[1][0] == "slot" and events[1][1] == 0 and events[1][2] is True
    assert events[2] == ("capturing", 1, None)
    assert events[3][0] == "slot" and events[3][1] == 1 and events[3][2] is True
    assert events[-1][0] == "done"
    assert events.index(("slot", 0, True)) < events.index(("capturing", 1, None))


def test_stock_snapshot_uses_private_ptz_path_no_goto(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="integration-key")
    jpeg = asyncio.run(client.fetch_stock_snapshot(CAMERA_ID, 1, "h1"))
    assert jpeg == mock_jpeg("SLOT-STALE")
    assert jpeg != mock_jpeg("PRIVATE-STUCK")
    paths = nvr.paths()
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/ptz/snapshot/1") in paths
    assert not any("/ptz/goto/" in path for _, path in paths)
    assert not any("/integration/" in path for _, path in paths)
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}/snapshot") not in paths
    call = next(c for c in nvr.calls if c["path"].endswith("/ptz/snapshot/1"))
    assert "x-api-key" not in call["headers"]
    assert "TOKEN=live-token" in call["headers"].get("cookie", "")
    assert "image/jpeg" in call["headers"].get("accept", "")
    assert "hash=h1" in call["url"]


def test_camera_auto_tracking_get_patch_paths() -> None:
    nvr = FakeNVR()
    client = _client(nvr)
    settings = asyncio.run(client.get_camera_settings(CAMERA_ID))
    assert settings["autoTracking"] == {"person": True, "vehicle": False, "zoomWhileTrack": False}
    patched = asyncio.run(
        client.patch_camera_settings(CAMERA_ID, {"person": True, "vehicle": True, "zoomWhileTrack": True})
    )
    assert patched["autoTracking"]["vehicle"] is True
    assert patched["autoTracking"]["zoomWhileTrack"] is True
    assert ("GET", f"/proxy/protect/api/cameras/{CAMERA_ID}") in nvr.paths()
    assert ("PATCH", f"/proxy/protect/api/cameras/{CAMERA_ID}") in nvr.paths()
    assert nvr.camera_patch == {
        "smartDetectSettings": {
            "autoTrackingObjectTypes": ["person", "vehicle"],
            "autoTrackingWithZoom": True,
        }
    }
    assert "objectTypes" not in nvr.camera_patch["smartDetectSettings"]
    assert nvr.integration_calls() == []
    both_off = asyncio.run(
        client.patch_camera_settings(CAMERA_ID, {"person": False, "vehicle": False, "zoomWhileTrack": False})
    )
    assert both_off["autoTracking"] == {"person": False, "vehicle": False, "zoomWhileTrack": False}
    assert nvr.camera_patch == {
        "smartDetectSettings": {
            "autoTrackingObjectTypes": [],
            "autoTrackingWithZoom": False,
        }
    }


def test_thumb_prefers_refresh_cache_over_stock(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    nvr = FakeNVR()
    client = _client(nvr, unifi_api_key="integration-key")
    stock = asyncio.run(client.fetch_stock_snapshot(CAMERA_ID, 0))
    asyncio.run(client.refresh_preset_thumbs(CAMERA_ID, [0]))
    cached = read_thumb(CAMERA_ID, 0)
    assert stock == mock_jpeg("SLOT-STALE")
    assert cached == mock_jpeg(f"live:{CAMERA_ID}:0")
    assert cached != stock
