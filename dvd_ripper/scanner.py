import re
import subprocess
from pathlib import Path

from ._handbrake import HANDBRAKE_CMD
from ._log import log

LOG_PATH = Path(__file__).parent.parent / "last-scan.log"


class DiscReadError(Exception):
    pass


def scan_disc(device: str) -> list[dict]:
    """Run HandBrakeCLI --scan and return a list of title dicts."""
    log.info("Scanning device: %s", device)
    try:
        result = subprocess.run(
            [*HANDBRAKE_CMD, "--scan", "-i", device, "--min-duration", "0", "-t", "0"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        msg = "flatpak not found — is HandBrake installed via Flatpak?"
        log.error(msg)
        raise DiscReadError(msg)
    except subprocess.TimeoutExpired:
        msg = "Disc scan timed out after 120 seconds."
        log.error(msg)
        raise DiscReadError(msg)

    stderr = result.stderr
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(stderr)

    titles = _parse(stderr)
    if not titles:
        msg = f"No titles found on disc. Raw scan log saved to {LOG_PATH}"
        log.error(msg)
        raise DiscReadError(msg)

    log.info("Scan complete — found %d title(s)", len(titles))
    return titles


def _parse(stderr: str) -> list[dict]:
    titles = []
    current: dict | None = None

    for line in stderr.splitlines():
        m = re.match(r"^\+ title (\d+):", line)
        if m:
            if current:
                titles.append(current)
            current = {
                "number": int(m.group(1)),
                "duration": (0, 0, 0),
                "resolution": "unknown",
            }
            continue

        if current is None:
            continue

        m = re.match(r"^\s+\+ duration: (\d+):(\d+):(\d+)", line)
        if m:
            current["duration"] = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            continue

        m = re.match(r"^\s+\+ size: (\d+x\d+)", line)
        if m:
            current["resolution"] = m.group(1)

    if current:
        titles.append(current)

    return titles
