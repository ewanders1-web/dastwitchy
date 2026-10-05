"""DasTwitchy post-processing: Demucs vocal removal -> music-only OGG -> size-capped parts.

Pipeline (mirrors Eric's v6.39 recorder):
  1. Cut the recording into ~10min chunks, transcoded to WAV
     (recordings are AAC-in-OGG; WAV is what Demucs eats natively).
  2. Run `demucs --two-stems=vocals` (htdemucs, CPU) per chunk.
  3. Concatenate the no_vocals stems, encode to OGG Vorbis (q6, 44.1kHz).
  4. Split into N equal parts so each is under max_part_bytes
     (parts >= min_part_secs long), named {base}-music-part{i}of{n}.ogg.
"""
import math
import shutil
import subprocess
from pathlib import Path

from env import demucs_cmd

FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/usr/bin/ffprobe"


def _run(cmd, timeout, log, prefix=""):
    log(f"{prefix}$ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{prefix}failed: {(r.stderr or r.stdout or '')[-400:]}")
    return r


def media_duration_secs(path):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float((r.stdout or "").strip())
    except Exception:
        return 0.0


def chunk_to_wav(src, chunk_dir, chunk_secs, log, prefix=""):
    """Cut src into fixed-length WAV chunks. Returns sorted chunk paths."""
    chunk_dir = Path(chunk_dir)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(src),
         "-map", "0:a:0", "-vn",
         "-f", "segment", "-segment_time", str(chunk_secs),
         "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
         "-reset_timestamps", "1",
         str(chunk_dir / "chunk%03d.wav")],
        timeout=3600, log=log, prefix=prefix,
    )
    chunks = sorted(chunk_dir.glob("chunk*.wav"))
    if not chunks:
        raise RuntimeError(f"{prefix}chunking produced no chunks")
    return chunks


def demucs_no_vocals(chunk_wav, out_dir, model, log, prefix=""):
    """Run Demucs on one WAV chunk. Returns the no_vocals.wav path."""
    out_dir = Path(out_dir)
    _run(
        demucs_cmd() + ["-n", model, "--two-stems=vocals",
                        "-o", str(out_dir), str(chunk_wav)],
        timeout=14400, log=log, prefix=prefix,
    )
    # demucs -o <dir> writes <dir>/<model>/<stem>.wav
    cand = out_dir / model / chunk_wav.stem / "no_vocals.wav"
    alt = out_dir / model / "no_vocals.wav"
    for p in (cand, alt):
        if p.exists():
            return p
    # fallback: find it
    found = sorted(out_dir.rglob("no_vocals.wav"))
    if not found:
        raise RuntimeError(f"{prefix}demucs produced no no_vocals stem")
    return found[0]


def join_and_encode(stems, out_ogg, log, prefix=""):
    """Concatenate no_vocals WAVs -> single music-only OGG (libvorbis q6)."""
    out_ogg = Path(out_ogg)
    out_ogg.parent.mkdir(parents=True, exist_ok=True)
    lst = out_ogg.parent / "join.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in stems))
    _run(
        [FFMPEG, "-nostdin", "-v", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c:a", "libvorbis", "-q:a", "6", "-ar", "44100", "-ac", "2",
         str(out_ogg)],
        timeout=3600, log=log, prefix=prefix,
    )
    return out_ogg


def split_plan(total_bytes, total_secs, max_bytes, min_secs):
    """Equal-count split so every part is under max_bytes and >= min_secs."""
    if total_bytes <= 0 or total_secs <= 0:
        return 1
    n = max(1, math.ceil(total_bytes / max_bytes))
    while n > 1 and (total_secs / n) < min_secs:
        n -= 1
    return n


def split_equal(src_ogg, out_dir, base, n_parts, log, prefix=""):
    """Split into n equal-duration parts. Returns part paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = media_duration_secs(src_ogg)
    if dur <= 0:
        raise RuntimeError(f"{prefix}cannot probe duration of {src_ogg}")
    part_dur = dur / n_parts
    parts = []
    for i in range(1, n_parts + 1):
        start = (i - 1) * part_dur
        # last part runs to EOF to avoid rounding loss
        out = out_dir / f"{base}-music-part{i}of{n_parts}.ogg"
        cmd = [FFMPEG, "-nostdin", "-v", "error", "-y",
               "-ss", f"{start:.3f}", "-i", str(src_ogg)]
        if i < n_parts:
            cmd += ["-t", f"{part_dur:.3f}"]
        cmd += ["-c", "copy", str(out)]
        _run(cmd, timeout=1800, log=log, prefix=prefix)
        parts.append(out)
    return parts


def process_recording(src_path, work_root, cfg, log):
    """Full pipeline for one finished recording.

    Returns dict(base=..., music_ogg=Path, parts=[Path, ...]).
    Raises on failure; caller writes the DONE marker.
    """
    src = Path(src_path)
    base = src.stem
    prefix = f"[{base}] "
    work = Path(work_root) / base
    work.mkdir(parents=True, exist_ok=True)

    log(prefix + "chunking to WAV...")
    chunks = chunk_to_wav(src, work / "chunks", cfg["demucs_chunk_secs"], log, prefix)
    log(prefix + f"{len(chunks)} chunk(s)")

    stems = []
    demucs_out = work / "demucs"
    for i, ch in enumerate(chunks, 1):
        log(prefix + f"demucs chunk {i}/{len(chunks)}: {ch.name}")
        stems.append(demucs_no_vocals(ch, demucs_out, cfg["demucs_model"], log, prefix))

    music_ogg = work / f"{base}-music.ogg"
    log(prefix + "joining + encoding music-only OGG...")
    join_and_encode(stems, music_ogg, log, prefix)

    total_bytes = music_ogg.stat().st_size
    total_secs = media_duration_secs(music_ogg)
    n = split_plan(total_bytes, total_secs, cfg["max_part_bytes"], cfg["min_part_secs"])
    log(prefix + f"splitting into {n} part(s) ({total_secs / max(n, 1) / 60:.1f} min each)")
    parts = split_equal(music_ogg, work / "parts", base, n, log, prefix)

    # free chunk audio; keep work dir for inspection on failure only
    for ch in chunks:
        try:
            ch.unlink()
        except OSError:
            pass

    return {"base": base, "music_ogg": music_ogg, "parts": parts,
            "total_secs": total_secs}
