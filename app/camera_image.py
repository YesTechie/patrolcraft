from __future__ import annotations

from pathlib import Path
from typing import Any

# Product shots live at img/cameras/ and are served at /img/cameras/.
IMAGE_DIR = "/img/cameras"
DEFAULT_COLOR = "white"
DEFAULT_SLUG = "ai-ptz-precision"
KNOWN_COLORS = {"white"}
KNOWN_SLUGS = ("ai-ptz-precision", "ai-ptz-industrial", "g6-ptz", "g5-ptz")

# Precision and Industrial must be matched before generic AI PTZ.
# Keys are compared against marketName, then type, then platform.
MODEL_RULES = (
    ("ai-ptz-precision", ("ai ptz precision", "uvc ai ptz precision", "cv25z")),
    ("ai-ptz-industrial", ("ai ptz industrial", "uvc ai ptz industrial", "ai ptz", "uvc ai ptz")),
    ("g6-ptz", ("g6 ptz", "uvc g6 ptz")),
    ("g5-ptz", ("g5 ptz", "uvc g5 ptz")),
)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _match_text(text: str) -> str:
    if not text:
        return ""
    for slug, keys in MODEL_RULES:
        for key in keys:
            if text == key or key in text:
                return slug
    return ""


def resolve_model_slug(camera: dict[str, Any]) -> str:
    """Prefer marketName, then type, then platform. Unknown → Precision."""
    for field in ("marketName", "type", "platform"):
        slug = _match_text(_norm(camera.get(field)))
        if slug:
            return slug
    return DEFAULT_SLUG


def resolve_color(camera: dict[str, Any]) -> str:
    """Protect does not expose housing color. Default white.

    housingColor/color selects a future variant filename (e.g. black).
    Until that PNG ships, unknown colors fall back to white so tiles do not 404.
    """
    raw = camera.get("housingColor") or camera.get("color") or DEFAULT_COLOR
    color = _norm(raw).replace(" ", "")
    if color in KNOWN_COLORS:
        return color
    return DEFAULT_COLOR


def product_image_filename(camera: dict[str, Any]) -> str:
    """Return a filename under img/cameras/.

    imageOverride (or productImageOverride) wins and can point at a future
    black variant such as uvc-ai-ptz-precision-black.png.
    """
    override = camera.get("imageOverride") or camera.get("productImageOverride")
    if override:
        name = Path(str(override)).name.strip()
        if name:
            return name if "." in name else f"{name}.png"
    slug = resolve_model_slug(camera)
    color = resolve_color(camera)
    return f"uvc-{slug}-{color}.png"


def product_image_url(camera: dict[str, Any]) -> str:
    return f"{IMAGE_DIR}/{product_image_filename(camera)}"
