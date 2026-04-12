"""
dvd-ripper — terminal DVD ripping tool
"""

import re
import sys
import time
import threading
import shutil
import argparse
from pathlib import Path

from . import config, scanner, selector, encoder, naming
from ._log import log

# ── Dummy data (--test mode only) ─────────────────────────────────────────────

_DUMMY_TITLES = [
    {"number": 1, "duration": (0, 47, 12), "resolution": "853x480"},
    {"number": 2, "duration": (0, 48,  3), "resolution": "853x480"},
    {"number": 3, "duration": (0, 47, 55), "resolution": "853x480"},
    {"number": 4, "duration": (0, 49,  1), "resolution": "853x480"},
    {"number": 5, "duration": (0,  3, 12), "resolution": "853x480"},
    {"number": 6, "duration": (0,  2, 44), "resolution": "720x480"},
    {"number": 7, "duration": (0,  1, 22), "resolution": "720x480"},
    {"number": 8, "duration": (0,  0, 45), "resolution": "720x480"},
]

_DUMMY_MOVIE_TITLES = [
    {"number":  1, "duration": (2, 32,  4), "resolution": "1920x1080"},
    {"number":  2, "duration": (0,  5, 12), "resolution": "1920x1080"},
    {"number":  3, "duration": (0,  2, 44), "resolution": "1920x1080"},
    {"number":  4, "duration": (0,  8, 33), "resolution": "720x480"},
    {"number":  5, "duration": (0,  1, 22), "resolution": "720x480"},
    {"number":  6, "duration": (0,  3,  1), "resolution": "720x480"},
    {"number":  7, "duration": (0, 12, 18), "resolution": "1920x1080"},
    {"number":  8, "duration": (0,  0, 45), "resolution": "720x480"},
    {"number":  9, "duration": (0,  4, 55), "resolution": "720x480"},
    {"number": 10, "duration": (0,  2,  9), "resolution": "720x480"},
    {"number": 11, "duration": (0,  6, 30), "resolution": "1920x1080"},
    {"number": 12, "duration": (0,  1,  5), "resolution": "720x480"},
]

# ── Constants ─────────────────────────────────────────────────────────────────

TOO_MANY_TITLES = 50

_ENCODER_LABELS = {
    "x265":       "H.265",
    "x264":       "H.264",
    "qsv_h265":   "H.265 QSV",
    "qsv_h264":   "H.264 QSV",
    "svt_av1":    "AV1",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_duration(h, m, s):
    return f"{h:02d}:{m:02d}:{s:02d}"

def _video_label(cfg) -> str:
    label = _ENCODER_LABELS.get(cfg.video_encoder, cfg.video_encoder.upper())
    return f"{label} RF{cfg.rf}"

def prompt(label, default=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"  {label}{suffix}: ").strip()
        if not val and default is not None:
            return str(default)
        if val:
            return val
        print("  Please enter a value.")

def prompt_choice(label, choices):
    options = "/".join(choices)
    while True:
        val = input(f"  {label} [{options}]: ").strip().lower()
        if val in choices:
            return val
        print(f"  Please enter one of: {options}")

def prompt_int(label, default=None, min_val=None, max_val=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"  {label}{suffix}: ").strip()
        if not val and default is not None:
            return default
        try:
            n = int(val)
            if min_val is not None and n < min_val:
                print(f"  Must be at least {min_val}.")
                continue
            if max_val is not None and n > max_val:
                print(f"  Must be at most {max_val}.")
                continue
            return n
        except ValueError:
            print("  Please enter a number.")

# ── Spinner ───────────────────────────────────────────────────────────────────

class Spinner:
    def __init__(self, message):
        self.message = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        frames = ["|", "/", "-", "\\"]
        i = 0
        while not self._stop.is_set():
            sys.stdout.write(f"\r  {self.message} {frames[i % len(frames)]}")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        sys.stdout.write("\r" + " " * (len(self.message) + 6) + "\r")
        sys.stdout.flush()

# ── Title table ───────────────────────────────────────────────────────────────

def print_title_table(titles):
    term_width = shutil.get_terminal_size().columns

    def fmt_entry(t):
        dur = fmt_duration(*t["duration"])
        return f"{t['number']:<4} {dur:<12} {t['resolution']:<10}"

    hdr = f"{'#':<4} {'Duration':<12} {'Resolution':<10}"
    col_width = len(hdr)
    num_cols = max(1, (term_width - 2 - col_width) // (col_width + 3) + 1)

    divider = " | "
    print()
    print("  " + divider.join([hdr] * num_cols))
    print("  " + divider.join(["-" * col_width] * num_cols))
    rows = [titles[i:i + num_cols] for i in range(0, len(titles), num_cols)]
    for row in rows:
        entries = [fmt_entry(t) for t in row]
        while len(entries) < num_cols:
            entries.append(" " * col_width)
        print("  " + divider.join(entries))
    print()

# ── Progress bar ──────────────────────────────────────────────────────────────

_GREY  = "\033[38;5;240m"
_GREEN = "\033[32m"
_RESET = "\033[0m"

def _fmt_duration(total_secs: int) -> str:
    h, rem = divmod(total_secs, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _draw_progress(pct: float, elapsed_secs: int):
    colour = _GREEN if pct >= 100.0 else _GREY
    elapsed_str = _fmt_duration(elapsed_secs)
    if pct > 0:
        remaining_secs = int(elapsed_secs * (100.0 - pct) / pct)
        remaining_str = _fmt_duration(remaining_secs)
    else:
        remaining_str = "--:--"
    suffix = f"  {pct:5.1f}%   {elapsed_str} elapsed   {remaining_str} remaining"
    term_width = shutil.get_terminal_size().columns
    bar_width = max(10, term_width - 4 - len(suffix))
    filled = int(pct / 100 * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    sys.stdout.write(f"\r  [{colour}{bar}{_RESET}]{suffix}")
    sys.stdout.flush()

# ── Encode (real + test) ──────────────────────────────────────────────────────

def _encode(label: str, output_path, device: str, title_num: int,
            enc: str, audio_enc: str, rf: int, test_mode: bool) -> bool:
    print(f"  Encoding: {label}")
    start_time = time.monotonic()

    if test_mode:
        log.info("Encode start (test): title=%d output=%s", title_num, output_path)
        steps = 40
        for i in range(steps + 1):
            pct = (i / steps) * 100
            elapsed = int(time.monotonic() - start_time)
            _draw_progress(pct, elapsed)
        sys.stdout.write("\n")
        log.info("Encode complete (test): %s", output_path)
        print(f"  Done -> {output_path}\n")
        return True

    def _progress(pct: float):
        _draw_progress(pct, int(time.monotonic() - start_time))

    ok = encoder.encode(device, title_num, output_path, enc, audio_enc, rf,
                        progress_callback=_progress)
    sys.stdout.write("\n")
    if ok:
        print(f"  Done -> {output_path}\n")
    else:
        print(f"  Encoding failed.\n")
    return ok

# ── Scan (real + test) ────────────────────────────────────────────────────────

def _scan(is_movie: bool, cfg, test_mode: bool) -> list[dict] | None:
    if test_mode:
        with Spinner("Scanning disc..."):
            pass  # instant in test mode
        return _DUMMY_MOVIE_TITLES if is_movie else _DUMMY_TITLES

    try:
        with Spinner("Scanning disc..."):
            titles = scanner.scan_disc(cfg.device)
        return titles
    except scanner.DiscReadError as e:
        print(f"\n  Error: {e}\n")
        return None

# ── Movie flow ────────────────────────────────────────────────────────────────

def run_movie(cfg, test_mode: bool):
    print()
    name = prompt("Movie name")
    log.info("Movie: %r", name)

    titles = _scan(is_movie=True, cfg=cfg, test_mode=test_mode)
    if titles is None:
        return

    print(f"  Disc scan complete. Found {len(titles)} titles.")

    if len(titles) >= TOO_MANY_TITLES:
        print("  Too many titles to display — please enter the title number manually.")
        best = _pick_movie_title(titles)
        if best is None:
            return
    else:
        print_title_table(titles)

        best = selector.select_movie(titles, cfg.min_feature_duration)
        if best is None:
            print("  No eligible titles found.\n")
            return

        h, m, s = best["duration"]
        best_secs = h * 3600 + m * 60 + s
        if best_secs < cfg.min_feature_duration:
            print(f"  Note: no title met the {cfg.min_feature_duration // 60}-minute minimum "
                  f"— showing longest title as best guess.")

        dur_str = fmt_duration(*best["duration"])
        print(f"  Auto-selected Title {best['number']} ({dur_str}) as main feature.")

        while True:
            proceed = input("  Proceed? [y/n/s]elect: ").strip().lower()
            if proceed == "y":
                break
            if proceed == "n":
                log.info("Cancelled by user at title selection")
                print("\n  Cancelled.\n")
                return
            if proceed == "s":
                chosen = _pick_movie_title(titles)
                if chosen is None:
                    return
                best = chosen
                break

    output = naming.movie_path(cfg.movies_dir, name, cfg.output_format)
    dur_str = fmt_duration(*best["duration"])
    log.info("Selected title %d (%s, %s) -> %s", best["number"], dur_str, best["resolution"], output)
    print()
    print(f"  Track    : {best['number']} ({dur_str}, {best['resolution']})")
    print(f"  Filename : {output.name}")
    print(f"  Location : {output.parent}/")
    print(f"  Video    : {_video_label(cfg)}")
    print(f"  Audio    : {cfg.audio_encoder}")
    print()

    if not test_mode and output.exists():
        choice = input(f"  {output.name} already exists — [o]verwrite / [s]kip? ").strip().lower()
        if choice != "o":
            print("\n  Skipped.\n")
            return

    confirm = input("  Start ripping? [y/n]: ").strip().lower()
    if confirm != "y":
        log.info("Cancelled by user at final confirm")
        print("\n  Cancelled.\n")
        return

    print()
    _encode(output.name, output, cfg.device, best["number"],
            cfg.video_encoder, cfg.audio_encoder, cfg.rf, test_mode)


def _pick_movie_title(titles) -> dict | None:
    title_map = {t["number"]: t for t in titles}
    while True:
        raw = input("  Enter title number: ").strip()
        try:
            n = int(raw)
        except ValueError:
            print("  Please enter a number.")
            continue
        if n not in title_map:
            print(f"  Title {n} not found. Try again.")
            continue
        return title_map[n]

# ── TV pickers ────────────────────────────────────────────────────────────────

_TEST_SHOWS = ["Breaking Bad", "The Wire", "Sopranos", "Succession"]
_TEST_SEASONS: dict[str, list[int]] = {
    "Breaking Bad": [1, 2, 3],
    "The Wire": [1, 2],
    "Sopranos": [1, 2, 3, 4, 5],
    "Succession": [1],
}


def _pick_show(tv_dir: str, test_shows: list[str] | None = None) -> str:
    if test_shows is not None:
        shows = test_shows
    else:
        try:
            shows = sorted(p.name for p in Path(tv_dir).iterdir() if p.is_dir())
        except FileNotFoundError:
            shows = []
    if shows:
        term_width = shutil.get_terminal_size().columns
        # Each entry is "  NNN. {name}" (7 chars prefix) plus 3 chars trailing gap.
        col_width = max(len(s) for s in shows) + 10
        num_cols = max(1, term_width // col_width)
        print("\n  Existing shows:\n")
        for i, name in enumerate(shows, 1):
            entry = f"  {i:>3}. {name}"
            print(f"{entry:<{col_width}}", end="\n" if i % num_cols == 0 else "")
        if len(shows) % num_cols != 0:
            print()
        print()
        while True:
            val = input("  Select number or enter new show name: ").strip()
            if not val:
                continue
            if val.isdigit():
                idx = int(val) - 1
                if 0 <= idx < len(shows):
                    return shows[idx]
                print(f"  Please enter a number between 1 and {len(shows)}.")
            else:
                return val
    else:
        return prompt("Show name")


def _pick_season(tv_dir: str, show: str, test_seasons: list[int] | None = None) -> int:
    if test_seasons is not None:
        seasons = test_seasons
    else:
        show_path = Path(tv_dir) / naming.sanitize(show)
        seasons = []
        if show_path.is_dir():
            for p in sorted(show_path.iterdir()):
                m = re.match(r"^Season (\d+)$", p.name)
                if m and p.is_dir():
                    seasons.append(int(m.group(1)))
    if seasons:
        print(f"\n  Existing seasons for {show}:\n")
        for i, s in enumerate(seasons, 1):
            print(f"    {i:>3}. Season {s:02d}")
        print()
        while True:
            val = input("  List number to select existing, or any season number to add new (0 = specials): ").strip()
            if not val:
                continue
            try:
                n = int(val)
            except ValueError:
                print("  Please enter a number.")
                continue
            if n < 0 or n > 99:
                print("  Please enter a number between 0 and 99.")
                continue
            # if it matches a list index, return that season
            if 1 <= n <= len(seasons):
                return seasons[n - 1]
            # otherwise treat it as a new season number (including 0 and lower than existing)
            return n
    else:
        return prompt_int("Season number", min_val=0, max_val=99)


# ── TV flow ───────────────────────────────────────────────────────────────────

def run_tv(cfg, test_mode: bool):
    print()
    test_shows = _TEST_SHOWS if test_mode else None
    show = _pick_show(cfg.tv_dir, test_shows=test_shows)
    test_seasons = _TEST_SEASONS.get(show) if test_mode else None
    season = _pick_season(cfg.tv_dir, show, test_seasons=test_seasons)
    start_ep = prompt_int("Starting episode", default=1, min_val=1)
    ep_count = prompt_int("Episodes on this disc", min_val=1)
    log.info("TV: %r  Season %02d  starting ep %d  count %d", show, season, start_ep, ep_count)

    titles = _scan(is_movie=False, cfg=cfg, test_mode=test_mode)
    if titles is None:
        return

    print(f"  Disc scan complete. Found {len(titles)} titles.")

    if len(titles) >= TOO_MANY_TITLES:
        print("  Too many titles to display — please enter the title numbers manually.")
        selected, episodes = _pick_episode_titles(titles, season, start_ep, ep_count)
    else:
        print_title_table(titles)

        selected = selector.select_tv(
            titles, cfg.min_episode_duration, ep_count
        )
        episodes = list(range(start_ep, start_ep + len(selected)))

        if len(selected) < ep_count:
            print(f"  Note: found {len(selected)} matching title(s), "
                  f"fewer than the {ep_count} requested.")

        print(f"  Auto-selected {len(selected)} title(s) for Season {season:02d}, "
              f"Episodes {start_ep}–{start_ep + len(selected) - 1}:\n")
        print(f"  {'Episode':<10} {'Title':<8} {'Duration'}")
        print(f"  {'-'*7:<10} {'-'*5:<8} {'-'*8}")
        for ep, t in zip(episodes, selected):
            ep_str = f"S{season:02d}E{ep:02d}"
            print(f"  {ep_str:<10} {t['number']:<8} {fmt_duration(*t['duration'])}")
        print()

        while True:
            proceed = input("  Proceed? [y/n/s]elect: ").strip().lower()
            if proceed == "y":
                break
            if proceed == "n":
                log.info("Cancelled by user at title selection")
                print("\n  Cancelled.\n")
                return
            if proceed == "s":
                selected, episodes = _pick_episode_titles(titles, season, start_ep, ep_count)
                break

    plan = [
        (f"S{season:02d}E{ep:02d}", t, naming.tv_path(cfg.tv_dir, show, season, ep, cfg.output_format))
        for ep, t in zip(episodes, selected)
    ]
    location = plan[0][2].parent

    print()
    print(f"  {'Episode':<10} {'Track':<7} {'Duration':<12} {'Filename'}")
    print(f"  {'-'*7:<10} {'-'*5:<7} {'-'*8:<12} {'-'*8}")
    for ep_str, t, path in plan:
        print(f"  {ep_str:<10} {t['number']:<7} {fmt_duration(*t['duration']):<12} {path.name}")
    print()
    print(f"  Location : {location}/")
    print(f"  Video    : {_video_label(cfg)}")
    print(f"  Audio    : {cfg.audio_encoder}")
    print()

    confirm = input("  Start ripping? [y/n]: ").strip().lower()
    if confirm != "y":
        log.info("Cancelled by user at final confirm")
        print("\n  Cancelled.\n")
        return

    print()
    results = []
    failed = []
    for ep_str, t, output in plan:
        if not test_mode and output.exists():
            choice = input(f"  {output.name} already exists — [o]verwrite / [s]kip? ").strip().lower()
            if choice != "o":
                log.info("Skipped %s (already exists)", ep_str)
                print(f"  Skipped {ep_str}.\n")
                continue
        print(f"  [{ep_str}]")
        ok = _encode(output.name, output, cfg.device, t["number"],
                     cfg.video_encoder, cfg.audio_encoder, cfg.rf, test_mode)
        if ok:
            results.append((ep_str, output))
        else:
            failed.append(ep_str)

    if results:
        log.info("Ripped %d episode(s): %s", len(results), ", ".join(e for e, _ in results))
        print(f"  Ripped {len(results)} episode(s):\n")
        for ep_str, path in results:
            print(f"    {ep_str} -> {path}")
        print()
    if failed:
        log.error("Failed episodes: %s", ", ".join(failed))
        print(f"  Failed: {', '.join(failed)}\n")


def _pick_episode_titles(titles, season, start_ep, ep_count):
    title_map = {t["number"]: t for t in titles}
    while True:
        raw = input(f"  Enter {ep_count} title number(s) in episode order (comma-separated): ").strip()
        try:
            nums = [int(x.strip()) for x in raw.split(",")]
        except ValueError:
            print("  Invalid input — enter numbers separated by commas.")
            continue
        if len(nums) != ep_count:
            print(f"  Expected {ep_count} title(s), got {len(nums)}. Try again.")
            continue
        missing = [n for n in nums if n not in title_map]
        if missing:
            print(f"  Title(s) not found: {missing}. Try again.")
            continue
        selected = [title_map[n] for n in nums]
        episodes = list(range(start_ep, start_ep + len(selected)))
        return selected, episodes

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--test", action="store_true")
    args, _ = parser.parse_known_args()

    cfg = config.Config.test_default() if args.test else config.load()

    print()
    print("  dvd-ripper")
    print("  ----------")
    if args.test:
        print("  [test mode — using dummy data, delays skipped]")
    print()

    choice = prompt_choice("Movie or TV series? [m]ovie / [t]v", ["m", "t"])
    mode_label = "movie" if choice == "m" else "tv"
    log.info("Session started — mode=%s test=%s", mode_label, args.test)

    if choice == "m":
        run_movie(cfg, test_mode=args.test)
    else:
        run_tv(cfg, test_mode=args.test)


if __name__ == "__main__":
    main()
