from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .settings import ROOT

CACHE_DIR = ROOT / "data" / "preset-thumbs"

# Tiny valid JPEG (16x10 gray). Unique POV files add a COM marker after SOI.
_BASE_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010200000100010000fffe00104c61766336302e33312e31303200"
    "ffdb0043000810101310131616161616161a181a1b1b1b1a1a1a1a1b1b1b1d1d1d2222221d1d"
    "1d1b1b1d1d2020222225262523232223262628282830302e2e38383a454553ffc4004a000100"
    "00000000000000000000000000000001010000000000000000000000000000000010010000"
    "0000000000000000000000000000110100000000000000000000000000000000ffc0001108"
    "000a001003012200021100031100ffda000c03010002110311003f00000fffd9"
)

NO_INTEGRATION_KEY = (
    "Protect Integration API key is required to refresh snapshots. "
    "Set UNIFI_API_KEY or save it on the Config page."
)


def is_jpeg(data: bytes) -> bool:
    return isinstance(data, (bytes, bytearray)) and len(data) >= 4 and data[:2] == b"\xff\xd8"


def mock_jpeg(tag: str) -> bytes:
    """Distinct JPEG bytes per camera+slot so mock/live tests can SHA-compare POVs."""
    payload = f"PatrolCraft POV {tag}".encode("ascii", "replace")[:80]
    length = len(payload) + 2
    com = bytes([0xFF, 0xFE, (length >> 8) & 0xFF, length & 0xFF]) + payload
    return b"\xff\xd8" + com + _BASE_JPEG[2:]


def sanitize_camera_id(camera_id: str) -> str:
    text = "".join(ch for ch in str(camera_id) if ch.isalnum() or ch in "-_")
    return (text[:80] or "unknown")


def thumb_path(camera_id: str, slot: int) -> Path:
    return CACHE_DIR / sanitize_camera_id(camera_id) / f"{int(slot)}.jpg"


def thumb_mtime(camera_id: str, slot: int) -> Optional[int]:
    path = thumb_path(camera_id, slot)
    if not path.is_file():
        return None
    return int(path.stat().st_mtime)


def read_thumb(camera_id: str, slot: int) -> Optional[bytes]:
    path = thumb_path(camera_id, slot)
    if not path.is_file():
        return None
    data = path.read_bytes()
    return data if is_jpeg(data) else None


def write_thumb(camera_id: str, slot: int, data: bytes) -> Path:
    if not is_jpeg(data):
        raise ValueError("POV icon must be a JPEG")
    path = thumb_path(camera_id, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jpg.tmp")
    tmp.write_bytes(bytes(data))
    tmp.replace(path)
    return path


def annotate_patrols(camera_id: str, patrols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for patrol in patrols:
        for preset in patrol.get("presets") or []:
            try:
                slot = int(preset.get("slot"))
            except (TypeError, ValueError):
                preset["hasCache"] = False
                preset["thumbSource"] = "none"
                preset["hasThumb"] = False
                preset["thumbMtime"] = None
                continue
            mtime = thumb_mtime(camera_id, slot)
            if not preset.get("snapshot"):
                preset["snapshot"] = f"/cameras/{camera_id}/ptz/snapshot/{slot}"
            preset["hasCache"] = mtime is not None
            preset["thumbSource"] = "cache" if mtime is not None else "stock"
            preset["hasThumb"] = True
            preset["thumbMtime"] = mtime
    return patrols
