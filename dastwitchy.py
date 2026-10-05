"""DasTwitchy — fresh rebuild of the Twitch recorder bot.

Watches armed streamers, records with streamlink, then:
  Demucs vocal removal -> music-only OGG -> size-capped parts -> Google Drive.

State (all under ./state/):
  processing_queue.json  FIFO of finished recordings awaiting processing
  done/<base>.json       per-recording result (parts, Drive links, error)
  reported.json          bases already reported to Eric (read by the notifier)

Recording is gated by config["record"]; with it off the bot only processes
whatever is enqueued. This lets the new bot be tested while the old one
is still live (no double-recording).
"""
import json
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import drive as drive_mod
import processor
import recorder
from recorder import Recording, find_external_recording, is_live
from state import State

CFG = json.loads((HERE / "config.json").read_text())
REC_DIR = HERE / "recordings"
WORK_DIR = HERE / "work"
LOG_FILE = HERE / "dastwitchy.log"

state = State(HERE / "state")
STOP = False


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def on_signal(signum, frame):
    global STOP
    STOP = True
    log("shutdown signal received")


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)


def handle_finished(rec):
    """A recording ended. Resume if still live, else finalize -> enqueue."""
    base = rec.base
    try:
        size = rec.path.stat().st_size
    except OSError:
        size = 0
    mins = size / 1e6
    log(f"[{base}] recording ended ({mins:.1f} MB)")

    if size < 200_000:  # <200KB: too short to be a real set
        log(f"[{base}] too short, discarding")
        try:
            rec.path.unlink(missing_ok=True)
        except OSError:
            pass
        return

    if is_live(rec.streamer):
        log(f"[{base}] streamer still live, will resume with a new file")
        return  # watcher starts a fresh Recording next loop

    log(f"[{base}] stream over, queued for processing")
    state.enqueue(rec.path)


def process_one(path_str):
    src = Path(path_str)
    base = src.stem
    log(f"[{base}] processing {src.name}")
    t0 = time.time()
    done = {
        "streamer": base.rsplit("_", 1)[0],
        "base": base,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "original_link": None,
        "parts": [],
        "part_links": [],
        "error": None,
        "elapsed_secs": 0,
    }
    try:
        if not drive_mod.connected():
            raise RuntimeError("Drive not connected")
        orig_id, parts_id = drive_mod.ensure_tree(
            CFG["drive_root"], CFG["drive_originals"], CFG["drive_parts"]
        )

        log(f"[{base}] uploading original...")
        fid, err = drive_mod.upload(src, orig_id)
        if err:
            raise RuntimeError(f"original upload failed: {err}")
        done["original_link"] = fid
        log(f"[{base}] original uploaded")

        log(f"[{base}] starting demucs (CPU, {CFG['demucs_chunk_secs']}s chunks)...")
        res = processor.process_recording(src, WORK_DIR, CFG, log)

        log(f"[{base}] uploading {len(res['parts'])} part(s)...")
        up_dir = drive_mod._find_folder(base, parts_id) or drive_mod._create_folder(base, parts_id)
        if not up_dir:
            raise RuntimeError("could not create Drive parts folder")
        for i, part in enumerate(res["parts"], 1):
            fid, err = drive_mod.upload(part, up_dir, name=part.name)
            if err:
                raise RuntimeError(f"part upload failed ({part.name}): {err}")
            done["parts"].append(part.name)
            done["part_links"].append(fid)
            log(f"[{base}] uploaded {i}/{len(res['parts'])} parts")
    except Exception as e:
        done["error"] = str(e)[:500]
        log(f"[{base}] ERROR: {done['error']}")
        traceback.print_exc()
    finally:
        done["elapsed_secs"] = round(time.time() - t0, 1)
        state.write_done(base, done)
        log(f"[{base}] done: error={done['error']!r}")


def watch_loop(active):
    """One pass of the watch loop; active maps streamer -> Recording."""
    for streamer in CFG["streamers"]:
        rec = active.get(streamer)

        # adopt externally-started recordings (e.g. after a restart)
        if rec is None and CFG.get("record"):
            ext = find_external_recording(REC_DIR, streamer, log)
            if ext:
                active[streamer] = ext
                rec = ext

        if rec is not None:
            if rec.healthy(CFG["stall_timeout_secs"], log):
                continue
            # unhealthy or finished
            active.pop(streamer, None)
            handle_finished(rec)
            rec = None

        if rec is None and CFG.get("record"):
            if is_live(streamer):
                try:
                    r = Recording(streamer, REC_DIR)
                    r.start(log)
                    active[streamer] = r
                    log(f"[{r.base}] now recording {streamer}")
                except Exception as e:
                    log(f"[{streamer}] failed to start recording: {e}")


def main():
    log(f"DasTwitchy starting; watching: {', '.join(CFG['streamers'])}; "
        f"record={'ON' if CFG.get('record') else 'OFF'}")
    active = {}
    last_check = 0
    last_proc = 0
    while not STOP:
        now = time.time()
        try:
            if now - last_check >= CFG["check_interval_secs"]:
                last_check = now
                watch_loop(active)
            if now - last_proc >= CFG["process_interval_secs"]:
                last_proc = now
                nxt = state.dequeue()
                if nxt:
                    process_one(nxt)
        except Exception:
            log("loop error:\n" + traceback.format_exc())
        time.sleep(2)
    for rec in active.values():
        try:
            rec.stop()
        except Exception:
            pass
    log("DasTwitchy stopped")


if __name__ == "__main__":
    main()
