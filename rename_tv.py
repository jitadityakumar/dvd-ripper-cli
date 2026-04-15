#!/usr/bin/env python3
"""
rename_tv.py — Reorganise TV library into clean Plex-standard structure.

Three passes, each using folder-level renames where possible:
    Pass 1: Show folders    TV/<messy name>          -> TV/Show Name (Year)/
    Pass 2: Season folders  Show/Season 1 Complete   -> Show/Season 01/
    Pass 3: Episode files   Show/Season 01/<messy>   -> Show/Season 01/Show (Year) - S01E01 - Title.mp4

Usage:
    python3 rename_tv.py            Dry-run — show proposed changes, move nothing
    python3 rename_tv.py --apply    Execute the renames

Prerequisites:
    1. Batch conversion complete (convert_library.py --batch)
    2. Plex library rescan triggered so all .mp4 files are indexed
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
    """Make a title safe for use as a filename/folder on Linux + SMB."""
    name = name.replace(":", " -")
    name = re.sub(r'[<>"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def plex_get(base_url: str, token: str, path: str) -> ET.Element:
    sep = "&" if "?" in path else "?"
    result = subprocess.run(
        ["curl", "-s", f"{base_url}{path}{sep}X-Plex-Token={token}"],
        capture_output=True, text=True,
    )
    if not result.stdout.strip():
        print(f"ERROR: Empty response from Plex for {path}")
        sys.exit(1)
    return ET.fromstring(result.stdout)


def fetch_all_episodes(base_url: str, token: str, tv_section: str) -> list[dict]:
    """Walk show -> season -> episode and return a flat list of episode dicts."""
    shows_tree = plex_get(base_url, token, f"/library/sections/{tv_section}/all")
    episodes = []

    for show_el in shows_tree.findall(".//Directory"):
        show_title = show_el.get("title")
        show_year  = show_el.get("year")
        show_key   = show_el.get("key").replace("/allLeaves", "/children")

        seasons_tree = plex_get(base_url, token, show_key)
        for season_el in seasons_tree.findall(".//Directory"):
            season_index = season_el.get("index")
            if season_index is None:
                continue  # skip "All episodes" pseudo-season
            season_key = season_el.get("key")

            eps_tree = plex_get(base_url, token, season_key)
            for ep_el in eps_tree.findall(".//Video"):
                part = ep_el.find(".//Part")
                if part is None:
                    continue
                episodes.append({
                    "show_title":  show_title,
                    "show_year":   show_year,
                    "season_num":  int(season_index),
                    "ep_num":      int(ep_el.get("index", 0)),
                    "ep_title":    ep_el.get("title", ""),
                    "path":        Path(part.get("file")),
                })

    return episodes


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true", help="Execute renames (default is dry-run)")
    args = parser.parse_args()

    cfg        = load_config()
    base_url   = cfg["plex"]["url"]
    token      = cfg["plex"]["token"]
    tv_section = cfg["plex"]["tv_section"]
    tv_dir     = Path(cfg["paths"]["tv_dir"])

    dry_run = not args.apply
    if dry_run:
        print("DRY RUN — nothing will be renamed. Pass --apply to execute.\n")

    print("Fetching TV metadata from Plex...")
    all_episodes = fetch_all_episodes(base_url, token, tv_section)
    print(f"Found {len(all_episodes)} episodes across Plex library.\n")

    # ── Build proposed renames ────────────────────────────────────────────────

    # Pass 1: show folder renames  {current_show_dir -> target_show_dir}
    show_folder_renames: dict[Path, Path] = {}
    for ep in all_episodes:
        show_label   = f"{sanitize(ep['show_title'])} ({ep['show_year']})"
        current_dir  = ep["path"].relative_to(tv_dir).parts[0]
        current_path = tv_dir / current_dir
        target_path  = tv_dir / show_label
        if current_path != target_path and current_path not in show_folder_renames:
            show_folder_renames[current_path] = target_path

    # Pass 2: season folder renames — keyed by (show_folder_after_pass1, season_num)
    # We need to account for show folder renames already applied
    season_folder_renames: dict[Path, Path] = {}
    for ep in all_episodes:
        show_label  = f"{sanitize(ep['show_title'])} ({ep['show_year']})"
        season_label = f"Season {ep['season_num']:02d}"

        # Where will the show folder be after pass 1?
        old_show_dir = tv_dir / ep["path"].relative_to(tv_dir).parts[0]
        new_show_dir = show_folder_renames.get(old_show_dir, old_show_dir)

        parts = ep["path"].relative_to(tv_dir).parts
        if len(parts) < 3:
            # Episode sits directly in show folder — no season subfolder yet
            # Will be handled in pass 3 (file move into new season folder)
            continue

        current_season_dir = new_show_dir / parts[1]
        target_season_dir  = new_show_dir / season_label
        if current_season_dir != target_season_dir and current_season_dir not in season_folder_renames:
            season_folder_renames[current_season_dir] = target_season_dir

    # Pass 3: episode file renames
    episode_renames: dict[Path, Path] = {}
    for ep in all_episodes:
        if not ep["path"].exists() and not dry_run:
            continue

        show_label   = f"{sanitize(ep['show_title'])} ({ep['show_year']})"
        season_label = f"Season {ep['season_num']:02d}"
        ep_label     = (
            f"{show_label} - "
            f"S{ep['season_num']:02d}E{ep['ep_num']:02d} - "
            f"{sanitize(ep['ep_title'])}.mp4"
        )

        old_show_dir = tv_dir / ep["path"].relative_to(tv_dir).parts[0]
        new_show_dir = show_folder_renames.get(old_show_dir, old_show_dir)
        target_file  = new_show_dir / season_label / ep_label

        # Compute where the file will be after passes 1+2
        parts = ep["path"].relative_to(tv_dir).parts
        if len(parts) >= 3:
            old_season_dir     = new_show_dir / parts[1]
            current_season_dir = season_folder_renames.get(old_season_dir, old_season_dir)
            current_file       = current_season_dir / ep["path"].name
        else:
            # Loose in show folder
            current_file = new_show_dir / ep["path"].name

        if current_file != target_file:
            episode_renames[current_file] = target_file

    # ── Unmatched files ───────────────────────────────────────────────────────
    matched_paths = {ep["path"] for ep in all_episodes}
    unmatched = []
    for f in tv_dir.rglob("*"):
        if f.is_file() and f not in matched_paths and f.suffix in {".mp4", ".avi", ".mkv", ".divx", ".mpg", ".mpeg"}:
            unmatched.append(f)

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"Pass 1 — Show folder renames   : {len(show_folder_renames)}")
    print(f"Pass 2 — Season folder renames : {len(season_folder_renames)}")
    print(f"Pass 3 — Episode file renames  : {len(episode_renames)}")
    print(f"Unmatched video files          : {len(unmatched)}")
    print()

    if show_folder_renames:
        print("=== PASS 1 — SHOW FOLDERS ===")
        for src, dst in sorted(show_folder_renames.items()):
            print(f"  {src.name}")
            print(f"  -> {dst.name}")
            print()

    if season_folder_renames:
        print("=== PASS 2 — SEASON FOLDERS ===")
        for src, dst in sorted(season_folder_renames.items()):
            print(f"  {src.parent.name}/{src.name}")
            print(f"  -> {src.parent.name}/{dst.name}")
            print()

    if episode_renames:
        print("=== PASS 3 — EPISODE FILES ===")
        for src, dst in sorted(episode_renames.items()):
            print(f"  {src.parent.parent.name}/{src.parent.name}/{src.name}")
            print(f"  -> {dst.parent.parent.name}/{dst.parent.name}/{dst.name}")
            print()

    if unmatched:
        print("=== UNMATCHED — manual review ===")
        for f in sorted(unmatched):
            print(f"  {f.relative_to(tv_dir)}")
        print()

    if dry_run:
        print("Dry-run complete. Re-run with --apply to execute.")
        return

    if not any([show_folder_renames, season_folder_renames, episode_renames]):
        print("Nothing to rename.")
        return

    # ── Apply ─────────────────────────────────────────────────────────────────
    errors = 0

    print("=== PASS 1 — Renaming show folders ===")
    for src, dst in sorted(show_folder_renames.items()):
        try:
            src.rename(dst)
            print(f"  OK  {src.name} -> {dst.name}")
        except Exception as e:
            print(f"  ERR {src.name}: {e}")
            errors += 1

    print("\n=== PASS 2 — Renaming season folders ===")
    for src, dst in sorted(season_folder_renames.items()):
        try:
            src.rename(dst)
            print(f"  OK  {src.parent.name}/{src.name} -> {dst.name}")
        except Exception as e:
            print(f"  ERR {src}: {e}")
            errors += 1

    print("\n=== PASS 3 — Renaming episode files ===")
    for src, dst in sorted(episode_renames.items()):
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            print(f"  OK  {src.name}")
            print(f"      -> {dst.name}")
            # Remove empty source folder
            if src.parent != dst.parent and src.parent.is_dir() and not any(src.parent.iterdir()):
                src.parent.rmdir()
                print(f"      RMDIR {src.parent.name} (empty)")
        except Exception as e:
            print(f"  ERR {src.name}: {e}")
            errors += 1

    print(f"\nDone — {errors} errors.")


if __name__ == "__main__":
    main()
