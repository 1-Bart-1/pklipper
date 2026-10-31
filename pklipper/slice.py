#!/usr/bin/env python3
"""Cut clips out of videos without re-encoding, driven by a spreadsheet.

Reads cuts.csv (source,start,end,name) from the current directory and
stream-copies each row into clips/<name>. Cuts snap to the nearest keyframe at or before `start`, so a
clip may begin up to one keyframe interval early.
"""
import argparse
import csv
import subprocess
import sys
from pathlib import Path

VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".mpg"}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sheet", nargs="?", default=None,
                        help="cut list (default: cuts.csv next to this script)")
    parser.add_argument("--outdir", default=None,
                        help="where clips land (default: clips/ next to the sheet)")
    parser.add_argument("--mp4", action="store_true",
                        help="write .mp4, re-encoding only the audio to AAC")
    parser.add_argument("--force", action="store_true", help="overwrite existing clips")
    parser.add_argument("--dry-run", action="store_true", help="print commands only")
    return parser.parse_args()


def find_source(name, root):
    """Locate `name` under `root`, by exact path first and then by basename."""
    direct = Path(name) if Path(name).is_absolute() else root / name
    if direct.is_file():
        return direct
    matches = [path for path in root.rglob(Path(name).name) if path.is_file()]
    if len(matches) > 1:
        raise LookupError(f"{name} matches {len(matches)} files under {root}")
    if not matches:
        raise LookupError(f"{name} not found under {root}")
    return matches[0]


def seconds(timestamp):
    """Convert [[HH:]MM:]SS[.sss] to seconds."""
    parts = str(timestamp).strip().split(":")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def read_cuts(sheet):
    with sheet.open(newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.reader(handle)
                if row and row[0].strip() and not row[0].lstrip().startswith("#")]
    if rows and rows[0][0].strip().lower() == "source":
        rows = rows[1:]
    cuts, seen = [], {}
    for number, row in enumerate(rows, start=1):
        if len(row) < 4:
            raise ValueError(f"row {number} has {len(row)} columns, expected 4")
        source, start, end, name = (field.strip() for field in row[:4])
        if not name:
            raise ValueError(f"row {number} ({source} {start}-{end}) has no name")
        if "/" in name:
            raise ValueError(f"row {number}: name {name!r} may not contain '/'")
        if seconds(end) <= seconds(start):
            raise ValueError(f"row {number}: end {end} is not after start {start}")
        if name in seen:
            raise ValueError(f"row {number}: name {name!r} already used by row {seen[name]}")
        seen[name] = number
        cuts.append((source, start, end, name))
    return cuts


def ffmpeg_command(source, start, end, target, mp4):
    codecs = ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"] if mp4 else ["-c", "copy"]
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-ss", start, "-to", end, "-i", str(source),
            "-map", "0", "-avoid_negative_ts", "make_zero",
            *codecs, str(target)]


def main():
    args = parse_args()
    sheet = Path(args.sheet).resolve() if args.sheet else Path.cwd() / "cuts.csv"
    if not sheet.is_file():
        sys.exit(f"no cut list at {sheet}")
    root = sheet.parent
    outdir = Path(args.outdir).resolve() if args.outdir else root / "clips"

    try:
        cuts = read_cuts(sheet)
    except ValueError as error:
        sys.exit(f"{sheet.name}: {error}")
    if not cuts:
        sys.exit(f"{sheet.name} has no cuts")

    if not args.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for source, start, end, name in cuts:
        try:
            path = find_source(source, root)
        except LookupError as error:
            print(f"skip  {name}: {error}", file=sys.stderr)
            failed += 1
            continue
        suffix = ".mp4" if args.mp4 else path.suffix.lower()
        target = outdir / (name if Path(name).suffix.lower() in VIDEO_SUFFIXES
                           else name + suffix)
        if target.exists() and not args.force:
            print(f"keep  {target.name} (exists)")
            continue
        command = ffmpeg_command(path, start, end, target, args.mp4)
        if args.dry_run:
            print(" ".join(command))
            continue
        print(f"cut   {target.name}  <- {path.name} {start}-{end}")
        if subprocess.run(command).returncode != 0:
            failed += 1
    if failed:
        sys.exit(f"{failed} cut(s) failed")


if __name__ == "__main__":
    main()
