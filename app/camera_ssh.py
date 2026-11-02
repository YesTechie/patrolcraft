"""Apply per-preset dwell/speed straight to a PTZ camera (no helper, no console SSH).

Flow (lab checks 2026-11-01 on Protect 7.3.70):
  1. PATCH /proxy/protect/api/cameras/{id} {"isSshEnabled": true} with the app's own session.
  2. Wait for camera port 22.
  3. Read the camera's recovery code from Protect (GET /proxy/protect/api/devices/password/camera/{id},
     the same call Protect's Manual Recovery page makes), ssh ubnt@camera with it,
     run ubnt_ipc_cli -T=ubnt_ptz Patrol set (proven 2026-09-21).
  4. Always PATCH isSshEnabled back to false and confirm.

The recovery code is fetched per Apply, held only in memory, passed to ssh via env,
never stored, logged or returned by the API.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

CAMERA_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")
HOST_RE = re.compile(r"[0-9A-Za-z:.\-]{1,253}")
PORT_WAIT_SECONDS = 30
SSH_ATTEMPTS = 5


def patrol_set_message(slot: int, points: list[dict[str, int]]) -> dict[str, Any]:
    wire = {
        str(i): {"preset": p["preset"], "dwellTime": p["dwellTime"], "moveSpeed": p["moveSpeed"]}
        for i, p in enumerate(points)
    }
    return {"functionName": "Patrol", "action": "set", "items": [{"index": int(slot), "points": wire}]}


def ipc_command(message: dict[str, Any]) -> str:
    payload = json.dumps(message, separators=(",", ":"))
    return "ubnt_ipc_cli -T=ubnt_ptz -r=1 -s -t=5000 -m=" + shlex.quote(payload)


def scrub(text: str, secret: str) -> str:
    if not text:
        return ""
    if secret and len(secret) >= 4:
        text = text.replace(secret, "[redacted]")
    return text


def wait_port(host: str, port: int = 22, timeout: float = PORT_WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(1)
    return False


def ssh_run(host: str, user: str, secret: str, remote: str, timeout: int = 25) -> subprocess.CompletedProcess:
    """System OpenSSH with SSH_ASKPASS. The code is passed via env, never argv or a file."""
    if secret and secret in remote:
        raise ValueError("Refusing a command that contains the recovery code.")
    target = f"{user}@[{host}]" if ":" in host else f"{user}@{host}"
    opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "PreferredAuthentications=password,keyboard-interactive",
        "-o", "PubkeyAuthentication=no",
        "-o", "NumberOfPasswordPrompts=1",
        "-o", "ConnectTimeout=5",
    ]
    with tempfile.TemporaryDirectory(prefix="pc-askpass-") as tmp:
        ask = Path(tmp) / "askpass.sh"
        ask.write_text("#!/bin/sh\nprintf '%s\\n' \"$PC_CAM_CODE\"\n")
        ask.chmod(0o700)
        env = {k: v for k, v in os.environ.items() if k not in {"SSHPASS", "PC_CAM_CODE"}}
        env.update({
            "PC_CAM_CODE": secret,
            "SSH_ASKPASS": str(ask),
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": env.get("DISPLAY", ":0"),
        })
        return subprocess.run(
            ["ssh", *opts, target, remote],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )


_RETRYABLE = re.compile(r"Connection refused|timed out|Connection reset|No route to host|Connection closed", re.I)


def _write_points_blocking(host: str, code: str, slot: int, points: list[dict[str, int]]) -> dict[str, Any]:
    if not wait_port(host):
        return {"ok": False, "step": "port22", "error": f"Camera port 22 did not open on {host}."}
    remote = ipc_command(patrol_set_message(slot, points))
    last: dict[str, Any] = {}
    for attempt in range(SSH_ATTEMPTS):
        try:
            cp = ssh_run(host, "ubnt", code, remote)
        except subprocess.TimeoutExpired:
            last = {"ok": False, "step": "ssh", "error": "SSH to the camera timed out."}
            continue
        out = scrub(cp.stdout, code)
        err = scrub(cp.stderr, code)
        if cp.returncode == 0:
            return {"ok": True, "step": "set", "stdout": out[:300]}
        if "Permission denied" in err:
            return {"ok": False, "step": "login", "error": "Camera rejected the recovery code Protect returned."}
        last = {"ok": False, "step": "set", "error": (err or out or f"ssh exit {cp.returncode}").strip()[:300]}
        if not _RETRYABLE.search(err or ""):
            break
        time.sleep(1)
    return last


async def apply_points_to_camera(protect: Any, camera_id: str, slot: int, points: list[dict[str, int]]) -> dict[str, Any]:
    """Enable SSH, write per-preset values, always disable SSH. Returns a secret-free result."""
    from .protect import camera_ip  # local import keeps module load light

    if not points:
        return {"ok": False, "error": "points must be a non-empty array"}
    if not protect.live:
        return {"ok": True, "mode": "mock", "note": "Mock mode: nothing was sent to a camera."}
    if not CAMERA_ID_RE.fullmatch(str(camera_id)):
        return {"ok": False, "error": "Bad camera id."}

    raw = await protect._raw_camera(camera_id)
    host = camera_ip(raw)
    if not host or not HOST_RE.fullmatch(host):
        return {"ok": False, "error": "Protect did not report an IP address for this camera."}

    from .protect import ProtectError

    try:
        code = await protect.camera_recovery_code(camera_id)
    except ProtectError as exc:
        # Stop before SSH is touched. 403 = account lacks permission (no re-login).
        return {"ok": False, "cameraId": str(camera_id), "status": exc.status_code, "error": exc.message}
    result: dict[str, Any] = {"ok": False, "cameraId": str(camera_id), "patrolSlot": int(slot), "host": host}
    try:
        on = await protect.set_camera_ssh(camera_id, True)
        result["sshOn"] = on
        if not on.get("ok"):
            result["error"] = "Protect did not turn camera SSH on."
        else:
            result.update(await asyncio.to_thread(_write_points_blocking, host, code, int(slot), points))
    except Exception as exc:  # noqa: BLE001 - always reach the finally
        result["ok"] = False
        result["error"] = scrub(str(exc), code)[:300] or type(exc).__name__
    finally:
        try:
            off = await protect.set_camera_ssh(camera_id, False)
        except Exception as exc:  # noqa: BLE001
            off = {"ok": False, "error": type(exc).__name__}
        result["sshOff"] = off
        if not off.get("ok") or off.get("isSshEnabled") is True:
            result["ok"] = False
            result["error"] = (result.get("error") + " ") if result.get("error") else ""
            result["error"] += "WARNING: camera SSH may still be on. Turn it off in Protect."
        code = ""
    return result
