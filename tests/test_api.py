from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.logs import log
from app.main import app
from app.protect import ProtectError
from app.settings import ROOT, settings


class FakeProtect:
    def __init__(self) -> None:
        self.cameras = [
            {
                "id": "64f8c2a91b3e4d001a2b3c4d",
                "name": "Yard East",
                "type": "UVC G4 PTZ",
                "state": "CONNECTED",
                "isPtz": True,
                "mac": "E43883123456",
                "firmwareVersion": "4.74.106",
                "autoTracking": {"person": True, "vehicle": False, "zoomWhileTrack": False},
            }
        ]
        self.patrols = [
            {
                "id": "patrol-1",
                "name": "Night Sweep",
                "slot": 0,
                "presetDurationSeconds": 18,
                "presetMovementSpeed": 400,
                "presets": [
                    {"id": "p1", "slot": 1, "name": "Dock entrance", "pan": 18420, "tilt": 4120, "zoom": 240, "focus": 88}
                ],
            }
        ]
        self.patched = None
        self.started = None
        self.stopped = None
        self.fail = None
        self.refreshed = None
        self.fail_thumbs = None

    async def fetch_stock_snapshot(self, camera_id: str, slot: int, snapshot_hash: str = ""):
        from app.thumbs import mock_jpeg

        return mock_jpeg(f"stock:{camera_id}:{slot}")

    async def list_cameras(self):
        if self.fail:
            raise ProtectError(self.fail, 502)
        return self.cameras

    async def list_patrols(self, camera_id: str):
        if self.fail:
            raise ProtectError(self.fail, 502)
        return self.patrols

    async def patch_patrol(self, camera_id: str, patrol_id: str, body: dict):
        self.patched = (camera_id, patrol_id, body)
        return body

    async def start_patrol(self, camera_id: str, patrol_id: str):
        self.started = (camera_id, patrol_id)
        return {"ok": True, "slot": 0}

    async def stop_patrol(self, camera_id: str, patrol_id: str):
        self.stopped = (camera_id, patrol_id)
        return {"ok": True}

    async def get_camera_settings(self, camera_id: str):
        tracking = self.cameras[0].get("autoTracking") or {
            "person": False,
            "vehicle": False,
            "zoomWhileTrack": False,
        }
        return {"cameraId": camera_id, "autoTracking": dict(tracking), "mode": "mock"}

    async def patch_camera_settings(self, camera_id: str, body: dict):
        current = dict((await self.get_camera_settings(camera_id))["autoTracking"])
        current.update(body)
        self.cameras[0]["autoTracking"] = current
        return {"cameraId": camera_id, "autoTracking": current, "mode": "mock"}

    async def iter_refresh_preset_thumbs(self, camera_id: str, slots):
        if self.fail_thumbs:
            raise ProtectError(self.fail_thumbs, 400)
        from app.thumbs import mock_jpeg, thumb_mtime, write_thumb

        self.refreshed = (camera_id, list(slots))
        results = []
        for slot in slots:
            yield {"event": "capturing", "slot": slot, "cameraId": camera_id}
            write_thumb(camera_id, slot, mock_jpeg(f"fake:{camera_id}:{slot}"))
            yield {
                "event": "slot",
                "slot": slot,
                "ok": True,
                "thumbMtime": thumb_mtime(camera_id, slot),
            }
            results.append({"slot": slot, "ok": True})
        yield {
            "event": "done",
            "ok": True,
            "cameraId": camera_id,
            "mode": "mock",
            "movedCamera": False,
            "slots": results,
        }


@pytest.fixture
def fake(tmp_path, monkeypatch) -> FakeProtect:
    from app import patrol_points, thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    monkeypatch.setattr(patrol_points, "POINTS_DIR", tmp_path / "patrol-points")
    client = FakeProtect()
    app.state.protect = client
    log.clear()
    settings.overlay.clear()
    settings.reload()
    yield client
    app.state.protect = None
    log.clear()


@pytest.fixture
def client(fake: FakeProtect) -> TestClient:
    with TestClient(app) as test:
        yield test


def ndjson_events(response) -> list:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def login(client: TestClient) -> None:
    response = client.post("/api/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def test_health_public(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["version"]
    assert body["protect"]["mode"] in {"live", "mock"}


def test_pydantic_models_import_on_py39() -> None:
    from typing import Optional, get_type_hints

    from app.main import AutoTrackingBody, CameraSettingsBody, PatrolPatchBody, PatrolPointsBody

    hints = get_type_hints(PatrolPatchBody)
    assert hints["presetDurationSeconds"] == Optional[int]
    assert hints["presetMovementSpeed"] == Optional[int]
    assert PatrolPatchBody().presetDurationSeconds is None
    assert PatrolPatchBody(presetDurationSeconds=0, presetMovementSpeed=800).presetMovementSpeed == 800
    assert AutoTrackingBody(person=True, vehicle=False, zoomWhileTrack=True).zoomWhileTrack is True
    assert CameraSettingsBody(autoTracking=AutoTrackingBody(person=True)).autoTracking.person is True
    assert PatrolPointsBody(points=[{"slot": 1, "presetDurationSeconds": 0, "presetMovementSpeed": 0}]).points[0].slot == 1
    with pytest.raises(ValidationError):
        PatrolPointsBody(points=[{"slot": 1, "presetDurationSeconds": 0, "presetMovementSpeed": -1}])
    with pytest.raises(ValidationError):
        PatrolPointsBody(points=[{"slot": 1, "presetDurationSeconds": 128, "presetMovementSpeed": 0}])
    legal = PatrolPointsBody(points=[{"slot": 1, "presetDurationSeconds": 127, "presetMovementSpeed": 0}])
    assert legal.points[0].presetDurationSeconds == 127
    assert legal.points[0].presetMovementSpeed == 0
    assert PatrolPointsBody(points=[{"slot": 1, "presetDurationSeconds": 0, "presetMovementSpeed": 1000}]).points[0].presetMovementSpeed == 1000


def test_cameras_require_login(client: TestClient) -> None:
    response = client.get("/api/cameras")
    assert response.status_code == 401


def test_login_reject(client: TestClient) -> None:
    response = client.post("/api/login", json={"username": "admin", "password": "nope"})
    assert response.status_code == 401


def test_cameras_ok(client: TestClient) -> None:
    login(client)
    settings.overlay["unifi_host"] = "10.0.0.1"
    settings.overlay["unifi_api_key"] = "test-key"
    settings.reload()
    response = client.get("/api/cameras")
    assert response.status_code == 200
    camera = response.json()["cameras"][0]
    assert camera["name"] == "Yard East"
    assert camera["patrolCount"] == 1


def test_cameras_patrol_count_zero_when_empty(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    fake.patrols = []
    response = client.get("/api/cameras")
    assert response.status_code == 200
    assert response.json()["cameras"][0]["patrolCount"] == 0


def test_cameras_protect_error(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    settings.overlay["unifi_host"] = "10.0.0.1"
    settings.overlay["unifi_api_key"] = "test-key"
    settings.reload()
    fake.fail = "console unreachable"
    response = client.get("/api/cameras")
    assert response.status_code == 502
    assert "unreachable" in response.json()["error"]


def test_health_reports_api_key_flag(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert "apiKeySet" in body["protect"]


def test_preset_thumb_requires_login(client: TestClient) -> None:
    response = client.get("/api/cameras/cam-1/presets/1/thumb")
    assert response.status_code == 401


def test_preset_thumb_defaults_to_stock(client: TestClient) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    response = client.get(f"/api/cameras/{camera}/presets/1/thumb")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    listed = client.get(f"/api/cameras/{camera}/patrols")
    preset = listed.json()["patrols"][0]["presets"][0]
    assert preset["hasCache"] is False
    assert preset["thumbSource"] == "stock"
    assert preset["hasThumb"] is True
    assert "/ptz/snapshot/1" in preset["snapshot"]


def test_refresh_pov_icons_and_serve_thumb(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    stock = client.get(f"/api/cameras/{camera}/presets/1/thumb")
    assert stock.status_code == 200
    stock_bytes = stock.content

    refresh = client.post(f"/api/cameras/{camera}/patrols/patrol-1/thumbs/refresh")
    assert refresh.status_code == 200
    assert fake.refreshed == (camera, [1])
    assert "ndjson" in refresh.headers.get("content-type", "")
    events = ndjson_events(refresh)
    assert [e["event"] for e in events] == ["capturing", "slot", "done"]
    assert events[1]["ok"] is True
    assert events[1]["slot"] == 1
    assert events[-1]["event"] == "done"

    thumb = client.get(f"/api/cameras/{camera}/presets/1/thumb")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")
    assert thumb.content[:2] == b"\xff\xd8"
    assert thumb.content != stock_bytes

    listed = client.get(f"/api/cameras/{camera}/patrols")
    preset = listed.json()["patrols"][0]["presets"][0]
    assert preset["hasCache"] is True
    assert preset["thumbSource"] == "cache"
    assert preset["thumbMtime"]


def test_refresh_camera_thumbs(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    refresh = client.post(f"/api/cameras/{camera}/thumbs/refresh")
    assert refresh.status_code == 200
    assert fake.refreshed == (camera, [1])


def test_refresh_thumbs_no_integration_key(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    fake.fail_thumbs = "Protect Integration API key is required to refresh snapshots. Set UNIFI_API_KEY or save it on the Config page."
    response = client.post("/api/cameras/64f8c2a91b3e4d001a2b3c4d/thumbs/refresh")
    assert response.status_code == 400
    assert "Integration API key" in response.json()["error"]


def test_patrols_and_actions(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    listed = client.get(f"/api/cameras/{camera}/patrols")
    assert listed.status_code == 200
    patrol = listed.json()["patrols"][0]
    assert patrol["name"] == "Night Sweep"
    assert patrol["presets"][0]["presetDurationSeconds"] == 18
    assert patrol["presets"][0]["dwellSource"] == "tour"

    patched = client.patch(
        f"/api/cameras/{camera}/patrols/patrol-1",
        json={"presetDurationSeconds": 0, "presetMovementSpeed": 600},
    )
    assert patched.status_code == 200
    assert fake.patched[2]["presetMovementSpeed"] == 600

    start = client.post(f"/api/cameras/{camera}/patrols/patrol-1/start")
    assert start.status_code == 200
    assert fake.started == (camera, "patrol-1")

    stop = client.post(f"/api/cameras/{camera}/patrols/patrol-1/stop")
    assert stop.status_code == 200
    assert fake.stopped == (camera, "patrol-1")


def test_save_patrol_points_sidecar_not_protect_patch(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    saved = client.put(
        f"/api/cameras/{camera}/patrols/patrol-1/points",
        json={
            "points": [
                {
                    "slot": 1,
                    "presetDurationSeconds": 7,
                    "presetMovementSpeed": 250,
                    "pan": 99,
                    "tilt": 98,
                    "zoom": 97,
                    "focus": 96,
                }
            ]
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["dwellSpeed"]["saved"] == "sidecar"
    assert body["dwellSpeed"]["applied"] is False
    assert body["ptz"]["saved"] is False
    assert "Per-preset dwell/speed saved locally" in body["note"]
    assert body["points"][0]["presetDurationSeconds"] == 7
    assert "pan" not in body["points"][0]
    assert fake.patched is None

    listed = client.get(f"/api/cameras/{camera}/patrols").json()["patrols"][0]
    assert listed["presets"][0]["presetDurationSeconds"] == 7
    assert listed["presets"][0]["presetMovementSpeed"] == 250
    assert listed["presets"][0]["dwellSource"] == "sidecar"
    assert listed["presets"][0]["pan"] == 18420


def test_save_patrol_points_rejects_out_of_range(client: TestClient) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    too_fast = client.put(
        f"/api/cameras/{camera}/patrols/patrol-1/points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 7, "presetMovementSpeed": 100000}]},
    )
    assert too_fast.status_code == 422
    too_slow = client.put(
        f"/api/cameras/{camera}/patrols/patrol-1/points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 7, "presetMovementSpeed": -1}]},
    )
    assert too_slow.status_code == 422
    too_long = client.put(
        f"/api/cameras/{camera}/patrols/patrol-1/points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 128, "presetMovementSpeed": 0}]},
    )
    assert too_long.status_code == 422
    ok_range = client.put(
        f"/api/cameras/{camera}/patrols/patrol-1/points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 127, "presetMovementSpeed": 0}]},
    )
    assert ok_range.status_code == 200
    assert ok_range.json()["points"][0]["presetDurationSeconds"] == 127
    assert ok_range.json()["points"][0]["presetMovementSpeed"] == 0


def _live_fake(fake: FakeProtect, calls: list) -> None:
    fake.live = True

    async def raw_camera(camera_id):
        return {"id": camera_id, "host": "10.0.0.50"}

    async def set_ssh(camera_id, enabled):
        calls.append(("ssh", enabled))
        return {"ok": True, "status": 200, "isSshEnabled": enabled}

    async def recovery_code(camera_id):
        calls.append(("code", camera_id))
        return "secret-code-123"

    fake._raw_camera = raw_camera
    fake.set_camera_ssh = set_ssh
    fake.camera_recovery_code = recovery_code


def test_apply_points_writes_camera_and_turns_ssh_off(client: TestClient, fake: FakeProtect, monkeypatch) -> None:
    from app import camera_ssh
    from app.patrol_points import read_points

    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    calls: list = []
    _live_fake(fake, calls)
    monkeypatch.setattr(camera_ssh, "wait_port", lambda host, port=22, timeout=30: True)

    def fake_ssh(host, user, secret, remote, timeout=25):
        calls.append(("run", host, user, secret == "secret-code-123", remote))

        class CP:
            returncode = 0
            stdout = '{"ok":true} secret-code-123'
            stderr = ""

        return CP()

    monkeypatch.setattr(camera_ssh, "ssh_run", fake_ssh)
    applied = client.post(
        f"/api/cameras/{camera}/patrols/0/apply-points",
        json={
            "points": [
                {"slot": 1, "presetDurationSeconds": 0, "presetMovementSpeed": 800, "pan": 1},
                {"slot": 2, "presetDurationSeconds": 12, "presetMovementSpeed": 200},
            ]
        },
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["ok"] is True
    assert "secret-code-123" not in applied.text
    assert calls[0] == ("code", camera)
    assert calls[1] == ("ssh", True)
    assert calls[-1] == ("ssh", False)
    run = calls[2]
    assert run[1:4] == ("10.0.0.50", "ubnt", True)
    assert "-T=ubnt_ptz" in run[4]
    assert '"action":"set"' in run[4]
    assert '"0":{"preset":1,"dwellTime":0,"moveSpeed":800}' in run[4]
    assert '"1":{"preset":2,"dwellTime":12,"moveSpeed":200}' in run[4]
    assert '"go"' not in run[4]
    assert fake.patched is None
    assert fake.started is None
    saved = read_points(camera, 0)
    assert [(p["slot"], p["presetDurationSeconds"], p["presetMovementSpeed"]) for p in saved["points"]] == [(1, 0, 800), (2, 12, 200)]
    assert body["points"][1]["presetDurationSeconds"] == 12
    listed = client.get(f"/api/cameras/{camera}/patrols").json()["patrols"][0]["presets"][0]
    assert listed["presetDurationSeconds"] == 0 and listed["dwellSource"] == "sidecar"
    settings_body = client.get(f"/api/cameras/{camera}/settings").json()
    assert "recoveryCodeSet" not in settings_body
    assert "secret-code-123" not in json.dumps(settings_body)


def test_apply_points_turns_ssh_off_on_failure(client: TestClient, fake: FakeProtect, monkeypatch) -> None:
    from app import camera_ssh

    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    calls: list = []
    _live_fake(fake, calls)
    monkeypatch.setattr(camera_ssh, "wait_port", lambda host, port=22, timeout=30: True)

    def denied(host, user, secret, remote, timeout=25):
        class CP:
            returncode = 255
            stdout = ""
            stderr = "ubnt@10.0.0.50: Permission denied (secret-code-123)"

        return CP()

    monkeypatch.setattr(camera_ssh, "ssh_run", denied)
    applied = client.post(
        f"/api/cameras/{camera}/patrols/0/apply-points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 10, "presetMovementSpeed": 200}]},
    )
    assert applied.status_code == 502
    assert "recovery code" in applied.json()["error"]
    assert "secret-code-123" not in applied.text
    assert calls[-1] == ("ssh", False)
    from app.patrol_points import read_points

    assert read_points(camera, 0) is None  # failed Apply saves nothing


def test_start_writes_saved_points_after_protect_start(client: TestClient, fake: FakeProtect, monkeypatch) -> None:
    from app import camera_ssh

    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    calls: list = []
    _live_fake(fake, calls)
    client.put(
        f"/api/cameras/{camera}/patrols/patrol-1/points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 9, "presetMovementSpeed": 300}]},
    )
    monkeypatch.setattr(camera_ssh, "wait_port", lambda host, port=22, timeout=30: True)
    orig_start = fake.start_patrol

    async def start(camera_id, patrol_id):
        calls.append(("start", patrol_id))
        return await orig_start(camera_id, patrol_id)

    fake.start_patrol = start

    def ok_ssh(host, user, secret, remote, timeout=25):
        calls.append(("run", remote))

        class CP:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return CP()

    monkeypatch.setattr(camera_ssh, "ssh_run", ok_ssh)
    started = client.post(f"/api/cameras/{camera}/patrols/patrol-1/start")
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["ok"] is True and body["pointsWritten"] is True
    kinds = [c[0] for c in calls]
    assert kinds.index("start") < kinds.index("code") < kinds.index("run")
    assert calls[-1] == ("ssh", False)
    run = [c for c in calls if c[0] == "run"][0]
    assert '"0":{"preset":1,"dwellTime":9,"moveSpeed":300}' in run[1]


def test_start_without_saved_points_is_protect_only(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    calls: list = []
    _live_fake(fake, calls)
    started = client.post("/api/cameras/64f8c2a91b3e4d001a2b3c4d/patrols/patrol-1/start")
    assert started.status_code == 200
    assert started.json()["pointsWritten"] is False
    assert calls == []


def test_start_reports_points_error_but_patrol_started(client: TestClient, fake: FakeProtect) -> None:
    from app.protect import ProtectError

    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    calls: list = []
    _live_fake(fake, calls)
    client.put(
        f"/api/cameras/{camera}/patrols/patrol-1/points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 9, "presetMovementSpeed": 300}]},
    )

    async def denied(camera_id):
        raise ProtectError("PatrolCraft account lacks permission to read the camera recovery code.", 403)

    fake.camera_recovery_code = denied
    started = client.post(f"/api/cameras/{camera}/patrols/patrol-1/start")
    assert started.status_code == 200
    body = started.json()
    assert fake.started is not None
    assert body["pointsWritten"] is False
    assert "lacks permission" in body["pointsError"]


def test_apply_points_stops_on_code_permission_denied(client: TestClient, fake: FakeProtect) -> None:
    from app.protect import ProtectError

    login(client)
    calls: list = []
    _live_fake(fake, calls)

    async def denied(camera_id):
        calls.append(("code", camera_id))
        raise ProtectError("PatrolCraft account lacks permission to read the camera recovery code.", 403)

    fake.camera_recovery_code = denied
    applied = client.post(
        "/api/cameras/64f8c2a91b3e4d001a2b3c4d/patrols/0/apply-points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 10, "presetMovementSpeed": 200}]},
    )
    assert applied.status_code == 502
    assert "lacks permission" in applied.json()["error"]
    assert applied.json()["status"] == 403
    assert calls == [("code", "64f8c2a91b3e4d001a2b3c4d")]  # SSH never touched


def test_apply_points_mock_mode_sends_nothing(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    fake.live = False
    applied = client.post(
        "/api/cameras/64f8c2a91b3e4d001a2b3c4d/patrols/0/apply-points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 10, "presetMovementSpeed": 200}]},
    )
    assert applied.status_code == 200
    assert applied.json()["mode"] == "mock"


def test_recovery_code_routes_removed(client: TestClient) -> None:
    login(client)
    gone = client.put("/api/cameras/64f8c2a91b3e4d001a2b3c4d/recovery-code", json={"code": "abc12345"})
    assert gone.status_code in (404, 405)


def test_apply_points_rejects_out_of_range(client: TestClient, monkeypatch) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"

    too_long = client.post(
        f"/api/cameras/{camera}/patrols/0/apply-points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 128, "presetMovementSpeed": 200}]},
    )
    assert too_long.status_code == 422
    too_fast = client.post(
        f"/api/cameras/{camera}/patrols/0/apply-points",
        json={"points": [{"slot": 1, "presetDurationSeconds": 10, "presetMovementSpeed": 1001}]},
    )
    assert too_fast.status_code == 422


def test_camera_settings_auto_tracking(client: TestClient, fake: FakeProtect) -> None:
    login(client)
    camera = "64f8c2a91b3e4d001a2b3c4d"
    got = client.get(f"/api/cameras/{camera}/settings")
    assert got.status_code == 200
    assert got.json()["autoTracking"] == {"person": True, "vehicle": False, "zoomWhileTrack": False}

    patched = client.patch(
        f"/api/cameras/{camera}/settings",
        json={"autoTracking": {"vehicle": True, "zoomWhileTrack": True}},
    )
    assert patched.status_code == 200
    tracking = patched.json()["autoTracking"]
    assert tracking["person"] is True
    assert tracking["vehicle"] is True
    assert tracking["zoomWhileTrack"] is True
    listed = client.get("/api/cameras").json()["cameras"][0]
    assert listed["autoTracking"]["vehicle"] is True


def test_camera_settings_require_login(client: TestClient) -> None:
    response = client.get("/api/cameras/64f8c2a91b3e4d001a2b3c4d/settings")
    assert response.status_code == 401


def test_logs_and_config(client: TestClient, tmp_path, monkeypatch) -> None:
    login(client)
    from app import settings as settings_mod

    monkeypatch.setattr(settings_mod, "LOCAL_YAML", tmp_path / "config.local.yaml")
    saved = client.put(
        "/api/config",
        json={"host": "10.0.0.2", "port": 443, "apiKey": "secret-key", "verifyTls": False},
    )
    assert saved.status_code == 200
    assert saved.json()["apiKeySet"] is True
    assert "secret-key" not in saved.text
    lines = client.get("/api/logs")
    assert lines.status_code == 200
    assert any("Saved" in row["message"] for row in lines.json()["lines"])


def test_kit_css_served(client: TestClient) -> None:
    response = client.get("/kit/css/yestechie-appliance.css")
    assert response.status_code == 200
    assert "--bg-color: #f4f5f4" in response.text
    assert ".ambient-scanner-h" in response.text


def test_version_public(client: TestClient) -> None:
    response = client.get("/api/version")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["version"] == Path(ROOT / "VERSION").read_text().strip()


def test_logout_redirect(client: TestClient) -> None:
    login(client)
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert "/pages/login.html" in response.headers["location"]
    assert client.get("/api/me").status_code == 401


def test_yestechie_labs_style_not_analyzer() -> None:
    login_html = (ROOT / "pages" / "login.html").read_text()
    app_html = (ROOT / "pages" / "app.html").read_text()
    kit_css = (ROOT / "vendor" / "yestechie-ui-kit" / "css" / "yestechie-appliance.css").read_text()

    assert "ambient-scanner" in login_html
    assert "login-container" in login_html
    assert "panel-corner" in login_html
    assert "page-login" in login_html
    assert "yestechie-appliance.css" in login_html
    assert "Inter" not in login_html
    assert "Outfit" not in login_html
    assert "talk-btn" not in login_html

    assert 'class="sidebar"' in app_html
    assert "// Patrols" in app_html
    assert "// Config" in app_html
    assert "// Logs" in app_html
    assert "// Sign Out" in app_html
    assert 'id="header"' not in app_html
    assert "bridge.css" not in app_html
    assert "Inter" not in app_html
    assert "yestechie-appliance.css" in app_html
    assert "patrolcraft.css" in app_html
    assert "camera-tile" in (ROOT / "js" / "app.js").read_text()
    assert "cameraProductImage" in (ROOT / "js" / "app.js").read_text()
    assert "uvc-ai-ptz-precision-white.png" in (ROOT / "js" / "app.js").read_text()
    assert ".camera-photo" in (ROOT / "css" / "patrolcraft.css").read_text()
    assert "object-fit: contain" in (ROOT / "css" / "patrolcraft.css").read_text()
    assert "background: transparent" in (ROOT / "css" / "patrolcraft.css").read_text()
    assert "grayscale" not in (ROOT / "css" / "patrolcraft.css").read_text()
    assert "camera-offline-silhouette.png" in (ROOT / "js" / "app.js").read_text()
    assert "is-offline" in (ROOT / "js" / "app.js").read_text()
    assert "#1c1f1d" not in (ROOT / "css" / "patrolcraft.css").read_text()
    assert "id=\"header\"" not in (ROOT / "css" / "patrolcraft.css").read_text()
    assert "bridge.css" not in (ROOT / "css" / "patrolcraft.css").read_text()

    assert "--bg-color: #f4f5f4" in kit_css
    assert ".sidebar" in kit_css
    assert "YesTechie Labs Style" in kit_css
    assert "Bridge Analyzer dark header is NOT this file" in kit_css
    assert (ROOT / "VERSION").read_text().strip() == "0.5.0"
    js = (ROOT / "js" / "app.js").read_text()
    assert "[data-camera-accordion]" in js
    assert 'selectedId === id ? "#/patrols"' in js
    css = (ROOT / "css" / "patrolcraft.css").read_text()
    assert "@media (min-width: 769px)" in css
    assert "position: sticky" not in css
    assert "overflow-y: auto" in css
    assert "html," in css
    assert "min-height: 0" in css
    assert "overflow: hidden" in css
    assert "max-height: 100vh" in css
    assert "preset-thumb" in js
    assert "Refresh snapshot" in js
    assert "Refresh POV Icons" not in js
    assert "data-refresh-thumbs-patrol" in js
    assert "data-refresh-thumbs-camera" not in js
    assert "moves this camera" in js
    assert "paintPresetThumb" in js
    assert "readNdjson" in js
    assert "data-thumb-slot" in js
    assert "thumbMtime" in js
    assert "snapshotHash" in js
    assert ".preset-thumb" in css
    assert ".preset-row" in css
    assert ".preset-row.is-capturing" in css
    assert "width: 160px" in css
    assert "height: 100px" in css
    assert "width: 48px" not in css
    assert "height: 32px" not in css
    assert "width: 144px" not in css
    assert "height: 96px" not in css
    assert ".preset-row-body" in css
    assert "grid-template-columns: 160px 1fr" in css
    assert "object-fit: cover" in css
    assert ".preset-ptz" in css
    assert "Integration Api Key (Optional)" not in js
    assert "required to refresh snapshots" in js
    assert "camera-group" in (ROOT / "js" / "app.js").read_text()
    assert "is-open" in (ROOT / "js" / "app.js").read_text()
    assert "renderCameraAccordion" in (ROOT / "js" / "app.js").read_text()
    assert "renderCameraGroup" in (ROOT / "js" / "app.js").read_text()
    assert "cameraPatrolCountLabel" in js
    assert "camera.patrolCount" in js
    assert ".camera-group" in (ROOT / "css" / "patrolcraft.css").read_text()
    assert ".camera-accordion" in (ROOT / "css" / "patrolcraft.css").read_text()
    assert (ROOT / "vendor" / "yestechie-ui-kit" / "VERSION").read_text().strip() == "0.2.1"
    js = (ROOT / "js" / "app.js").read_text()
    css = (ROOT / "css" / "patrolcraft.css").read_text()
    assert "Save Dwell / Speed" not in js
    assert "data-save-patrol" not in js
    assert "Movement Speed" not in js
    assert "Auto-tracking" in js
    assert "Zoom-while-track" in js
    assert "data-autotrack" in js
    assert "Save presets" not in js
    assert "Save dwell / speed" not in js
    assert "data-save-points" not in js
    assert 'class="btn btn-primary" type="button" data-apply-points=' in js
    assert "Hide Presets" in js
    assert '"Hide Presets" : "Edit"' in js
    assert "Stock POV from Protect preset snapshot" not in js
    assert "Per-preset dwell/speed saved locally" not in js
    assert "autoTrackingObjectTypes" not in js
    assert "ptzSocket" not in js
    assert "does not rewrite pose" not in js
    assert "data-field=\"dwell\"" in js
    assert "data-field=\"pan\"" not in js
    assert "preset-ptz" in js
    assert "Live apply is not available yet." not in js
    assert "data-apply-points" in js
    assert "/apply-points" in js
    assert "Patrol settings applied." in js
    assert "data-save-recovery" not in js
    assert "rememberSavedPoints(cameraId, null, slot, result?.points)" in js
    assert "Patrol started with saved dwell and speed." in js
    assert "data-recovery-input" not in js
    assert "Moves the camera to each preset and updates its photo." not in js
    assert ">Focus <span class=\"data-value\">" in js
    assert "Dwell must be 0–127 seconds." in js
    assert "Speed must be 0–1000." in js
    assert "DWELL_MAX = 127" in js
    assert "SPEED_MIN = 0" in js
    assert "0–9999" not in js
    assert "200–1000" not in js
    assert "How fast the camera moves to this preset" in js
    assert "validatePresetPanel" in js
    assert "data-tip=" in js
    assert "title=" in js
    assert "Camera type from Protect." not in js
    assert "Patrol slot on this camera." not in js
    assert "Patrol name. Read-only." not in js
    assert "Pan position. Read-only." not in js
    assert "Start this patrol." not in js
    assert "Save dwell and speed for each preset." not in js
    assert "function infoIcon" in js
    assert "function applyControl" not in js
    assert "function btnWithInfo" not in js
    assert 'class="info-i"' in js
    assert 'class="info-i" data-tip=' in js
    assert 'class="info-i" title=' not in js
    assert "btn-with-info" not in js
    assert ">(i)</button>" in js
    assert 'labeled("Dwell"' in js
    assert 'labeled("Speed"' in js
    assert 'renderTrackToggle("Person"' in js
    assert 'renderTrackToggle("Vehicle"' in js
    assert 'renderTrackToggle("Zoom-while-track"' in js
    assert 'labeled("Type"' not in js
    assert 'labeled("State"' not in js
    assert 'labeled("Patrols"' not in js
    assert 'labeled("Ip"' not in js
    assert 'labeled("Mac"' not in js
    assert 'labeled("Slot"' not in js
    assert 'labeled("Name"' not in js
    assert 'labeled("P"' not in js
    assert "data-start-patrol" in js
    assert "data-refresh-thumbs-patrol" in js
    assert "preset-refresh-row" in js
    assert ".info-i" in css
    assert ".preset-refresh-row" in css
    assert "padding-bottom: 16px" in css
    assert ".btn-with-info" not in css
    assert ".toggle" in css
    assert ".preset-fields" in css
    assert ".toggle.is-on" in css
    assert ".field-error" in css
    assert ".form-group.is-invalid" in css


def test_no_default_login(monkeypatch) -> None:
    from app.settings import Settings

    for name in ("APP_USER", "APP_PASS", "APP_SECRET", "APP_HOST"):
        monkeypatch.delenv(name, raising=False)
    fresh = Settings()
    assert fresh.app_user == "" and fresh.app_pass == ""
    assert not fresh.login_ready
    assert len(fresh.app_secret) >= 48
    assert fresh.app_host == "127.0.0.1"


def test_login_refused_when_not_set_up(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_pass", "")
    response = client.post("/api/login", json={"username": "admin", "password": ""})
    assert response.status_code == 503


def test_data_folder_not_served(client: TestClient) -> None:
    assert client.get("/data/mock-console.json").status_code == 404
