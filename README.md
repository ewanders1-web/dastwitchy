# DasTwitchy

Fresh rebuild of the Twitch recorder bot (replaces the old `twitch-bot`).

## What it does

1. **Watches** armed streamers (config: `whistleface`, `sweetdeesauce`) via streamlink
   live checks (no API keys).
2. **Records** audio-only to `recordings/<streamer>_<UTC-timestamp>.ogg`.
3. **Processes** finished recordings:
   - cut into ~10 min WAV chunks (recordings are AAC-in-OGG; WAV is what Demucs eats)
   - Demucs `htdemucs` `--two-stems=vocals` per chunk, CPU
   - join no_vocals stems -> music-only OGG Vorbis (q6, 44.1 kHz, stereo)
   - split into equal parts under 45 MB (min 10 min each), named
     `<base>-music-part{i}of{n}.ogg`
4. **Uploads** the original to Drive `TwitchRecordings/Originals/`
   and parts to `TwitchRecordings/Parts/<base>/`.

## Layout

- `dastwitchy.py` — main loop (watch + process)
- `recorder.py` — streamlink recording, live checks, stall guard, adoption of
  externally-started recordings
- `processor.py` — chunk -> Demucs -> join -> encode -> split
- `drive.py` — Google Drive via `hatch_gws_cli` (`+upload`, `files create/get/list`)
- `state.py` — JSON state helpers
- `env.py` — resolves the venv python (reuses the old bot's venv)

## State (`state/`)

- `processing_queue.json` — FIFO of finished recordings awaiting processing
- `done/<base>.json` — per-recording result (parts, Drive file ids, error)
- `reported.json` — bases already reported to Eric (read by the notifier cron)

## Config (`config.json`)

- `record`: false by default. With it off the bot only processes the queue —
  safe to run alongside the old bot for testing. Flip to true at cutover.

## Service

`dastwitchy.service` + `install-service.sh` (systemd). Note: `/etc` is wiped on
VM replacement; the hourly watchdog reinstalls the unit (same pattern as the
old bot).
