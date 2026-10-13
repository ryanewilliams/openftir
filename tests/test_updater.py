import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

from openftir import updater


def test_version_comparison():
    assert updater.parse_version("v0.2.10") == (0, 2, 10)
    assert updater.is_newer("0.2.0", "0.1.0")
    assert updater.is_newer("v0.10.0", "0.9.9")
    assert updater.is_newer("1.0", "0.9.9")
    assert not updater.is_newer("0.1.0", "0.1.0")
    assert not updater.is_newer("0.1", "0.1.0")
    assert not updater.is_newer("garbage", "0.1.0")


def test_asset_name_matches_each_build():
    assert updater.asset_name("win32", (3, 12)) == "OpenFTIR-windows.zip"
    assert updater.asset_name("win32", (3, 8), bits=32) == "OpenFTIR-win7-32bit.zip"
    assert updater.asset_name("win32", (3, 8), bits=64) == "OpenFTIR-win7-64bit.zip"
    assert updater.asset_name("darwin", (3, 12)) == "OpenFTIR-macos.zip"
    assert updater.asset_name("linux", (3, 12)) == "OpenFTIR-linux.tar.gz"


def test_install_location():
    assert updater.install_location("/Applications/OpenFTIR.app/Contents/MacOS/OpenFTIR", "darwin") == Path(
        "/Applications/OpenFTIR.app"
    ).resolve()
    assert updater.install_location("/opt/OpenFTIR/OpenFTIR", "linux") == Path("/opt/OpenFTIR").resolve()


def test_release_from_github_api():
    release = updater.release_from_api({
        "tag_name": "v0.2.0",
        "body": "Fixes typing on macOS.",
        "html_url": "https://github.com/x/y/releases/tag/v0.2.0",
        "assets": [{
            "name": "OpenFTIR-macos.zip",
            "browser_download_url": "https://example.invalid/OpenFTIR-macos.zip",
            "size": 123,
            "digest": "sha256:" + "ab" * 32,
        }],
    })
    assert release.version == "0.2.0"
    asset = release.assets["OpenFTIR-macos.zip"]
    assert asset.size == 123 and asset.sha256 == "ab" * 32


def test_running_from_source_never_self_updates():
    assert updater.why_not_self_update() is not None


def _fake_bundle(root: Path, marker: str) -> Path:
    app = root / "OpenFTIR"
    (app / "_internal").mkdir(parents=True)
    (app / "_internal" / "version.txt").write_text(marker)
    return app


def test_unpack_zip_and_tarball(tmp_path):
    src = _fake_bundle(tmp_path / "src", "new")
    zip_path = tmp_path / "OpenFTIR-windows.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in src.rglob("*"):
            zf.write(f, f.relative_to(src.parent))
    new = updater.unpack(zip_path, tmp_path / "stage1", platform="win32")
    assert (new / "_internal" / "version.txt").read_text() == "new"

    tar_path = tmp_path / "OpenFTIR-linux.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(src, arcname="OpenFTIR")
    new = updater.unpack(tar_path, tmp_path / "stage2", platform="linux")
    assert (new / "_internal" / "version.txt").read_text() == "new"


def _wait_for(predicate, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.2)
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX helper script")
def test_shell_swap_script_replaces_the_app_and_relaunches(tmp_path):
    install = tmp_path / "Apps"
    old = _fake_bundle(install, "old")
    staging = install / ".openftir-update"
    new = _fake_bundle(staging, "new")
    relaunched = tmp_path / "relaunched"
    launcher = new / "OpenFTIR"
    launcher.write_text(f"#!/bin/sh\necho yes > {relaunched}\n")
    launcher.chmod(0o755)

    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    command = updater.write_swap_script(old, new, staging, running.pid, platform="linux", script_dir=tmp_path)
    helper = subprocess.Popen(command)
    running.wait()  # reap it, as the OS does for a real app, or kill -0 still sees it
    assert helper.wait(timeout=60) == 0

    assert (old / "_internal" / "version.txt").read_text() == "new"
    assert not (install / "OpenFTIR.old").exists()
    assert not staging.exists()
    assert _wait_for(relaunched.exists)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows helper script")
def test_batch_swap_script_replaces_the_app(tmp_path):
    install = tmp_path / "Program Files (x86) test"
    old = _fake_bundle(install, "old")
    staging = install / ".openftir-update"
    new = _fake_bundle(staging, "new")
    # Any real program will do as the relaunched "OpenFTIR.exe".
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "where.exe", new / "OpenFTIR.exe")

    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    command = updater.write_swap_script(old, new, staging, running.pid, platform="win32", script_dir=tmp_path)
    helper = subprocess.Popen(command, cwd=tmp_path)
    running.wait()
    helper.wait(timeout=120)

    assert (old / "_internal" / "version.txt").read_text() == "new"
    assert not (install / "OpenFTIR.old").exists()
    assert not staging.exists()
