from __future__ import annotations

import hashlib

from app.thumbs import annotate_patrols, is_jpeg, mock_jpeg, read_thumb, sanitize_camera_id, write_thumb


def test_mock_jpegs_are_unique_per_slot() -> None:
    a = mock_jpeg("cam:1")
    b = mock_jpeg("cam:2")
    assert is_jpeg(a) and is_jpeg(b)
    assert a != b
    assert hashlib.sha256(a).hexdigest() != hashlib.sha256(b).hexdigest()


def test_sanitize_camera_id_blocks_path_escape() -> None:
    assert ".." not in sanitize_camera_id("../etc/passwd")
    assert "/" not in sanitize_camera_id("a/b")
    assert sanitize_camera_id("") == "unknown"


def test_write_read_roundtrip(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    payload = mock_jpeg("cam:3")
    write_thumb("64f8c2a91b3e4d001a2b3c4d", 3, payload)
    assert read_thumb("64f8c2a91b3e4d001a2b3c4d", 3) == payload
    assert read_thumb("64f8c2a91b3e4d001a2b3c4d", 9) is None


def test_annotate_patrols_has_thumb(tmp_path, monkeypatch) -> None:
    from app import thumbs

    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "preset-thumbs")
    write_thumb("cam-1", 2, mock_jpeg("cam-1:2"))
    patrols = [
        {
            "id": "p",
            "presets": [
                {"slot": 1, "name": "A"},
                {"slot": 2, "name": "B"},
            ],
        }
    ]
    annotated = annotate_patrols("cam-1", patrols)
    assert annotated[0]["presets"][0]["hasCache"] is False
    assert annotated[0]["presets"][0]["thumbSource"] == "stock"
    assert annotated[0]["presets"][0]["hasThumb"] is True
    assert annotated[0]["presets"][0]["snapshot"].endswith("/ptz/snapshot/1")
    assert annotated[0]["presets"][1]["hasCache"] is True
    assert annotated[0]["presets"][1]["thumbSource"] == "cache"
    assert annotated[0]["presets"][1]["thumbMtime"]
