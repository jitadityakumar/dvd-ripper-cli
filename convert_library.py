#!/usr/bin/env python3
"""
convert_library.py — Transcode non-MP4 media files to MP4 using HandBrakeCLI,
                     then stitch multi-part films into a single file with ffmpeg.

Usage:
    python convert_library.py --init              Scan library, create conversion_log.json + dashboard.html
    python convert_library.py --test              Convert one standalone file per extension (originals kept)
    python convert_library.py --batch [--limit N] Convert pending files, delete originals on success
    python convert_library.py --stitch            Join multi-part films whose parts are all done

Status lifecycle:
    file entry:   pending -> in-progress -> done | failed
    stitch entry: pending -> in-progress -> done | failed

On resume, any 'in-progress' entries are cleaned up (partial output removed, reset to pending).
A stitch only runs once all its input MP4s exist (i.e. all parts are done).
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.toml"
LOG_PATH    = SCRIPT_DIR / "conversion_log.json"
DASHBOARD   = SCRIPT_DIR / "dashboard.html"
LOG_FILE    = SCRIPT_DIR / "conversion.log"

logger = logging.getLogger("convert_library")


def _setup_logging() -> None:
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

HANDBRAKE_CMD = ["flatpak", "run", "--command=HandBrakeCLI", "fr.handbrake.ghb"]

NON_MP4_EXTENSIONS = {
    ".avi", ".mkv", ".divx", ".mpg", ".mpeg", ".vob",
    ".mov", ".wmv", ".m4v", ".ts",  ".m2ts", ".mts",
    ".flv", ".webm", ".rmvb", ".ogv", ".3gp",  ".asf",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def output_path_for(source: Path) -> Path:
    return source.with_suffix(".mp4")


def find_non_mp4_files(directories: list[Path]) -> list[Path]:
    files = []
    for d in directories:
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix.lower() in NON_MP4_EXTENSIONS:
                files.append(p)
    return files


def detect_groups(files: list[Path], movies_dir: Path) -> dict[Path, list[Path]]:
    """
    Return a mapping of group_root -> sorted list of files for multi-part films.
    Single files are excluded.

    Only considers files under movies_dir — TV episode folders must not be grouped.

    Two layouts handled:
      Flat:   movies_dir/<movie-folder>/cd1.avi + cd2.avi  (parent == movie-folder)
      Nested: movies_dir/<movie-folder>/cd1/a.avi + cd2/b.avi  (grandparent == movie-folder)

    In both cases the group root is the movie-folder, which is a direct child of movies_dir.
    """
    # Only look at files inside the movies directory
    movie_files = [f for f in files if f.is_relative_to(movies_dir)]

    by_parent: dict[Path, list[Path]] = defaultdict(list)
    for f in movie_files:
        by_parent[f.parent].append(f)

    groups: dict[Path, list[Path]] = {}
    ungrouped: list[Path] = []

    for parent, parent_files in sorted(by_parent.items()):
        # Flat layout: movie-folder is a direct child of movies_dir
        if parent.parent == movies_dir and len(parent_files) >= 2:
            groups[parent] = sorted(parent_files)
        else:
            ungrouped.extend(parent_files)

    # Nested layout: files sit one level deeper (cd1/, cd2/ sub-folders)
    # grandparent must be a direct child of movies_dir
    by_grandparent: dict[Path, list[Path]] = defaultdict(list)
    for f in ungrouped:
        grandparent = f.parent.parent
        if grandparent.parent == movies_dir:
            by_grandparent[grandparent].append(f)

    for grandparent, gp_files in sorted(by_grandparent.items()):
        if len(gp_files) >= 2 and grandparent not in groups:
            groups[grandparent] = sorted(gp_files)

    return groups

# ── Log / dashboard persistence ───────────────────────────────────────────────

def load_log() -> dict:
    if not LOG_PATH.exists():
        return {"files": [], "stitches": []}
    with open(LOG_PATH) as f:
        return json.load(f)


def save_log(log: dict) -> None:
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)
    _write_dashboard(log)


_DASHBOARD_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Conversion Dashboard</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh;padding:28px}
    h1{font-size:1.4rem;font-weight:600;color:#f8fafc;margin-bottom:24px}
    h2{font-size:1rem;font-weight:600;color:#94a3b8;margin:28px 0 12px;text-transform:uppercase;letter-spacing:.06em;font-size:.78rem}
    .cards{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}
    .card{background:#1e2130;border-radius:10px;padding:14px 22px;min-width:110px}
    .card .lbl{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin-bottom:6px}
    .card .n{font-size:2rem;font-weight:700}
    .total .n{color:#f8fafc} .pending .n{color:#94a3b8}
    .in-progress .n{color:#f59e0b} .done .n{color:#22c55e} .failed .n{color:#ef4444}
    .controls{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
    .fb{padding:5px 13px;border-radius:6px;border:1px solid #334155;background:#1e2130;color:#94a3b8;cursor:pointer;font-size:.83rem}
    .fb.on{border-color:#6366f1;background:#312e81;color:#a5b4fc}
    .fb:hover{border-color:#6366f1}
    input.search{padding:5px 11px;border-radius:6px;border:1px solid #334155;background:#1e2130;color:#e2e8f0;font-size:.83rem;width:260px}
    input.search::placeholder{color:#475569}
    .refresh{margin-left:auto;padding:5px 13px;border-radius:6px;border:1px solid #334155;background:#1e2130;color:#94a3b8;cursor:pointer;font-size:.83rem}
    .refresh:hover{border-color:#6366f1;color:#a5b4fc}
    table{width:100%;border-collapse:collapse;background:#1e2130;border-radius:10px;overflow:hidden;font-size:.83rem;margin-bottom:8px}
    th{padding:9px 13px;text-align:left;background:#161926;color:#64748b;font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #2d3748}
    td{padding:8px 13px;border-bottom:1px solid #1a1f2e;color:#cbd5e1;max-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    tr:last-child td{border-bottom:none}
    tr:hover td{background:#252a3a}
    .badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.72rem;font-weight:600;white-space:nowrap}
    .badge.pending{background:#1e293b;color:#94a3b8}
    .badge.in-progress{background:#451a03;color:#f59e0b}
    .badge.done{background:#052e16;color:#22c55e}
    .badge.failed{background:#2d0a0a;color:#ef4444}
    .ext{display:inline-block;padding:1px 7px;border-radius:4px;background:#1e293b;color:#7dd3fc;font-family:monospace;font-size:.78rem}
    .type-stitch{display:inline-block;padding:1px 7px;border-radius:4px;background:#1e1b4b;color:#a5b4fc;font-size:.78rem}
    .fname{color:#e2e8f0}
    .fpath{color:#475569;font-size:.75rem}
    .empty{text-align:center;padding:48px;color:#475569}
    .meta{font-size:.72rem;color:#475569;margin-bottom:14px}
  </style>
</head>
<body>
  <h1>Library Conversion Dashboard</h1>
  <div class="cards">
    <div class="card total">      <div class="lbl">Total</div>       <div class="n" id="c-total"></div></div>
    <div class="card pending">    <div class="lbl">Pending</div>     <div class="n" id="c-pending"></div></div>
    <div class="card in-progress"><div class="lbl">In Progress</div> <div class="n" id="c-in-progress"></div></div>
    <div class="card done">       <div class="lbl">Done</div>        <div class="n" id="c-done"></div></div>
    <div class="card failed">     <div class="lbl">Failed</div>      <div class="n" id="c-failed"></div></div>
  </div>
  <div class="controls">
    <button class="fb on" data-f="all">All</button>
    <button class="fb"    data-f="pending">Pending</button>
    <button class="fb"    data-f="in-progress">In Progress</button>
    <button class="fb"    data-f="done">Done</button>
    <button class="fb"    data-f="failed">Failed</button>
    <input  class="search" type="text" placeholder="Filter by filename…" id="search">
    <button class="refresh" onclick="location.reload()">&#8635; Refresh</button>
  </div>
  <p class="meta" id="meta"></p>

  <h2>Conversions</h2>
  <table>
    <thead><tr>
      <th style="width:120px">Status</th>
      <th style="width:70px">Type</th>
      <th>File</th>
      <th style="width:170px">Timestamp (UTC)</th>
    </tr></thead>
    <tbody id="tbody-files"></tbody>
  </table>

  <h2>Stitches</h2>
  <table>
    <thead><tr>
      <th style="width:120px">Status</th>
      <th>Group</th>
      <th>Output</th>
      <th style="width:170px">Timestamp (UTC)</th>
    </tr></thead>
    <tbody id="tbody-stitches"></tbody>
  </table>

<script>
const FILES    = __FILES_DATA__;
const STITCHES = __STITCHES_DATA__;

let filt = 'all', q = '';

function getCounts() {
  const all = [...FILES, ...STITCHES];
  const c = {total: all.length, pending: 0, 'in-progress': 0, done: 0, failed: 0};
  all.forEach(e => { if (c[e.status] !== undefined) c[e.status]++; });
  return c;
}

function renderFiles() {
  const rows = FILES.filter(e =>
    (filt === 'all' || e.status === filt) &&
    (!q || e.source.toLowerCase().includes(q))
  );
  const tbody = document.getElementById('tbody-files');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">No entries match</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(e => {
    const parts = e.source.split('/');
    const name  = parts.pop();
    const dir   = parts.join('/') + '/';
    return `<tr>
      <td><span class="badge ${e.status}">${e.status}</span></td>
      <td><span class="ext">${e.extension}</span></td>
      <td title="${e.source}"><span class="fname">${name}</span><br><span class="fpath">${dir}</span></td>
      <td>${e.timestamp || '—'}</td>
    </tr>`;
  }).join('');
}

function renderStitches() {
  const rows = STITCHES.filter(e =>
    (filt === 'all' || e.status === filt) &&
    (!q || e.name.toLowerCase().includes(q))
  );
  const tbody = document.getElementById('tbody-stitches');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">No entries match</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(e => {
    const outParts = e.output.split('/');
    const outName  = outParts.pop();
    return `<tr>
      <td><span class="badge ${e.status}">${e.status}</span></td>
      <td title="${e.inputs.join('\\n')}">${e.name} <span class="fpath">(${e.inputs.length} parts)</span></td>
      <td title="${e.output}"><span class="fname">${outName}</span></td>
      <td>${e.timestamp || '—'}</td>
    </tr>`;
  }).join('');
}

function render() {
  const c = getCounts();
  ['total','pending','in-progress','done','failed'].forEach(k => {
    document.getElementById('c-' + k).textContent = c[k];
  });
  renderFiles();
  renderStitches();
  const all = [...FILES, ...STITCHES];
  const latest = all.map(e => e.timestamp).filter(Boolean).sort().pop();
  document.getElementById('meta').textContent = latest ? 'Last updated: ' + latest + ' UTC' : '';
}

document.querySelectorAll('.fb').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.fb').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  filt = b.dataset.f;
  render();
}));
document.getElementById('search').addEventListener('input', e => { q = e.target.value.toLowerCase(); render(); });

render();
</script>
</body>
</html>
"""


def _write_dashboard(log: dict) -> None:
    html = _DASHBOARD_TEMPLATE \
        .replace("__FILES_DATA__",    json.dumps(log["files"])) \
        .replace("__STITCHES_DATA__", json.dumps(log["stitches"]))
    with open(DASHBOARD, "w") as f:
        f.write(html)

# ── Core operations ───────────────────────────────────────────────────────────

def cleanup_interrupted(log: dict) -> int:
    """Reset any in-progress entries (files or stitches) to pending, removing partial outputs."""
    count = 0
    for entry in log["files"] + log["stitches"]:
        if entry["status"] == "in-progress":
            partial = Path(entry["output"])
            if partial.exists():
                partial.unlink()
                print(f"  [cleanup] removed partial: {partial.name}")
            entry["status"]    = "pending"
            entry["timestamp"] = None
            count += 1
    return count


def convert(source: Path, output: Path, cfg: dict) -> tuple[bool, float]:
    """Run HandBrakeCLI on a single file. Removes partial output on failure.
    Returns (success, elapsed_seconds)."""
    cmd = [
        *HANDBRAKE_CMD,
        "-i", str(source),
        "--encoder", cfg["encoding"]["video_encoder"],
        "--quality", str(cfg["encoding"]["rf"]),
        "--aencoder", cfg["encoding"]["audio_encoder"],
        "--format", "av_mp4",
        "-o", str(output),
    ]
    t0 = time.monotonic()
    proc = None
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    finally:
        elapsed = time.monotonic() - t0
        rc = proc.returncode if proc is not None else "N/A"
        logger.debug("EXIT     %s — returncode=%s  elapsed=%.1fs", source.name, rc, elapsed)
    if proc.returncode == 0:
        return True, elapsed
    print(f"  [FAILED] returncode={proc.returncode}")
    for line in proc.stderr.strip().splitlines()[-10:]:
        print(f"    {line}")
    logger.error("FAILED   %s — returncode=%d", source.name, proc.returncode)
    for line in proc.stderr.strip().splitlines():
        logger.error("  %s", line)
    if output.exists():
        output.unlink()
    return False, elapsed


def stitch(inputs: list[Path], output: Path) -> tuple[bool, float]:
    """
    Join MP4 files with ffmpeg stream-copy (lossless, no re-encode).
    Removes partial output on failure.
    Returns (success, elapsed_seconds).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
        for inp in inputs:
            tf.write(f"file '{inp}'\n")
        concat_file = Path(tf.name)

    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output),
    ]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - t0
    concat_file.unlink(missing_ok=True)

    if proc.returncode == 0:
        return True, elapsed
    print(f"  [FAILED] returncode={proc.returncode}")
    for line in proc.stderr.strip().splitlines()[-10:]:
        print(f"    {line}")
    logger.error("STITCH FAILED  %s — returncode=%d", output.name, proc.returncode)
    for line in proc.stderr.strip().splitlines():
        logger.error("  %s", line)
    if output.exists():
        output.unlink()
    return False, elapsed

# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_init(cfg: dict) -> None:
    movies_dir = Path(cfg["paths"]["movies_dir"])
    tv_dir     = Path(cfg["paths"]["tv_dir"])

    print(f"Scanning {movies_dir} and {tv_dir}...")
    files = find_non_mp4_files([movies_dir, tv_dir])
    print(f"Found {len(files)} non-MP4 file(s)")

    # Also include existing MP4 files from movies_dir for group detection —
    # after a completed batch conversion all parts will be MP4 and won't appear in `files`.
    # Exclude: outputs already tracked in `files` (avoids double-counting source + output
    # for the same film), and files named "sample" or "trailer" (not film parts).
    tracked_outputs = {output_path_for(f) for f in files}
    _sample_names = {"sample", "trailer", "featurette"}
    mp4_in_movies = [
        p for p in sorted(movies_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() == ".mp4"
        and p not in tracked_outputs
        and p.stem.lower() not in _sample_names
    ]
    groups = detect_groups(files + mp4_in_movies, movies_dir)
    grouped_sources = {f for group_files in groups.values() for f in group_files}
    print(f"Detected {len(groups)} multi-part group(s) across {len(grouped_sources)} file(s)")

    existing_log   = load_log()
    existing_files = {e["source"]: e for e in existing_log["files"]}
    existing_stitch = {e["name"]: e for e in existing_log["stitches"]}

    # Build file entries
    file_entries = []
    new_files = 0
    for f in files:
        key = str(f)
        if key in existing_files:
            file_entries.append(existing_files[key])
        else:
            # Determine which group (if any) this file belongs to
            if f.parent in groups:
                group_name = f.parent.name
            elif f.parent.parent in groups:
                group_name = f.parent.parent.name
            else:
                group_name = None
            file_entries.append({
                "status":    "pending",
                "source":    key,
                "extension": f.suffix.lower(),
                "output":    str(output_path_for(f)),
                "timestamp": None,
                "group":     group_name,
            })
            new_files += 1

    # Build stitch entries
    stitch_entries = []
    new_stitches = 0
    for group_root, group_files in sorted(groups.items()):
        name = group_root.name
        output = group_root / (group_root.name + ".mp4")
        if name in existing_stitch:
            stitch_entries.append(existing_stitch[name])
        else:
            stitch_entries.append({
                "status":    "pending",
                "name":      name,
                "inputs":    [str(output_path_for(f)) for f in group_files],
                "output":    str(output),
                "timestamp": None,
            })
            new_stitches += 1

    log = {"files": file_entries, "stitches": stitch_entries}
    save_log(log)

    print(f"\nFile entries:   {len(file_entries)} total, {new_files} new")
    print(f"Stitch entries: {len(stitch_entries)} total, {new_stitches} new")
    print(f"\nDashboard: {DASHBOARD}")


def cmd_run(log: dict, cfg: dict, file_targets: list[dict], delete_original: bool) -> None:
    succeeded = failed = skipped = 0

    logger.info("=" * 60)
    logger.info("BATCH START — %d file(s) to convert", len(file_targets))

    # ── Pass 1: convert files ─────────────────────────────────────────────────
    for i, entry in enumerate(file_targets, 1):
        source = Path(entry["source"])
        output = Path(entry["output"])

        print(f"[{i}/{len(file_targets)}] {source.name}")

        if output.exists():
            print(f"  [skip] output already exists")
            entry["status"]    = "done"
            entry["timestamp"] = now_iso()
            skipped += 1
            save_log(log)
            print()
            continue

        entry["status"]    = "in-progress"
        entry["timestamp"] = now_iso()
        save_log(log)

        print(f"  [converting] {source.name}")
        print(f"       → {output.name}")
        logger.info("START    [%d/%d] %s", i, len(file_targets), source)

        ok, elapsed = convert(source, output, cfg)

        if ok:
            size_mb = output.stat().st_size / (1024 * 1024)
            print(f"  [done] {output.name} ({size_mb:.1f} MB)")
            logger.info("DONE     %s → %s (%.1f MB, %.1fs)", source.name, output.name, size_mb, elapsed)
            entry["status"]    = "done"
            entry["timestamp"] = now_iso()
            if delete_original:
                source.unlink()
                print(f"  [deleted] {source.name}")
                logger.info("DELETED  %s", source)
            succeeded += 1
        else:
            entry["status"]    = "failed"
            entry["timestamp"] = now_iso()
            failed += 1

        save_log(log)
        print()

    logger.info("BATCH END — succeeded=%d  failed=%d  skipped=%d", succeeded, failed, skipped)
    print("─" * 60)
    print(f"Conversions — succeeded={succeeded}  failed={failed}  skipped={skipped}")
    if failed:
        sys.exit(1)


def cmd_stitch(log: dict) -> None:
    pending_stitches = [e for e in log["stitches"] if e["status"] == "pending"]
    if not pending_stitches:
        print("No pending stitches.")
        return

    print(f"Stitch pass: {len(pending_stitches)} group(s) to join\n")
    logger.info("=" * 60)
    logger.info("STITCH START — %d group(s) to join", len(pending_stitches))
    s_succeeded = s_failed = s_skipped = 0
    cleanup_groups: list[tuple[str, Path, list[Path]]] = []  # (name, output, inputs)

    for i, entry in enumerate(pending_stitches, 1):
        inputs = [Path(p) for p in entry["inputs"]]
        output = Path(entry["output"])
        missing = [p for p in inputs if not p.exists()]

        print(f"[{i}/{len(pending_stitches)}] {entry['name']}")

        if missing:
            print(f"  [skip] {len(missing)} part(s) not yet converted:")
            for m in missing:
                print(f"    {m.name}")
            logger.warning("STITCH SKIPPED  %s — %d part(s) missing: %s",
                           entry["name"], len(missing), ", ".join(m.name for m in missing))
            s_skipped += 1
            print()
            continue

        if output.exists():
            print(f"  [skip] output already exists: {output.name}")
            entry["status"]    = "done"
            entry["timestamp"] = now_iso()
            s_skipped += 1
            save_log(log)
            print()
            continue

        entry["status"]    = "in-progress"
        entry["timestamp"] = now_iso()
        save_log(log)

        print(f"  [stitching] {len(inputs)} parts → {output.name}")
        for inp in inputs:
            print(f"    + {inp.name}")
        logger.info("STITCH START  %s (%d parts)", entry["name"], len(inputs))
        for inp in inputs:
            logger.info("  + %s", inp)

        ok, elapsed = stitch(inputs, output)

        if ok:
            size_mb = output.stat().st_size / (1024 * 1024)
            print(f"  [done] {output.name} ({size_mb:.1f} MB)")
            logger.info("STITCH DONE   %s → %s (%.1f MB, %.1fs)", entry["name"], output.name, size_mb, elapsed)
            entry["status"]    = "done"
            entry["timestamp"] = now_iso()
            s_succeeded += 1
            cleanup_groups.append((entry["name"], output, inputs))
        else:
            entry["status"]    = "failed"
            entry["timestamp"] = now_iso()
            s_failed += 1

        save_log(log)
        print()

    logger.info("STITCH END — succeeded=%d  failed=%d  skipped=%d", s_succeeded, s_failed, s_skipped)
    print("─" * 60)
    print(f"Stitches — succeeded={s_succeeded}  failed={s_failed}  skipped={s_skipped}")

    if cleanup_groups:
        cleanup_path = Path("stitch_cleanup.txt")
        with open(cleanup_path, "w") as f:
            f.write(f"# Stitch cleanup — generated {now_iso()}\n")
            f.write("# Verify each stitched output, then delete the part files listed under it.\n\n")
            for name, output, inputs in cleanup_groups:
                f.write(f"## {name}\n")
                f.write(f"Output:  {output}\n")
                f.write("Parts to delete:\n")
                for inp in inputs:
                    f.write(f"  {inp}\n")
                f.write("\n")
        print(f"\nCleanup list written to: {cleanup_path.resolve()}")
        logger.info("Cleanup list written to %s (%d group(s))", cleanup_path.resolve(), len(cleanup_groups))

    if s_failed:
        sys.exit(1)

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="convert_library.py",
        description="Transcode non-MP4 media files to MP4, then stitch multi-part films.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init",   action="store_true", help="Scan library, create conversion_log.json + dashboard.html")
    group.add_argument("--test",   action="store_true", help="Convert one standalone file per extension (originals kept)")
    group.add_argument("--batch",  action="store_true", help="Convert pending files, delete originals on success")
    group.add_argument("--stitch", action="store_true", help="Join multi-part films whose parts are all done")
    parser.add_argument("--limit", type=int, metavar="N", help="(--batch only) stop after converting N files")
    args = parser.parse_args()

    if args.limit and not args.batch:
        parser.error("--limit can only be used with --batch")

    _setup_logging()

    mode = next(m for m in ("init", "test", "batch", "stitch") if getattr(args, m))
    limit_note = f" --limit {args.limit}" if args.limit else ""
    logger.info("SESSION START — pid=%d  mode=--%s%s", os.getpid(), mode, limit_note)

    try:
        cfg = load_config()

        if args.init:
            cmd_init(cfg)
            return

        log = load_log()
        if not log["files"]:
            print("No log found — run --init first.")
            sys.exit(1)

        interrupted = cleanup_interrupted(log)
        if interrupted:
            print(f"Cleaned up {interrupted} interrupted entry/entries — reset to pending\n")
            save_log(log)

        if args.test:
            seen: set[str] = set()
            targets = []
            for e in log["files"]:
                if e["status"] == "pending" and e["group"] is None and e["extension"] not in seen:
                    seen.add(e["extension"])
                    targets.append(e)
            print(f"Test mode: {len(targets)} file(s) (one standalone file per extension) — originals kept\n")
            cmd_run(log, cfg, targets, delete_original=False)

        elif args.batch:
            targets = [e for e in log["files"] if e["status"] == "pending"]
            if args.limit:
                targets = targets[: args.limit]
            pending_stitches = sum(1 for e in log["stitches"] if e["status"] == "pending")
            if not targets:
                print("Nothing to convert.")
            else:
                limit_note = f" (limit {args.limit})" if args.limit else ""
                print(f"Batch mode: {len(targets)} file(s) to convert{limit_note}, "
                      f"{pending_stitches} group(s) pending stitch "
                      f"— originals deleted on success\n")
            cmd_run(log, cfg, targets, delete_original=True)

        else:  # --stitch
            cmd_stitch(log)

    except Exception:
        logger.critical("UNHANDLED EXCEPTION\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
