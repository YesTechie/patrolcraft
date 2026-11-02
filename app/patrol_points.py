from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .thumbs import sanitize_camera_id

POINTS_DIR = Path(__file__).resolve().parent.parent / "data" / "patrol-points"

# Stock Protect PATCH .../ptz/patrol/{slot} is tour-wide only (presetDurationSeconds /
# presetMovementSpeed apply to every pose). Per-point dwell/speed lives in this sidecar.
# Save writes the sidecar only. Apply (app/camera_ssh.py) writes per-preset values
# on the camera itself. Do not send per-pose values on the stock patrol PATCH.
# LIVE Protect bounds: dwell 0–127 seconds (0 allowed), speed 0–1000.
DWELL_MIN = 0
DWELL_MAX = 127
SPEED_MIN = 0
SPEED_MAX = 1000
PRESET_MIN = 0
PRESET_MAX = 254
DWELL_SPEED_NOTE = (
    "Per-preset dwell/speed saved locally. Apply writes them to the camera."
)
PTZ_NOTE = (
    "P/T/Z/F are live GET .../ptz/preset values. Private PATCH does not rewrite pose."
)


def points_path(camera_id: str, patrol_slot: int) -> Path:
    return POINTS_DIR / sanitize_camera_id(camera_id) / f"{int(patrol_slot)}.json"


def read_points(camera_id: str, patrol_slot: int) -> Optional[dict[str, Any]]:
    path = points_path(camera_id, patrol_slot)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(lo, min(hi, number))


def normalize_point(item: dict[str, Any], *, tour_dwell: int = 0, tour_speed: int = 400) -> dict[str, Any]:
    try:
        slot = int(item.get("slot"))
    except (TypeError, ValueError):
        slot = 0
    return {
        "slot": slot,
        "presetDurationSeconds": _clamp_int(item.get("presetDurationSeconds"), DWELL_MIN, DWELL_MAX, tour_dwell),
        "presetMovementSpeed": _clamp_int(item.get("presetMovementSpeed"), SPEED_MIN, SPEED_MAX, tour_speed),
    }


def write_points(camera_id: str, patrol_slot: int, points: list[dict[str, Any]], *, tour_dwell: int = 0, tour_speed: int = 400) -> dict[str, Any]:
    payload = {
        "cameraId": str(camera_id),
        "patrolSlot": int(patrol_slot),
        "points": [normalize_point(item, tour_dwell=tour_dwell, tour_speed=tour_speed) for item in points],
    }
    path = points_path(camera_id, patrol_slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)
    return payload


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) is not None:
            return item.get(key)
    return None


def live_set_points_body(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Clamp Edit-form points to camera limits (preset 0-254, dwell 0-127, speed 0-1000)."""
    mapped: list[dict[str, int]] = []
    for item in points:
        if not isinstance(item, dict):
            continue
        mapped.append(
            {
                "preset": _clamp_int(_first_present(item, "preset", "slot"), PRESET_MIN, PRESET_MAX, 0),
                "dwellTime": _clamp_int(
                    _first_present(item, "dwellTime", "presetDurationSeconds"),
                    DWELL_MIN,
                    DWELL_MAX,
                    0,
                ),
                "moveSpeed": _clamp_int(
                    _first_present(item, "moveSpeed", "presetMovementSpeed"),
                    SPEED_MIN,
                    SPEED_MAX,
                    0,
                ),
            }
        )
    return {"points": mapped}


def apply_preset_ptz(camera_id: str, points: list[dict[str, Any]]) -> dict[str, Any]:
    """LIVE proven (lab): private PATCH does not rewrite pose.

    Display GET .../ptz/preset ptz.{pan,tilt,zoom,focus} as read-only. Do not
    PATCH preset ptz over REST.
    """
    _ = (camera_id, points)
    return {"saved": False, "writable": False, "reason": "Private PATCH does not rewrite pose."}


def save_patrol_points(
    camera_id: str,
    patrol_slot: int,
    points: list[dict[str, Any]],
    *,
    tour_dwell: int = 0,
    tour_speed: int = 400,
) -> dict[str, Any]:
    sidecar = write_points(camera_id, patrol_slot, points, tour_dwell=tour_dwell, tour_speed=tour_speed)
    ptz = apply_preset_ptz(camera_id, points)
    notes = [DWELL_SPEED_NOTE]
    if not ptz.get("saved"):
        notes.append(PTZ_NOTE)
    return {
        "ok": True,
        "cameraId": str(camera_id),
        "patrolSlot": int(patrol_slot),
        "points": sidecar["points"],
        "dwellSpeed": {"saved": "sidecar", "applied": False},
        "ptz": ptz,
        "note": DWELL_SPEED_NOTE,
        "notes": notes,
    }


def annotate_patrol_points(camera_id: str, patrols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for patrol in patrols:
        try:
            patrol_slot = int(patrol.get("slot") or 0)
        except (TypeError, ValueError):
            patrol_slot = 0
        try:
            tour_dwell = int(patrol.get("presetDurationSeconds", 0) or 0)
        except (TypeError, ValueError):
            tour_dwell = 0
        try:
            tour_speed = int(patrol.get("presetMovementSpeed", 400) or 400)
        except (TypeError, ValueError):
            tour_speed = 400
        sidecar = read_points(camera_id, patrol_slot)
        by_slot: dict[int, dict[str, Any]] = {}
        if sidecar:
            for item in sidecar.get("points") or []:
                try:
                    by_slot[int(item.get("slot"))] = item
                except (TypeError, ValueError):
                    continue
        for preset in patrol.get("presets") or []:
            try:
                slot = int(preset.get("slot"))
            except (TypeError, ValueError):
                continue
            hit = by_slot.get(slot)
            if hit:
                preset["presetDurationSeconds"] = _clamp_int(hit.get("presetDurationSeconds"), DWELL_MIN, DWELL_MAX, tour_dwell)
                preset["presetMovementSpeed"] = _clamp_int(hit.get("presetMovementSpeed"), SPEED_MIN, SPEED_MAX, tour_speed)
                preset["dwellSource"] = "sidecar"
            else:
                preset["presetDurationSeconds"] = tour_dwell
                preset["presetMovementSpeed"] = tour_speed
                preset["dwellSource"] = "tour"
        patrol["pointsSidecar"] = sidecar is not None
    return patrols
