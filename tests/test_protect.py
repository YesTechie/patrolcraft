import asyncio

from app.protect import (
    ProtectClient,
    auto_tracking_patch_body,
    camera_ip,
    format_mac,
    normalize_auto_tracking,
    normalize_camera,
    normalize_patrol,
    normalize_preset,
)
from app.settings import Settings


def test_normalize_camera_ptz_flag() -> None:
    camera = normalize_camera(
        {"id": "abc", "name": "Yard East", "type": "UVC G4 PTZ", "state": "CONNECTED", "featureFlags": {"isPtz": True}}
    )
    assert camera["isPtz"] is True
    assert camera["name"] == "Yard East"
    assert camera["productImage"].endswith("uvc-ai-ptz-precision-white.png")
    assert camera["ip"] == ""
    assert camera["mac"] == ""
    assert "patrolCount" not in camera
    nested = normalize_camera(
        {
            "id": "abc",
            "name": "Yard East",
            "type": "UVC G4 PTZ",
            "state": "CONNECTED",
            "isPtz": True,
            "patrols": [{"id": "p1"}, {"id": "p2"}],
        }
    )
    assert nested["patrolCount"] == 2
    empty = normalize_camera({"id": "x", "name": "Empty", "isPtz": True, "patrols": []})
    assert empty["patrolCount"] == 0


def test_format_mac_colon_separated() -> None:
    assert format_mac("E43883123456") == "E4:38:83:12:34:56"
    assert format_mac("e4:38:83:12:34:56") == "E4:38:83:12:34:56"
    assert format_mac("") == ""
    assert format_mac(None) == ""


def test_camera_ip_from_protect_fields() -> None:
    assert camera_ip({"host": "10.0.0.40"}) == "10.0.0.40"
    assert camera_ip({"connectionHost": "10.0.0.41"}) == "10.0.0.41"
    assert camera_ip({"ip": "10.0.0.8"}) == "10.0.0.8"
    assert camera_ip({"host": "", "connectionHost": "10.0.0.41"}) == "10.0.0.41"
    assert camera_ip({"connection": {"host": "10.0.0.50"}}) == "10.0.0.50"
    assert camera_ip({}) == ""


def test_normalize_camera_ip_and_mac() -> None:
    camera = normalize_camera(
        {
            "id": "abc",
            "name": "Yard East",
            "type": "UVC AI PTZ Precision",
            "mac": "E43883123456",
            "host": "10.0.0.40",
            "state": "CONNECTED",
            "featureFlags": {"isPtz": True},
        }
    )
    assert camera["mac"] == "E4:38:83:12:34:56"
    assert camera["ip"] == "10.0.0.40"
    assert camera["host"] == "10.0.0.40"


def test_normalize_preset_keeps_snapshot() -> None:
    preset = normalize_preset(
        {
            "id": "p1",
            "slot": 1,
            "name": "Dock",
            "snapshot": "/cameras/abc/ptz/snapshot/1?hash=deadbeef",
            "ptz": {"pan": 1, "tilt": 2, "zoom": 3, "focus": 4},
        },
        "abc",
    )
    assert preset["snapshot"].startswith("/cameras/abc/ptz/snapshot/1")
    assert "hash=deadbeef" in preset["snapshot"]


def test_normalize_patrol_joins_presets() -> None:
    presets = {
        1: normalize_preset({"id": "p1", "slot": 1, "name": "Dock", "ptz": {"pan": 1, "tilt": 2, "zoom": 3, "focus": 4}})
    }
    patrol = normalize_patrol(
        {"id": "n1", "name": "Night", "slot": 0, "presets": [1], "presetDurationSeconds": 0, "presetMovementSpeed": 400},
        presets,
    )
    assert patrol["presets"][0]["name"] == "Dock"
    assert patrol["presetDurationSeconds"] == 0


def test_mock_patch_camera_auto_tracking() -> None:
    settings = Settings()
    settings.overlay["unifi_host"] = ""
    settings.overlay["unifi_user"] = ""
    settings.overlay["unifi_pass"] = ""
    settings.reload()
    client = ProtectClient(settings)
    camera_id = "64f8c2a91b3e4d001a2b3c4d"
    before = asyncio.run(client.get_camera_settings(camera_id))
    assert before["autoTracking"]["person"] is True
    assert before["mode"] == "mock"
    after = asyncio.run(
        client.patch_camera_settings(camera_id, {"vehicle": True, "zoomWhileTrack": True})
    )
    assert after["autoTracking"] == {"person": True, "vehicle": True, "zoomWhileTrack": True}
    cam = next(camera for camera in asyncio.run(client.list_cameras()) if camera["id"] == camera_id)
    assert cam["autoTracking"]["vehicle"] is True
    assert cam["autoTracking"]["zoomWhileTrack"] is True


def test_normalize_auto_tracking_from_smart_detect() -> None:
    tracking = normalize_auto_tracking(
        {
            "smartDetectSettings": {
                "objectTypes": ["person", "vehicle"],
                "autoTrackingObjectTypes": ["person"],
                "autoTrackingWithZoom": True,
            }
        }
    )
    assert tracking == {"person": True, "vehicle": False, "zoomWhileTrack": True}
    payload = auto_tracking_patch_body({"person": False, "vehicle": True, "zoomWhileTrack": False})
    assert payload == {
        "smartDetectSettings": {
            "autoTrackingObjectTypes": ["vehicle"],
            "autoTrackingWithZoom": False,
        }
    }
    empty = auto_tracking_patch_body({"person": False, "vehicle": False, "zoomWhileTrack": False})
    assert empty["smartDetectSettings"]["autoTrackingObjectTypes"] == []


def test_normalize_auto_tracking_ignores_object_types() -> None:
    tracking = normalize_auto_tracking(
        {
            "smartDetectSettings": {
                "objectTypes": ["person", "vehicle"],
                "autoTrackingObjectTypes": [],
                "autoTrackingWithZoom": False,
            }
        }
    )
    assert tracking == {"person": False, "vehicle": False, "zoomWhileTrack": False}


def test_mock_cameras_without_credentials() -> None:
    settings = Settings()
    settings.overlay["unifi_host"] = ""
    settings.overlay["unifi_user"] = ""
    settings.overlay["unifi_pass"] = ""
    settings.reload()
    cameras = asyncio.run(ProtectClient(settings).list_cameras())
    cam = next(camera for camera in cameras if camera["name"] == "Yard East")
    assert cam["mac"] == "E4:38:83:12:34:56"
    assert cam["ip"] == "10.0.0.40"
    assert cam["patrolCount"] == 2
    parking = next(camera for camera in cameras if camera["name"] == "Parking Lot")
    assert parking["patrolCount"] == 1
    assert cam["autoTracking"]["person"] is True
    assert cam["autoTracking"]["vehicle"] is False
    assert cam["autoTracking"]["zoomWhileTrack"] is False


def test_mock_stock_snapshot_without_refresh() -> None:
    from app.thumbs import mock_jpeg

    settings = Settings()
    settings.overlay["unifi_host"] = ""
    settings.overlay["unifi_user"] = ""
    settings.overlay["unifi_pass"] = ""
    settings.reload()
    client = ProtectClient(settings)
    jpeg = asyncio.run(client.fetch_stock_snapshot("64f8c2a91b3e4d001a2b3c4d", 1))
    assert jpeg == mock_jpeg("stock:64f8c2a91b3e4d001a2b3c4d:1")
    patrols = asyncio.run(client.list_patrols("64f8c2a91b3e4d001a2b3c4d"))
    assert "/ptz/snapshot/1" in patrols[0]["presets"][0]["snapshot"]


def test_mock_refresh_writes_unique_thumbs(tmp_path, monkeypatch) -> None:
    from app import thumbs
    from app.thumbs import read_thumb

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    settings = Settings()
    settings.overlay["unifi_host"] = ""
    settings.overlay["unifi_user"] = ""
    settings.overlay["unifi_pass"] = ""
    settings.reload()
    client = ProtectClient(settings)
    result = asyncio.run(client.refresh_preset_thumbs("64f8c2a91b3e4d001a2b3c4d", [1, 2]))
    assert result["mode"] == "mock"
    assert result["movedCamera"] is False
    one = read_thumb("64f8c2a91b3e4d001a2b3c4d", 1)
    two = read_thumb("64f8c2a91b3e4d001a2b3c4d", 2)
    assert one and two and one != two


def _live_client(handler):
    import httpx

    settings = Settings()
    settings.overlay["unifi_host"] = "192.0.2.10"
    settings.overlay["unifi_user"] = "pc"
    settings.overlay["unifi_pass"] = "pw"
    settings.reload()
    return ProtectClient(settings, transport=httpx.MockTransport(handler))


def test_recovery_code_403_does_not_log_in_again() -> None:
    import httpx
    import pytest
    from app.protect import ProtectError

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, headers={"x-csrf-token": "t"}, json={})
        return httpx.Response(403, json={"error": "forbidden"})

    client = _live_client(handler)
    with pytest.raises(ProtectError) as err:
        asyncio.run(client.camera_recovery_code("64f8c2a90000000000000001"))
    assert err.value.status_code == 403
    assert "lacks permission" in err.value.message
    assert seen.count("/api/auth/login") == 1
    assert seen[-1] == "/proxy/protect/api/devices/password/camera/64f8c2a90000000000000001"


def test_recovery_code_200_returns_value_without_logging_it() -> None:
    import httpx
    from app.logs import log as applog

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, headers={"x-csrf-token": "t"}, json={})
        return httpx.Response(200, json={"password": "ABCDEFGHIJKLMNOPQRST"})

    client = _live_client(handler)
    code = asyncio.run(client.camera_recovery_code("64f8c2a90000000000000001"))
    assert code == "ABCDEFGHIJKLMNOPQRST"
    assert "ABCDEFGHIJKLMNOPQRST" not in repr(applog.list())
