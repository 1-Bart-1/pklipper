"""Everything that shells out to ffmpeg: thumbnails, playback proxies and saves.

Originals are never written to. Browsers cannot play the camera's MOV files
(H.264 alongside PCM audio), so playback runs off small re-encoded proxies that
keep the original timeline, which lets in and out points marked in the viewer be
cut straight from the original without re-encoding.
"""
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from .slice import VIDEO_SUFFIXES, ffmpeg_command

CACHE_DIR = ".pklipper"
THUMB_HEIGHT = 320
PROXY_HEIGHT = 720
VIEW_WIDTH = 2400
PROXY_PRESET = "ultrafast"
URGENT_BUILDS = 3
BACKGROUND_THREADS = 3
FFMPEG_QUIET = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]


def cache_path(cache_root, kind, relpath, suffix):
    """Path of a derived file for `relpath`, mirroring the library's layout."""
    return (cache_root / kind / relpath).with_suffix(suffix)


def run(command):
    """Run an ffmpeg command, returning True when it succeeded."""
    return report(command)[0]


def report(command):
    """Run an ffmpeg command, returning (succeeded, last line of its moaning)."""
    try:
        finished = subprocess.run(command, capture_output=True, text=True)
    except OSError as missing:
        return False, str(missing)
    complaint = (finished.stderr or "").strip().splitlines()
    return finished.returncode == 0, complaint[-1] if complaint else ""


def build_thumbnail(source, target):
    """Write a filmstrip-sized JPEG for a photo or video."""
    target.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", "1"] if source.suffix.lower() in VIDEO_SUFFIXES else []
    return run([*FFMPEG_QUIET, *seek, "-i", str(source), "-frames:v", "1",
                "-vf", f"scale=-2:{THUMB_HEIGHT}", "-q:v", "4", str(target)])


def build_view(source, target):
    """Write a screen-sized JPEG of a photo, so the browser loads it quickly."""
    target.parent.mkdir(parents=True, exist_ok=True)
    return run([*FFMPEG_QUIET, "-i", str(source), "-frames:v", "1",
                "-vf", f"scale='min({VIEW_WIDTH},iw)':-2", "-q:v", "3", str(target)])


def build_proxy(source, target, background=False):
    """Write a 720p MP4 the browser can play and scrub, timeline unchanged.

    A background build is held to a few threads so warming a day's worth of
    files does not take the machine away from whatever is being watched.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part.mp4")
    threads = ["-threads", str(BACKGROUND_THREADS)] if background else []
    done, complaint = report([*FFMPEG_QUIET, *threads, "-i", str(source),
                              "-vf", f"scale=-2:{PROXY_HEIGHT}",
                              "-c:v", "libx264", "-preset", PROXY_PRESET, "-crf", "26",
                              "-g", "50", "-c:a", "aac", "-b:a", "128k",
                              "-movflags", "+faststart", str(partial)])
    if done:
        partial.replace(target)
    else:
        partial.unlink(missing_ok=True)
    return done, complaint


class ProxyPool:
    """Builds playback proxies, with the file being watched never queued.

    Background work — the next few files, then the rest of the day — runs on a
    small pool. A file somebody is waiting on is built on the spot instead, so
    opening it never waits behind work nobody asked for yet.
    """

    def __init__(self, source_root, cache_root, workers=2):
        self.source_root = source_root
        self.cache_root = cache_root
        self.states = {}
        self.complaints = {}
        self.lock = threading.Lock()
        self.pending = queue.PriorityQueue()
        self.rush = threading.Semaphore(URGENT_BUILDS)
        self.ticket = 0
        for number in range(workers):
            threading.Thread(target=self.work, name=f"proxy-{number}",
                             daemon=True).start()

    def target(self, relpath):
        """Where the proxy for `relpath` lives."""
        return cache_path(self.cache_root, "proxies", relpath, ".mp4")

    def complaint(self, relpath):
        """What ffmpeg said when this proxy failed, if it did."""
        with self.lock:
            return self.complaints.get(relpath, "")

    def state(self, relpath):
        """One of ready, working, queued or failed."""
        if self.target(relpath).is_file():
            return "ready"
        with self.lock:
            return self.states.get(relpath, "absent")

    def request(self, relpath, priority=0):
        """Start `relpath` building unless it already is, or already has a proxy.

        Priority 0 is built on the spot, even for a file already waiting in the
        background queue: whoever is watching should never sit behind warming
        work. The stale queue entry is harmless, since only one thread can
        claim a build.
        """
        state = self.state(relpath)
        if state in {"ready", "working"}:
            return state
        if state == "queued" and priority > 0:
            return state
        with self.lock:
            self.states[relpath] = "queued"
            self.ticket += 1
            ticket = self.ticket
        if priority == 0:
            threading.Thread(target=self.rush_build, args=(relpath,),
                             name=f"rush-{ticket}", daemon=True).start()
        else:
            self.pending.put((priority, ticket, relpath))
        return "queued"

    def claim(self, relpath):
        """Take ownership of building `relpath`, or False if someone else has."""
        with self.lock:
            if self.states.get(relpath) in {"working", "ready"}:
                return False
            if self.target(relpath).is_file():
                self.states[relpath] = "ready"
                return False
            self.states[relpath] = "working"
            return True

    def rush_build(self, relpath):
        """Build one proxy straight away, a few at a time at most."""
        with self.rush:
            self.build(relpath)

    def build(self, relpath, background=False):
        """Make one proxy and record how it went.

        This must never raise: a worker that died would leave every later
        request sitting in `queued` for ever and the viewer spinning.
        """
        if not self.claim(relpath):
            return
        try:
            done, complaint = build_proxy(self.source_root / relpath,
                                          self.target(relpath), background)
        except Exception as mishap:
            done, complaint = False, repr(mishap)
        with self.lock:
            self.states[relpath] = "ready" if done else "failed"
            if complaint:
                self.complaints[relpath] = complaint
        if not done:
            print(f"proxy failed for {relpath}: {complaint}", flush=True)

    def work(self):
        """Drain the background queue, one proxy at a time."""
        while True:
            _, _, relpath = self.pending.get()
            self.build(relpath, background=True)
            self.pending.task_done()


def thumbnail(source_root, cache_root, relpath):
    """Path to the thumbnail for `relpath`, building it on first use."""
    target = cache_path(cache_root, "thumbs", relpath, ".jpg")
    if not target.is_file():
        build_thumbnail(source_root / relpath, target)
    return target if target.is_file() else None


def view(source_root, cache_root, relpath):
    """Path to the screen-sized copy of a photo, building it on first use."""
    target = cache_path(cache_root, "views", relpath, ".jpg")
    if not target.is_file():
        build_view(source_root / relpath, target)
    return target if target.is_file() else None


def day_directory(filtered_root, day):
    """The filtered folder for `day`, reusing one already named after it."""
    existing = [path for path in sorted(filtered_root.glob(f"{day}*")) if path.is_dir()]
    return existing[0] if existing else filtered_root / day


def flatten_day_folders(filtered_root):
    """Move keepers up out of the `clips/`, `photos/` and `frames/` older versions made."""
    for folder in sorted(filtered_root.glob("*/*")):
        if folder.name not in ("clips", "photos", "frames") or not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            target = folder.parent / path.name
            if target.exists():
                print(f"{path} left where it is: {target} already exists")
            else:
                path.rename(target)
        if not any(folder.iterdir()):
            folder.rmdir()


def day_name(filtered_root, day):
    """The name given to `day`: whatever follows the day in its filtered folder."""
    return day_directory(filtered_root, day).name[len(day):].strip()


def cut_video(source, start, end, target):
    """Stream-copy `start`-`end` out of `source`, leaving the original alone."""
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ffmpeg_command(source, f"{start:.3f}", f"{end:.3f}", target, mp4=False)
    return run(command)


def copy_photo(source, target):
    """Copy a photo into the filtered tree under its new name."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target.is_file()


def grab_frame(source, at, target):
    """Write the frame of `source` at `at` seconds as a full-size JPEG."""
    target.parent.mkdir(parents=True, exist_ok=True)
    return run([*FFMPEG_QUIET, "-ss", f"{at:.3f}", "-i", str(source), "-frames:v", "1",
                "-q:v", "2", str(target)])
