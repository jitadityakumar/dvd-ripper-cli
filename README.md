# dvd-ripper

Terminal-based DVD ripping tool. Scans a disc, selects the right titles automatically, and encodes them to Plex-compatible MP4 files using HandBrakeCLI.

## Dependencies

### Runtime

| Dependency | Notes |
|---|---|
| Python 3.11+ | Uses stdlib `tomllib` — no older versions |
| [HandBrake](https://handbrake.fr) | Must be available as `HandBrakeCLI` on your PATH, or installed as a Flatpak (see below) |
| `libdvdcss2` | Required for encrypted commercial DVDs |

**HandBrake via Flatpak (Linux):**

```
flatpak install flathub fr.handbrake.ghb
```

The app expects the Flatpak command `flatpak run --command=HandBrakeCLI fr.handbrake.ghb`. If you have a native `HandBrakeCLI` instead, update the `HANDBRAKE_CMD` constant in `dvd_ripper/_handbrake.py`.

**libdvdcss2 (Ubuntu/Debian):**

```
sudo apt install libdvd-pkg && sudo dpkg-reconfigure libdvd-pkg
```

### Development (tests only)

```
pytest
```

---

## Setup

**1. Clone the repo and create a virtual environment:**

```
git clone <repo-url>
cd handbrake-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**2. Create your config file:**

```
cp config.toml.example config.toml
```

Edit `config.toml` to set your output directories, device path, and encoding settings:

```toml
[paths]
movies_dir = "/mnt/media/Movies"
tv_dir     = "/mnt/media/TV"
device     = "/dev/sr0"

[encoding]
video_encoder = "qsv_h265"   # or x265, qsv_h264, x264, svt_av1
audio_encoder = "av_aac"
rf            = 20
output_format = "mp4"

[tv]
min_episode_duration_seconds = 900    # 15 min — filters menus/extras

[movie]
min_feature_duration_seconds = 3600   # 60 min — ignores short bonus titles
```

> `config.toml` is gitignored and never committed.

---

## Running the app

```
dvd-ripper
```

Or without installing:

```
python -m dvd_ripper.cli
```

You will be prompted to choose **Movie** or **TV** mode, then guided through the rest of the flow interactively.

**Movie mode** — scans the disc, auto-selects the longest title as the main feature, shows a summary, and asks for confirmation before encoding. You can override the auto-selection with `s` at the proceed prompt.

**TV mode** — prompts for show name, season, starting episode number, and episode count. Scans the disc, clusters titles by duration to identify episodes, shows the plan, and asks for confirmation. Supports manual title selection if the auto-selection isn't right.

---

## Test mode

Run with `--test` to use dummy disc data and skip all real scanning and encoding. Useful for checking the UX flow without a disc inserted or HandBrake installed.

```
dvd-ripper --test
```

Or:

```
python -m dvd_ripper.cli --test
```

Test mode uses hardcoded titles and shows so every prompt still behaves as normal — it just never touches the disc drive or produces output files.

---

## Running the tests

```
python -m pytest tests/ -v
```

Tests cover `naming`, `selector`, and the full interactive CLI flows (prompt helpers, show/season/title pickers, movie flow, TV flow). Flow tests mock the scan and encode steps so no disc or HandBrake installation is needed.

---

## Library utilities

Three standalone scripts for converting and reorganising an existing media library. They read paths from `config.toml` and are independent of the main ripping flow.

### convert_library.py — batch convert existing files to MP4

Scans a library for non-MP4 video files and converts them using HandBrakeCLI, then optionally stitches multi-part films into a single file.

```
python3 convert_library.py --init    # scan library, create conversion_log.json + dashboard.html
python3 convert_library.py --test    # convert one file per extension (keeps originals)
python3 convert_library.py --batch   # convert all pending files, delete originals on success
python3 convert_library.py --stitch  # join multi-part films (cd1/cd2, VOBs) into one MP4
```

Progress is written to `conversion.log`. State is tracked in `conversion_log.json` so a crashed or interrupted batch can be resumed — re-run `--batch` and it skips already-converted files.

### rename_movies.py — rename Movies library to Plex-standard structure

Queries the Plex API for matched titles and reorganises your Movies folder into:

```
Movies/
  Movie Name (Year)/
    Movie Name (Year).mp4
```

Requires a `[plex]` section in `config.toml`:

```toml
[plex]
url            = "http://localhost:32400"
token          = "your-plex-token"
movies_section = "1"
tv_section     = "2"
```

```
python3 rename_movies.py          # dry-run — show proposed moves, touch nothing
python3 rename_movies.py --apply  # execute
```

Files not matched by Plex are listed for manual review and left untouched.

### rename_tv.py — rename TV library to Plex-standard structure

Queries Plex and renames the TV library in three passes:

1. Show folders → `Show Name (Year)/`
2. Season folders → `Season 01/`
3. Episode files → `Show Name (Year) - S01E01 - Episode Title.mp4`

Uses folder-level renames in passes 1 and 2 so all companion files (subtitles, artwork) move with their folder automatically.

```
python3 rename_tv.py          # dry-run
python3 rename_tv.py --apply  # execute
```

Unmatched video files are reported separately and left in place.
