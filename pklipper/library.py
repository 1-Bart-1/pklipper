"""Scan a media library and group every file by the day it was captured.

A top folder named `<dd-mm> <tag>` puts its files on days of their own,
labelled `<tag> <dd-mm>`, so they stay apart from the plain `<dd-mm>` days.

Photo timestamps come from EXIF and are already camera-local. Video timestamps
come from the QuickTime/MP4 container in UTC and are converted to `CAPTURE_TZ`,
so a clip shot late in the evening lands on the day it was filmed.
"""
import json
import re
import struct
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .slice import VIDEO_SUFFIXES

CACHE_VERSION = 3
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic"}
TAGGED_FOLDER = re.compile(r"\d\d-\d\d (.+)")
CAPTURE_TZ = ZoneInfo("Europe/Dublin")


@dataclass
class MediaItem:
    """One original file in the unfiltered tree."""

    relpath: str
    kind: str
    day: str
    captured: str
    duration: float
    size: int
    fps: float = 25.0

    @property
    def label(self):
        """File name as shown in the viewer."""
        return Path(self.relpath).name


def exif_datetime(path):
    """Read EXIF DateTimeOriginal from a JPEG, or None when it carries none."""
    with path.open("rb") as handle:
        head = handle.read(256 * 1024)
    marker = head.find(b"Exif\x00\x00")
    if marker < 0:
        return None
    tiff = marker + 6
    order = ">" if head[tiff:tiff + 2] == b"MM" else "<"
    try:
        first_ifd = struct.unpack_from(order + "I", head, tiff + 4)[0]
    except struct.error:
        return None

    def entries(offset, wanted):
        count = struct.unpack_from(order + "H", head, tiff + offset)[0]
        found = {}
        for number in range(count):
            field = tiff + offset + 2 + number * 12
            tag = struct.unpack_from(order + "H", head, field)[0]
            if tag in wanted:
                found[tag] = struct.unpack_from(order + "I", head, field + 8)[0]
        return found

    try:
        top = entries(first_ifd, {0x8769, 0x0132})
        stamp_offset = None
        if 0x8769 in top:
            stamp_offset = entries(top[0x8769], {0x9003}).get(0x9003)
        if stamp_offset is None:
            stamp_offset = top.get(0x0132)
        if stamp_offset is None:
            return None
        text = head[tiff + stamp_offset:tiff + stamp_offset + 19].decode("ascii")
        return datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except (struct.error, UnicodeDecodeError, ValueError):
        return None


def probe_video(path):
    """Return (local capture time, duration, frame rate) for a video file."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-show_entries", "format=duration:format_tags=creation_time",
         "-of", "json", str(path)], capture_output=True, text=True)
    duration, captured, fps = 0.0, None, 25.0
    if probe.returncode == 0:
        report = json.loads(probe.stdout)
        container = report.get("format", {})
        duration = float(container.get("duration") or 0.0)
        stamp = (container.get("tags") or {}).get("creation_time")
        if stamp:
            utc = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if utc.tzinfo is None:
                utc = utc.replace(tzinfo=timezone.utc)
            captured = utc.astimezone(CAPTURE_TZ).replace(tzinfo=None)
        rate = (report.get("streams") or [{}])[0].get("r_frame_rate", "")
        if "/" in rate:
            top, bottom = rate.split("/")
            if float(bottom):
                fps = float(top) / float(bottom)
    return captured, duration, fps


def day_label(relpath, captured):
    """The capture date as `dd-mm`, behind the tag of a `<dd-mm> <tag>` top folder."""
    date = captured.strftime("%d-%m")
    parts = Path(relpath).parts
    tagged = TAGGED_FOLDER.fullmatch(parts[0]) if len(parts) > 1 else None
    return f"{tagged[1]} {date}" if tagged else date


def describe(path, root):
    """Build the MediaItem for one file, falling back to its mtime when unknown."""
    kind = "video" if path.suffix.lower() in VIDEO_SUFFIXES else "photo"
    if kind == "video":
        captured, duration, fps = probe_video(path)
    else:
        captured, duration, fps = exif_datetime(path), 0.0, 25.0
    if captured is None:
        captured = datetime.fromtimestamp(path.stat().st_mtime)
    relpath = str(path.relative_to(root))
    return MediaItem(relpath=relpath, kind=kind, day=day_label(relpath, captured),
                     captured=captured.isoformat(timespec="seconds"),
                     duration=round(duration, 3), size=path.stat().st_size,
                     fps=round(fps, 3))


def media_files(root):
    """Every photo and video under `root`, sorted by name, hidden dirs skipped."""
    wanted = PHOTO_SUFFIXES | VIDEO_SUFFIXES
    found = [path for path in sorted(root.rglob("*"))
             if path.suffix.lower() in wanted and path.is_file()
             and not any(part.startswith(".") for part in path.relative_to(root).parts)]
    return found


def scan(root, cache_path):
    """Describe every file under `root`, reusing a cache keyed by size and mtime."""
    cached = {}
    if cache_path.is_file():
        try:
            stored = json.loads(cache_path.read_text())
            if stored.get("version") == CACHE_VERSION:
                cached = stored["files"]
        except (json.JSONDecodeError, KeyError, AttributeError):
            cached = {}
    items, fresh = [], {}
    for path in media_files(root):
        relpath = str(path.relative_to(root))
        stat = path.stat()
        key = f"{stat.st_size}:{int(stat.st_mtime)}"
        entry = cached.get(relpath)
        if not entry or entry.get("key") != key:
            entry = {"key": key, "item": asdict(describe(path, root))}
        fresh[relpath] = entry
        items.append(MediaItem(**entry["item"]))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"version": CACHE_VERSION, "files": fresh},
                                     indent=1))
    items.sort(key=lambda item: (item.captured, item.relpath))
    return items


def days(items):
    """Day labels present in `items`, earliest first."""
    seen = {}
    for item in items:
        seen.setdefault(item.day, item.captured)
    return [day for day, _ in sorted(seen.items(), key=lambda pair: pair[1])]
