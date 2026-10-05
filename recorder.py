"""Streamlink recording for DasTwitchy: live checks, start/stop, stall guard, adoption."""
import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from env import streamlink_cmd

FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"


def safe_name(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def is_live(streamer, timeout=45):
    """True if the streamer is currently live (no API keys needed)."""
    try:
        cmd = streamlink_cmd()
    except RuntimeError:
        return False
    try:
        r = subprocess.run(
            cmd + ["--json", f"https://www.twitch.tv/{streamer}"],
            capture_output=True, text=True, timeout=timeout,
        )
        d = json.loads(r.stdout or "{}")
        return bool(d.get("streams"))
    except Exception:
        return False


class Recording:
    def __init__(self, streamer, rec_dir):
        self.streamer = streamer
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.base = f"{safe_name(streamer)}_{ts}"
        self.path = Path(rec_dir) / f"{self.base}.ogg"
        self.proc = None
        self.started_at = time.time()
        self.last_size = 0
        self.last_grew = time.time()

    def start(self, log):
        cmd = streamlink_cmd()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        full = cmd + [
            "--force",
            "--output", str(self.path),
            "--ffmpeg-ffmpeg", FFMPEG,
            "--ffmpeg-fout", "ogg",
            "--ffmpeg-copyts",
            f"https://www.twitch.tv/{self.streamer}",
            "audio_only",
        ]
        log(f"[{self.base}] recording: {' '.join(full)}")
        self.proc = subprocess.Popen(
            full, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.started_at = time.time()
        self.last_grew = time.time()

    def poll(self):
        return None if self.proc is None else self.proc.poll()

    def healthy(self, stall_secs, log):
        """False if the process died or the file stopped growing (stall)."""
        if self.poll() is not None:
            return False
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        now = time.time()
        if size > self.last_size:
            self.last_size = size
            self.last_grew = now
        if now - self.last_grew > stall_secs:
            log(f"[{self.base}] stall: no growth for {stall_secs}s, killing")
            self.stop()
            return False
        return True

    def stop(self):
        if self.proc and self.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=15)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None


class AdoptedRecording:
    """A streamlink process started outside this bot (e.g. before a restart)."""

    def __init__(self, base, path, proc):
        self.base = base
        self.path = Path(path)
        self.proc = proc
        self.streamer = base.rsplit("_", 1)[0]
        self.started_at = time.time()
        self.last_size = 0
        self.last_grew = time.time()

    def poll(self):
        return None if self.proc is None else self.proc.poll()

    def healthy(self, stall_secs, log):
        if self.poll() is not None:
            return False
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        now = time.time()
        if size > self.last_size:
            self.last_size = size
            self.last_grew = now
        if now - self.last_grew > stall_secs:
            log(f"[{self.base}] stall: no growth for {stall_secs}s, killing")
            self.stop()
            return False
        return True

    def stop(self):
        if self.proc and self.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=15)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None


def find_external_recording(rec_dir, streamer, log):
    """Adopt a streamlink recording for this streamer started outside the bot."""
    try:
        r = subprocess.run(["pgrep", "-af", "streamlink"], capture_output=True,
                           text=True, timeout=10)
    except Exception:
        return None
    for line in (r.stdout or "").splitlines():
        if f"twitch.tv/{streamer}" not in line:
            continue
        m = re.search(r"--output\s+(\S+)", line)
        pid = line.split()[0] if line.split() else None
        if not m or not pid or not pid.isdigit():
            continue
        out = Path(m.group(1))
        if out.parent != Path(rec_dir):
            continue
        base = out.stem
        try:
            import psutil  # noqa
            proc = psutil.Process(int(pid))
        except Exception:
            class _P:
                def __init__(self, pid):
                    self._pid = int(pid)

                def poll(self):
                    try:
                        subprocess.run(["kill", "-0", str(self._pid)],
                                       capture_output=True, timeout=5)
                        return None
                    except Exception:
                        return 0

                def terminate(self):
                    subprocess.run(["kill", str(self._pid)], capture_output=True)

                def wait(self, timeout=None):
                    pass

                def kill(self):
                    subprocess.run(["kill", "-9", str(self._pid)], capture_output=True)

            proc = _P(pid)
        log(f"[{base}] adopting external recording (pid {pid})")
        return AdoptedRecording(base, out, proc)
    return None
