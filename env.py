"""Environment resolution for DasTwitchy.

Reuses the existing twitch-bot venv (streamlink, demucs, torch) so we don't
need a second heavy install. Falls back to PATH binaries when present.
"""
import shutil
from pathlib import Path

VENV_PY = Path("/home/hatch/workspace/twitch-bot/venv/bin/python")


def demucs_cmd():
    if VENV_PY.exists():
        return [str(VENV_PY), "-m", "demucs"]
    d = shutil.which("demucs")
    if d:
        return [d]
    raise RuntimeError("demucs not installed")


def streamlink_cmd():
    if VENV_PY.exists():
        return [str(VENV_PY), "-m", "streamlink"]
    s = shutil.which("streamlink")
    if s:
        return [s]
    raise RuntimeError("streamlink not installed")
