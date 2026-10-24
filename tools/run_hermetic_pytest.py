"""Run pytest without workstation credentials, dotenv or external networking.

Usage: python tools/run_hermetic_pytest.py tests/test_track9_api_routes.py -q

No synthetic provider configuration is supplied here: tests own their fixtures.
Loopback HTTP is allowed for route tests; attempted external access fails the
run even if application code catches the network exception. Runtime paths are
isolated from the operator's productions, browser profile and OAuth files.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PREFIXES = (
    "AVM_", "ALIBABA_", "DASHSCOPE_", "CODECRAFT_", "GEMINI_",
    "OPENROUTER_", "OPENAI_", "TIKTOK_", "YOUTUBE_", "GOOGLE_",
    "TTS_", "LLM_", "CDP_", "FLOW_", "CHROME_", "VOICE_",
    "VIDEO_", "MUSIC_", "CAPTION_", "DEFAULT_LLM_",
)
CONFIG_NAMES = {
    "APP_ENV", "LOG_LEVEL", "FFMPEG_PATH", "FFPROBE_PATH",
    "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
}


def _is_loopback(host: object) -> bool:
    text = str(host).strip().lower()
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _make_media_tools_discoverable() -> None:
    """Use installed local tools without reading the operator's .env file."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    for executable in sorted(packages.glob("Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe")):
        if executable.with_name("ffprobe.exe").is_file():
            os.environ["PATH"] = str(executable.parent) + os.pathsep + os.environ.get("PATH", "")
            return


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    os.chdir(REPO_ROOT)
    for key in list(os.environ):
        if key in CONFIG_NAMES or key.startswith(CONFIG_PREFIXES):
            os.environ.pop(key, None)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    _make_media_tools_discoverable()

    import dotenv
    import dotenv.main
    import pytest
    import requests

    blocked: list[str] = []

    def check_host(host: object) -> None:
        if not _is_loopback(host):
            # Record hosts only, never credentials, headers, prompts or URLs.
            safe_host = re.sub(r"[^a-zA-Z0-9.:-]", "?", str(host))[:100]
            blocked.append(safe_host)
            raise AssertionError(f"hermetic pytest forbids external network access: {safe_host}")

    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_request = requests.sessions.Session.request

    def getaddrinfo(host, *args, **kwargs):
        check_host(host)
        return original_getaddrinfo(host, *args, **kwargs)

    def connect(sock, address):
        if isinstance(address, tuple):
            check_host(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple):
            check_host(address[0])
        return original_connect_ex(sock, address)

    def sendto(sock, data, *args):
        address = args[-1]
        if isinstance(address, tuple):
            check_host(address[0])
        return original_sendto(sock, data, *args)

    def request(session, method, url, *args, **kwargs):
        check_host(urlsplit(str(url)).hostname)
        return original_request(session, method, url, *args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="avm-hermetic-") as scratch, pytest.MonkeyPatch.context() as patch:
        # python-dotenv 1.1.0 does not implement PYTHON_DOTENV_DISABLED.
        # Patch before importing app.config so no .env is ever parsed/loaded.
        patch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
        patch.setattr(dotenv.main, "load_dotenv", lambda *args, **kwargs: False)
        patch.setattr(dotenv.main.DotEnv, "set_as_environment_variables", lambda self: False)
        patch.setattr(socket, "getaddrinfo", getaddrinfo)
        patch.setattr(socket.socket, "connect", connect)
        patch.setattr(socket.socket, "connect_ex", connect_ex)
        patch.setattr(socket.socket, "sendto", sendto)
        patch.setattr(requests.sessions.Session, "request", request)

        from app import config

        root = Path(scratch)
        patch.setattr(config, "PROJECT_ROOT", root)
        paths = {
            "PROJECTS_DIR": root / "projects",
            "OUTPUT_DIR": root / "output",
            "ASSETS_DIR": root / "assets",
            "LOGS_DIR": root / "logs",
            "CREDENTIALS_DIR": root / "credentials",
            "PRODUCTIONS_DIR": root / "productions",
        }
        for name, path in paths.items():
            path.mkdir(parents=True)
            patch.setattr(config, name, path)
        patch.setattr(config, "REQUIRED_DIRS", list(paths.values()))

        print("Hermetic pytest: dotenv disabled; inherited AVM/provider config removed;")
        print("runtime/credential paths isolated; external network blocked; loopback allowed.")
        try:
            result = pytest.main(sys.argv[1:] or ["tests"])
        finally:
            # File handlers otherwise keep Windows scratch logs open on cleanup.
            logging.shutdown()
        print(f"Hermetic network guard: {len(blocked)} external attempts.")
        if blocked:
            print("Blocked hosts: " + ", ".join(sorted(set(blocked))))
            return 1
        return int(result)


if __name__ == "__main__":
    raise SystemExit(main())