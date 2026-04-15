#!/usr/bin/env python3
"""
rename_movies.py — Reorganise Movies library into clean Plex-standard structure.

Each movie is moved to its own folder and renamed:
    Movies/<Title> (<Year>)/<Title> (<Year>).mp4

Usage:
    python3 rename_movies.py            Dry-run — show proposed changes, move nothing
    python3 rename_movies.py --apply    Execute the moves

Prerequisites (run in this order before using --apply):
    1. Batch conversion complete (convert_library.py --batch)
    2. Multi-part stitching complete (convert_library.py --stitch)
    3. Plex library rescan triggered so all .mp4 files are indexed
"""

import argparse
import os
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.toml"


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def sanitize(name: str) -> str:
    """Make a movie title safe for use as a filename/folder name."""
    name = name.replace(":", " -")
    name = re.sub(r'[<>"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def get_plex_movies(url: str, token: str, section: str) -> list[dict]:
    """Query Plex and return list of {title, year, path} for all matched movies."""
    result = subprocess.run(
        ["curl", "-s", f"{url}/library/sections/{section}/all?X-Plex-Token={token}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print("ERROR: Could not reach Plex. Is it running and has it rescanned?")
        sys.exit(1)

    tree = ET.fromstring(result.stdout)
    movies = []
    for v in tree.findall(".//Video"):
        title = v.get("title")
        year = v.get("year")
        media = v.find(".//Part")
        if media is not None:
            movies.append({"title": title, "year": year, "path": Path(media.get("file"))})
    return movies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Execute moves (default is dry-run)")
    args = parser.parse_args()

    cfg = load_config()
    plex_url     = cfg["plex"]["url"]
    plex_token   = cfg["plex"]["token"]
    plex_section = cfg["plex"]["movies_section"]
    movies_dir   = Path(cfg["paths"]["movies_dir"])

    dry_run = not args.apply
    if dry_run:
        print("DRY RUN — no files will be moved. Pass --apply to execute.\n")

    movies = get_plex_movies(plex_url, plex_token, plex_section)

    to_move   = []   # (src_path, target_dir, target_file, label)
    already_ok = []
    not_found  = []  # Plex indexed a path that no longer exists (needs rescan)
    matched_toplevel = set()

    for movie in movies:
        src  = movie["path"]
        safe = sanitize(movie["title"])
        year = movie["year"]
        label       = f"{safe} ({year})"
        target_dir  = movies_dir / label
        target_file = target_dir / f"{label}.mp4"

        # Track which top-level entries Plex has matched
        try:
            rel = src.relative_to(movies_dir)
            matched_toplevel.add(rel.parts[0])
        except ValueError:
            pass  # file outside movies_dir — shouldn't happen

        if not src.exists():
            not_found.append(src)
            continue

        if src == target_file:
            already_ok.append(label)
            continue

        to_move.append((src, target_dir, target_file, label))

    # Anything on disk that Plex didn't match
    unmatched = sorted(set(os.listdir(movies_dir)) - matched_toplevel)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"Plex-matched movies : {len(movies)}")
    print(f"  Already correct   : {len(already_ok)}")
    print(f"  To move           : {len(to_move)}")
    print(f"  Source not found  : {len(not_found)}  (Plex needs a rescan if > 0)")
    print(f"Not matched by Plex : {len(unmatched)}  (manual review needed)")
    print()

    if to_move:
        print("=== PROPOSED MOVES ===")
        for src, _, target_file, _ in to_move:
            print(f"  {src}")
            print(f"  -> {target_file}")
            print()

    if not_found:
        print("=== SOURCE NOT FOUND — trigger a Plex rescan ===")
        for p in not_found:
            print(f"  {p}")
        print()

    if unmatched:
        print("=== NOT MATCHED BY PLEX — manual review ===")
        for u in unmatched:
            print(f"  {u}")
        print()

    if dry_run:
        print("Dry-run complete. Re-run with --apply to execute.")
        return

    if not to_move:
        print("Nothing to move.")
        return

    # ── Apply ─────────────────────────────────────────────────────────────────
    print("=== APPLYING ===")
    ok = 0
    errors = 0
    for src, target_dir, target_file, label in to_move:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            src.rename(target_file)
            print(f"  MOVED  {label}")

            # Remove the source folder if it's now empty
            src_dir = src.parent
            if src_dir != movies_dir and src_dir.is_dir() and not any(src_dir.iterdir()):
                src_dir.rmdir()
                print(f"  RMDIR  {src_dir.name}  (was empty)")

            ok += 1
        except Exception as e:
            print(f"  ERROR  {label}: {e}")
            errors += 1

    print(f"\nDone — {ok} moved, {errors} errors.")


if __name__ == "__main__":
    main()
