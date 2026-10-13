"""Self-update from the project's GitHub Releases.

The packaged app checks the latest release, and if it is newer downloads the
archive for this build (Windows, macOS or Linux),
unpacks it next to the installed copy and hands over to a small helper
script. The script waits for OpenFTIR to exit, swaps the folders (keeping
the old copy until the new one is in place), reopens the app and tidies up.

Running from source (``pip install -e .``) never self-updates; use git.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openftir import __version__

REPO = "ryanewilliams/openftir"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

#: the folder (or .app) the helper script swaps in place of the running copy
_STAGING_DIR = ".openftir-update"


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str | None = None


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page_url: str
    assets: dict[str, Asset] = field(default_factory=dict)


# -- versions --------------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, ...]:
    """``"v0.2.10"`` -> ``(0, 2, 10)``; anything after the numbers is ignored."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# -- which build is this ------------------------------------------------------------------


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def asset_name(platform: str = sys.platform) -> str:
    """The release download that matches this build."""
    if platform == "win32":
        return "OpenFTIR-windows.zip"
    if platform == "darwin":
        return "OpenFTIR-macos.zip"
    return "OpenFTIR-linux.tar.gz"


def install_location(executable: str = sys.executable, platform: str = sys.platform) -> Path:
    """The installed copy the update replaces: the ``OpenFTIR`` folder, or
    ``OpenFTIR.app`` on macOS."""
    exe = Path(executable).resolve()
    if platform == "darwin":
        return exe.parents[2]  # OpenFTIR.app/Contents/MacOS/OpenFTIR
    return exe.parent


def why_not_self_update(location: Path | None = None) -> str | None:
    """None if this copy can update itself, otherwise a reason to show."""
    if not is_packaged():
        return "OpenFTIR is running from source; update it with git."
    location = location or install_location()
    if "/AppTranslocation/" in str(location):
        return "Move OpenFTIR into your Applications folder first, then open it from there."
    parent = location.parent
    if not (os.access(location, os.W_OK) and os.access(parent, os.W_OK)):
        return f"OpenFTIR can't write to {parent}, so it can't replace itself there."
    return None


# -- GitHub -------------------------------------------------------------------------------


def _request(url: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"OpenFTIR/{__version__}"})


def fetch_latest_release(timeout: float = 15.0) -> Release:
    """The newest published release (GitHub's API needs no login for a
    public repository)."""
    try:
        with urllib.request.urlopen(_request(LATEST_RELEASE_API, "application/vnd.github+json"), timeout=timeout) as resp:
            data = json.load(resp)
    except OSError as exc:
        raise UpdateError(f"Couldn't reach GitHub to check for updates ({exc}).") from exc
    return release_from_api(data)


def release_from_api(data: dict) -> Release:
    assets = {}
    for item in data.get("assets", []):
        digest = item.get("digest") or ""
        sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
        assets[item["name"]] = Asset(item["name"], item["browser_download_url"], int(item.get("size") or 0), sha256)
    return Release(
        version=str(data.get("tag_name") or "").lstrip("v"),
        notes=str(data.get("body") or "").strip(),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        assets=assets,
    )


# -- download + unpack ----------------------------------------------------------------------


def download(
    asset: Asset,
    dest_dir: Path,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download ``asset`` into ``dest_dir``, checking its size and (when
    GitHub provides one) its SHA-256."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / asset.name
    digest = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(_request(asset.url, "application/octet-stream"), timeout=timeout) as resp, open(path, "wb") as out:
            total = int(resp.headers.get("Content-Length") or asset.size or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("Download cancelled.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except OSError as exc:
        raise UpdateError(f"Download failed ({exc}).") from exc
    if asset.size and done != asset.size:
        raise UpdateError("The download was incomplete. Try again.")
    if asset.sha256 and digest.hexdigest() != asset.sha256.lower():
        raise UpdateError("The download is corrupted (checksum mismatch). Try again.")
    return path


def unpack(archive: Path, staging: Path, platform: str = sys.platform) -> Path:
    """Unpack ``archive`` into ``staging`` and return the new app inside it."""
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    name = archive.name.lower()
    if platform == "darwin":
        # ditto keeps the symlinks and permissions inside the .app bundle.
        subprocess.run(["ditto", "-x", "-k", str(archive), str(staging)], check=True)
        new = staging / "OpenFTIR.app"
    elif name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(staging)
        new = staging / "OpenFTIR"
    else:
        with tarfile.open(archive) as tf:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(staging, filter="data")
            else:  # Python < 3.12 without the filter backport
                tf.extractall(staging)
        new = staging / "OpenFTIR"
    if not new.exists():
        raise UpdateError(f"The download doesn't contain {new.name}.")
    return new


# -- the swap ----------------------------------------------------------------------------


def _batch_script(old: Path, new: Path, staging: Path, pid: int, exe_name: str) -> str:
    # Plain cmd.exe only, and no ( ) blocks around paths,
    # which break on folders like "Program Files (x86)". ping is the
    # portable sleep: timeout.exe refuses to run without a console.
    return "\r\n".join([
        "@echo off",
        f'set "OLD={old}"',
        f'set "NEW={new}"',
        f'set "BAK={old}.old"',
        f'set "STAGE={staging}"',
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL',
        "if errorlevel 1 goto gone",
        "ping -n 2 127.0.0.1 >NUL",
        "goto wait",
        ":gone",
        'if exist "%BAK%" rmdir /S /Q "%BAK%"',
        "set tries=0",
        ":moveold",
        'move "%OLD%" "%BAK%" >NUL 2>&1',
        "if not errorlevel 1 goto movenew",
        "set /a tries+=1",
        "if %tries% GEQ 30 goto relaunch",
        "ping -n 2 127.0.0.1 >NUL",
        "goto moveold",
        ":movenew",
        'move "%NEW%" "%OLD%" >NUL 2>&1',
        "if not errorlevel 1 goto done",
        'move "%BAK%" "%OLD%" >NUL 2>&1',
        "goto relaunch",
        ":done",
        'rmdir /S /Q "%BAK%"',
        ":relaunch",
        f'start "" "%OLD%\\{exe_name}"',
        'rmdir /S /Q "%STAGE%"',
        '(goto) 2>NUL & del "%~f0"',
        "",
    ])


def _shell_script(old: Path, new: Path, staging: Path, pid: int, relaunch: str) -> str:
    q = shlex.quote
    return "\n".join([
        "#!/bin/sh",
        f"OLD={q(str(old))}",
        f"NEW={q(str(new))}",
        'BAK="$OLD.old"',
        f"STAGE={q(str(staging))}",
        f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done",
        'rm -rf "$BAK"',
        'if mv "$OLD" "$BAK"; then',
        '  if mv "$NEW" "$OLD"; then rm -rf "$BAK"; else mv "$BAK" "$OLD"; fi',
        "fi",
        'rm -rf "$STAGE"',
        relaunch,
        'rm -f "$0"',
        "",
    ])


def write_swap_script(
    old: Path, new: Path, staging: Path, pid: int, platform: str = sys.platform, script_dir: Path | None = None
) -> list[str]:
    """Write the helper script and return the command that runs it."""
    script_dir = script_dir or Path(tempfile.mkdtemp(prefix="openftir-update-"))
    if platform == "win32":
        script = script_dir / "openftir-update.bat"
        text = _batch_script(old, new, staging, pid, "OpenFTIR.exe")
        try:
            import ctypes

            encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"  # what cmd.exe reads batch files as
        except (AttributeError, OSError):
            encoding = "mbcs"
        try:
            script.write_bytes(text.encode(encoding))
        except UnicodeEncodeError as exc:
            raise UpdateError("OpenFTIR's folder name has characters the Windows updater can't handle.") from exc
        return ["cmd.exe", "/c", str(script)]
    if platform == "darwin":
        relaunch = 'open "$OLD"'
    else:
        relaunch = 'nohup "$OLD/OpenFTIR" >/dev/null 2>&1 &'
    script = script_dir / "openftir-update.sh"
    script.write_text(_shell_script(old, new, staging, pid, relaunch))
    return ["/bin/sh", str(script)]


def launch_detached(command: list[str]) -> None:
    """Start the helper so it outlives this process."""
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        # Not the app's folder: Windows can't rename a folder that a running
        # process (the helper itself) has as its working directory.
        "cwd": tempfile.gettempdir(),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def prepare_update(
    release: Release,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[str]:
    """Download and unpack ``release`` beside the installed copy and write
    the helper script. Returns the command to run (with
    :func:`launch_detached`) just before the app quits."""
    location = install_location()
    reason = why_not_self_update(location)
    if reason:
        raise UpdateError(reason)
    asset = release.assets.get(asset_name())
    if asset is None:
        raise UpdateError(f"Release {release.version} has no {asset_name()} download.")
    # Staged on the same disk as the installed copy, so the swap is a rename.
    staging = location.parent / _STAGING_DIR
    archive = download(asset, Path(tempfile.mkdtemp(prefix="openftir-download-")), progress, cancel)
    try:
        new = unpack(archive, staging)
    finally:
        shutil.rmtree(archive.parent, ignore_errors=True)
    return write_swap_script(location, new, staging, os.getpid())
