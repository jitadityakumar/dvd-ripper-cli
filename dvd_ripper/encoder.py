import re
import subprocess
from pathlib import Path
from typing import Callable

from ._handbrake import HANDBRAKE_CMD
from ._log import log

_PROGRESS_RE = re.compile(r"Encoding:.*?([\d.]+) %")


def encode(
    device: str,
    title: int,
    output_path: Path,
    video_encoder: str,
    audio_encoder: str,
    rf: int,
    progress_callback: Callable[[float], None] | None = None,
) -> bool:
    """
    Encode a single title. Calls progress_callback(pct, eta) for each progress
    line emitted by HandBrakeCLI. Returns True on success, False on failure.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        *HANDBRAKE_CMD,
        "-i", device,
        "-t", str(title),
        "--encoder", video_encoder,
        "--quality", str(rf),
        "--aencoder", audio_encoder,
        "--format", "av_mp4",
        "-o", str(output_path),
    ]

    log.info("Encode start: title=%d output=%s", title, output_path)
    log.debug("Command: %s", " ".join(cmd))

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        log.error("HandBrakeCLI not found — encode aborted")
        return False

    buf = ""
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        if ch in ("\r", "\n"):
            if buf:
                m = _PROGRESS_RE.search(buf)
                if m and progress_callback:
                    progress_callback(float(m.group(1)))
                buf = ""
        else:
            buf += ch

    proc.wait()
    if proc.returncode == 0:
        log.info("Encode complete: %s", output_path)
    else:
        log.error("Encode failed: title=%d returncode=%d output=%s", title, proc.returncode, output_path)
    return proc.returncode == 0
