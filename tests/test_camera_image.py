from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.camera_image import product_image_filename, product_image_url, resolve_model_slug
from app.main import app
from app.protect import ProtectClient, normalize_camera
from app.settings import ROOT, Settings

WHITE = {
    "precision": "uvc-ai-ptz-precision-white.png",
    "industrial": "uvc-ai-ptz-industrial-white.png",
    "g6": "uvc-g6-ptz-white.png",
    "g5": "uvc-g5-ptz-white.png",
}


def test_match_prefers_market_name_then_type_then_platform() -> None:
    assert resolve_model_slug({"marketName": "AI PTZ Precision", "type": "UVC G5 PTZ", "platform": "sav5444"}) == "ai-ptz-precision"
    assert resolve_model_slug({"marketName": "", "type": "UVC G5 PTZ", "platform": "cv25z"}) == "g5-ptz"
    assert resolve_model_slug({"marketName": "", "type": "", "platform": "cv25z"}) == "ai-ptz-precision"


def test_precision_not_confused_with_industrial() -> None:
    assert product_image_filename({"marketName": "AI PTZ Precision"}) == WHITE["precision"]
    assert product_image_filename({"type": "UVC AI PTZ Precision"}) == WHITE["precision"]
    assert product_image_filename({"platform": "cv25z"}) == WHITE["precision"]
    assert product_image_filename({"marketName": "AI PTZ Industrial"}) == WHITE["industrial"]
    assert product_image_filename({"type": "UVC AI PTZ Industrial"}) == WHITE["industrial"]
    assert product_image_filename({"marketName": "AI PTZ", "type": "UVC AI PTZ"}) == WHITE["industrial"]


def test_g5_g6_and_unknown_fallback() -> None:
    assert product_image_filename({"type": "UVC G5 PTZ"}) == WHITE["g5"]
    assert product_image_filename({"marketName": "G5 PTZ"}) == WHITE["g5"]
    assert product_image_filename({"type": "UVC G6 PTZ"}) == WHITE["g6"]
    assert product_image_filename({"marketName": "G6 PTZ"}) == WHITE["g6"]
    assert product_image_filename({"name": "Mystery"}) == WHITE["precision"]
    assert product_image_filename({"type": "UVC G4 PTZ"}) == WHITE["precision"]


def test_live_camera_fields() -> None:
    camera = normalize_camera(
        {
            "id": "cam-1",
            "name": "Yard East",
            "marketName": "AI PTZ Precision",
            "type": "UVC AI PTZ Precision",
            "platform": "cv25z",
            "state": "CONNECTED",
            "featureFlags": {"isPtz": True},
        }
    )
    assert camera["marketName"] == "AI PTZ Precision"
    assert camera["platform"] == "cv25z"
    assert camera["productImage"] == "/img/cameras/" + WHITE["precision"]


def test_optional_override_and_future_black_filename() -> None:
    assert product_image_filename({"type": "UVC G5 PTZ", "imageOverride": "uvc-g5-ptz-black.png"}) == "uvc-g5-ptz-black.png"
    assert product_image_filename({"imageOverride": "img/cameras/uvc-ai-ptz-precision-black.png"}) == "uvc-ai-ptz-precision-black.png"
    assert product_image_filename({"type": "UVC G5 PTZ", "housingColor": "black"}) == WHITE["g5"]
    assert product_image_url({"type": "UVC G5 PTZ"}).startswith("/img/cameras/")


def test_assets_on_disk() -> None:
    folder = ROOT / "img" / "cameras"
    for name in (*WHITE.values(), "camera-offline-silhouette.png"):
        path = folder / name
        assert path.is_file()
        assert path.stat().st_size > 1000


def test_assets_served() -> None:
    with TestClient(app) as client:
        for name in WHITE.values():
            response = client.get("/img/cameras/" + name)
            assert response.status_code == 200
            assert "png" in response.headers.get("content-type", "").lower()


def test_mock_cameras_map_each_model() -> None:
    settings = Settings()
    settings.overlay["unifi_host"] = ""
    settings.overlay["unifi_user"] = ""
    settings.overlay["unifi_pass"] = ""
    settings.reload()
    cameras = asyncio.run(ProtectClient(settings).list_cameras())
    by_name = {camera["name"]: camera for camera in cameras}
    assert by_name["Yard East"]["productImage"].endswith(WHITE["g5"])
    assert by_name["Parking Lot"]["productImage"].endswith(WHITE["g6"])
    assert by_name["Front Gate"]["state"] == "DISCONNECTED"
    assert by_name["Gate West"]["productImage"].endswith(WHITE["precision"])
    assert by_name["Gate West"]["marketName"] == "AI PTZ Precision"
    assert by_name["Gate West"]["platform"] == "cv25z"
    assert by_name["Shop Door"]["productImage"].endswith(WHITE["industrial"])


def test_js_mapper_mirrors_python_rules() -> None:
    js = Path(ROOT / "js" / "app.js").read_text()
    assert "marketName" in js
    assert '["ai-ptz-precision", ["ai ptz precision", "uvc ai ptz precision", "cv25z"]]' in js
    assert '["ai-ptz-industrial", ["ai ptz industrial", "uvc ai ptz industrial", "ai ptz", "uvc ai ptz"]]' in js
    assert '["g6-ptz", ["g6 ptz", "uvc g6 ptz"]]' in js
    assert '["g5-ptz", ["g5 ptz", "uvc g5 ptz"]]' in js
    assert "is-offline" in js
    assert "camera-offline-silhouette.png" in js
    assert "data-toggle-patrol" in js
    assert "renderCameraTile" in js
    assert "renderCameraGroup" in js
    assert "camera-group is-open" in js
    assert "camera-accordion" in js
    assert "<span>Type</span>" in js
    assert "<span>Ip</span>" in js
    assert "<span>Mac</span>" in js
    assert 'labeled("Type"' not in js
    assert 'labeled("Ip"' not in js
    assert 'labeled("Mac"' not in js
    assert '<span>Model</span>' not in js
    assert '<span>Id</span>' not in js
    assert "cameraMac" in js
    assert "displayMeta" in js
