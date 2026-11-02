from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
LOCAL_YAML = ROOT / "config.local.yaml"

load_dotenv(ENV_PATH)


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None else value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_yaml() -> dict[str, Any]:
    if not LOCAL_YAML.exists():
        return {}
    data = yaml.safe_load(LOCAL_YAML.read_text()) or {}
    return data if isinstance(data, dict) else {}


# Used only when APP_SECRET is unset: random per process, so sessions end on restart.
_RUNTIME_SECRET = secrets.token_urlsafe(48)


class Settings:
    def __init__(self) -> None:
        self.overlay: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        file_cfg = _read_yaml()
        unifi = file_cfg.get("unifi") if isinstance(file_cfg.get("unifi"), dict) else {}
        appliance = file_cfg.get("appliance") if isinstance(file_cfg.get("appliance"), dict) else {}

        # No built-in login or session secret: the installer writes unique values to .env.
        # With APP_PASS unset, every sign-in is refused.
        self.app_user = _env("APP_USER", "")
        self.app_pass = _env("APP_PASS", "")
        self.app_secret = _env("APP_SECRET", "") or _RUNTIME_SECRET
        self.app_host = _env("APP_HOST", "127.0.0.1")
        self.app_port = int(_env("APP_PORT", "8080") or "8080")

        self.unifi_host = self._pick("unifi_host", "UNIFI_HOST", unifi.get("host", ""))
        self.unifi_port = int(self._pick("unifi_port", "UNIFI_PORT", str(unifi.get("port", 443))) or 443)
        self.unifi_api_key = self._pick("unifi_api_key", "UNIFI_API_KEY", unifi.get("api_key", ""))
        self.unifi_user = self._pick("unifi_user", "UNIFI_USER", unifi.get("username", ""))
        self.unifi_pass = self._pick("unifi_pass", "UNIFI_PASS", unifi.get("password", ""))
        tls_default = unifi.get("verify_tls", False)
        if "unifi_verify_tls" in self.overlay:
            self.unifi_verify_tls = bool(self.overlay["unifi_verify_tls"])
        elif os.environ.get("UNIFI_VERIFY_TLS") is not None:
            self.unifi_verify_tls = _env_bool("UNIFI_VERIFY_TLS", False)
        else:
            self.unifi_verify_tls = bool(tls_default)
        self.unifi_site = self._pick("unifi_site", "UNIFI_SITE", unifi.get("site", "default")) or "default"

        self.device_name = self._pick("device_name", "DEVICE_NAME", appliance.get("device_name", "PatrolCraft"))
        self.timezone = self._pick("timezone", "TZ", appliance.get("timezone", "America/Los_Angeles"))

    def _pick(self, overlay_key: str, env_name: str, file_value: Any) -> str:
        if overlay_key in self.overlay and self.overlay[overlay_key] is not None:
            return str(self.overlay[overlay_key])
        if os.environ.get(env_name) not in (None, ""):
            return os.environ[env_name]
        return "" if file_value is None else str(file_value)

    @property
    def login_ready(self) -> bool:
        return bool(self.app_user and self.app_pass)

    @property
    def configured(self) -> bool:
        return bool(self.unifi_host and self.unifi_user and self.unifi_pass)

    @property
    def base_url(self) -> str:
        host = self.unifi_host.strip()
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        return f"https://{host}:{self.unifi_port}"

    def public_dict(self) -> dict[str, Any]:
        key = self.unifi_api_key
        return {
            "host": self.unifi_host,
            "port": self.unifi_port,
            "site": self.unifi_site,
            "apiKeySet": bool(key),
            "apiKeyHint": f"••••{key[-4:]}" if len(key) >= 4 else ("••••" if key else ""),
            "username": self.unifi_user,
            "passwordSet": bool(self.unifi_pass),
            "verifyTls": self.unifi_verify_tls,
            "deviceName": self.device_name,
            "timezone": self.timezone,
            "configured": self.configured,
        }

    def apply_form(self, payload: dict[str, Any]) -> None:
        host = str(payload.get("host") or "").strip()
        port = int(payload.get("port") or 443)
        site = str(payload.get("site") or "default").strip() or "default"
        api_key = str(payload.get("apiKey") or "")
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        verify_tls = bool(payload.get("verifyTls"))
        device_name = str(payload.get("deviceName") or "PatrolCraft")
        timezone = str(payload.get("timezone") or "America/Los_Angeles")

        existing = _read_yaml()
        existing_unifi = existing.get("unifi") if isinstance(existing.get("unifi"), dict) else {}
        if not api_key:
            api_key = str(existing_unifi.get("api_key") or self.unifi_api_key or "")
        if not password:
            password = str(existing_unifi.get("password") or self.unifi_pass or "")

        self.overlay.update(
            {
                "unifi_host": host,
                "unifi_port": str(port),
                "unifi_api_key": api_key,
                "unifi_user": username,
                "unifi_pass": password,
                "unifi_verify_tls": verify_tls,
                "unifi_site": site,
                "device_name": device_name,
                "timezone": timezone,
            }
        )
        self.reload()

        LOCAL_YAML.write_text(
            yaml.safe_dump(
                {
                    "unifi": {
                        "host": host,
                        "port": port,
                        "site": site,
                        "api_key": api_key,
                        "username": username,
                        "password": password,
                        "verify_tls": verify_tls,
                    },
                    "appliance": {
                        "device_name": device_name,
                        "timezone": timezone,
                    },
                },
                sort_keys=False,
            )
        )
        try:
            os.chmod(LOCAL_YAML, 0o600)
        except OSError:
            pass


settings = Settings()
