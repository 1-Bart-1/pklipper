"""The viewer's HTTP server: a static page plus a small JSON and media API."""
import json
import mimetypes
import re
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import library, media, record

STATIC_DIR = Path(__file__).parent / "static"
UNFILTERED = "unfiltered"
FILTERED = "filtered"
STARRED = "starred"


class Library:
    """A folder holding `unfiltered/` originals and the `filtered/` keepers."""

    def __init__(self, root):
        self.root = root
        self.unfiltered = root / UNFILTERED
        self.filtered = root / FILTERED
        self.cache = root / media.CACHE_DIR
        self.proxies = media.ProxyPool(self.unfiltered, self.cache)
        media.flatten_day_folders(self.filtered)
        self.items = []
        self.refresh()

    def refresh(self):
        """Rescan the unfiltered tree, reusing cached capture times."""
        self.items = library.scan(self.unfiltered, self.cache / "index.json")
        return self.items

    def catalogue(self):
        """The whole library as the page needs it, including what is saved.

        A save recorded by bare file name, as `pklipper.slice` accepts, lights up
        only the one file of that name; a camera restarting its numbering makes
        such a name ambiguous.
        """
        saves = record.saves_by_source(self.root)
        name_counts = Counter(item.label for item in self.items)
        entries = []
        for item in self.items:
            item_saves = saves.get(item.relpath, [])
            if item.relpath != item.label and name_counts[item.label] == 1:
                item_saves = item_saves + saves.get(item.label, [])
            for save in item_saves:
                save["starred"] = self.keeper(item, save["name"]).parent.name == STARRED
            entry = {"relpath": item.relpath, "label": item.label, "kind": item.kind,
                     "day": item.day, "captured": item.captured,
                     "duration": item.duration, "size": item.size, "fps": item.fps,
                     "saves": item_saves}
            entries.append(entry)
        days = library.days(self.items)
        return {"root": str(self.root), "days": days,
                "dayNames": {day: media.day_name(self.filtered, day) for day in days},
                "items": entries}

    def find(self, relpath):
        """The item at `relpath`, raising ValueError when it is not in the library."""
        item = next((item for item in self.items if item.relpath == relpath), None)
        if item is None:
            raise ValueError(f"{relpath} is not in the library")
        return item

    def keeper(self, item, name):
        """The filtered file saved from `item` as `name`, in `starred/` when starred."""
        suffix = Path(item.relpath).suffix
        filename = name + (suffix if item.kind == "photo" else suffix.lower())
        day_dir = media.day_directory(self.filtered, item.day)
        starred = day_dir / STARRED / filename
        return starred if starred.is_file() else day_dir / filename

    def save(self, relpath, name, start=None, end=None):
        """Cut or copy `relpath` into the filtered tree as `name`.

        Returns the path written. Raises ValueError with a message meant for the
        viewer when the request cannot be honoured.
        """
        item = self.find(relpath)
        name = unused_name(self.root, name)
        source = self.unfiltered / relpath
        target = self.keeper(item, name)
        if target.exists():
            raise ValueError(f"{target.name} already exists")
        if item.kind == "photo":
            if not media.copy_photo(source, target):
                raise ValueError(f"copying {source.name} failed")
            record.append_pick(self.root, relpath, name)
            return target
        if start is None or end is None or end <= start:
            raise ValueError("mark an in point and a later out point first")
        if not media.cut_video(source, start, end, target):
            raise ValueError(f"ffmpeg could not cut {source.name}")
        record.append_cut(self.root, relpath, start, end, name)
        return target

    def saved(self, relpath, name):
        """The filtered file saved from `relpath` as `name`, which must exist."""
        target = self.keeper(self.find(relpath), name)
        if not target.is_file():
            raise ValueError(f"{target.relative_to(self.root)} is missing")
        return target

    def update(self, relpath, name, to, start=None, end=None):
        """Rename the keeper saved as `name` to `to`, re-cutting it when given new marks.

        Returns the path it now lives at, starred or not as before.
        """
        current = self.saved(relpath, name)
        to = checked_name(to) if to.strip() == name else unused_name(self.root, to)
        target = current.with_name(to + current.suffix)
        if target != current and target.exists():
            raise ValueError(f"{target.name} already exists")
        if start is None:
            current.rename(target)
        else:
            if self.find(relpath).kind != "video" or end is None or end <= start:
                raise ValueError("mark an in point and a later out point first")
            partial = target.with_name(f".recut {target.name}")
            if not media.cut_video(self.unfiltered / relpath, start, end, partial):
                partial.unlink(missing_ok=True)
                raise ValueError(f"ffmpeg could not cut {Path(relpath).name}")
            partial.replace(target)
            if target != current:
                current.unlink()
        record.update(self.root, name, to, start, end)
        return target

    def toggle_star(self, relpath, name):
        """Move a saved keeper into `starred/` or back out; returns the new path."""
        current = self.saved(relpath, name)
        day_dir = media.day_directory(self.filtered, self.find(relpath).day)
        target = day_dir / current.name if current.parent.name == STARRED \
            else day_dir / STARRED / current.name
        if target.exists():
            raise ValueError(f"{target.relative_to(self.root)} already exists")
        target.parent.mkdir(exist_ok=True)
        current.rename(target)
        return target

    def name_day(self, day, name):
        """Name `day` by renaming its filtered folder to `<day> <name>`."""
        if day not in library.days(self.items):
            raise ValueError(f"{day} is not a day in the library")
        name = name.strip()
        target = self.filtered / (f"{day} {checked_name(name)}" if name else day)
        current = media.day_directory(self.filtered, day)
        if current == target:
            return target
        if target.exists():
            raise ValueError(f"{target.name} already exists")
        if current.is_dir():
            current.rename(target)
        else:
            target.mkdir(parents=True)
        return target

    def export_frame(self, relpath, at):
        """Write the original's frame at `at` seconds into the day's folder."""
        item = self.find(relpath)
        if item.kind != "video" or at is None:
            raise ValueError("only a video has frames to export")
        target = (media.day_directory(self.filtered, item.day)
                  / f"{Path(relpath).stem} {at:.2f}s.jpg")
        if not media.grab_frame(self.unfiltered / relpath, at, target):
            raise ValueError(f"ffmpeg could not grab a frame from {item.label}")
        return target


def checked_name(name):
    """`name` stripped, raising ValueError when it cannot be a file name."""
    name = name.strip()
    if not name:
        raise ValueError("a name is needed")
    if "/" in name or name.startswith("."):
        raise ValueError(f"name {name!r} may not contain '/' or start with '.'")
    return name


def unused_name(root, name):
    """`name` checked, raising ValueError when a keeper already has it."""
    name = checked_name(name)
    if name in record.names_in_use(root):
        raise ValueError(f"name {name!r} is already used")
    return name


def send_file(handler, path, content_type=None, cache=True):
    """Send `path` whole, or the requested byte range for a media player.

    The page and its script carry `no-cache` so a reload always picks up a
    newly installed version; derived media is content-addressed by name and
    stays cacheable.
    """
    size = path.stat().st_size
    content_type = content_type or mimetypes.guess_type(path.name)[0] \
        or "application/octet-stream"
    requested = handler.headers.get("Range", "")
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
    start, end = 0, size - 1
    partial = False
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
        else:
            start = max(size - int(match.group(2)), 0)
        if start >= size:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
        partial = True
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Accept-Ranges", "bytes")
    if not cache:
        handler.send_header("Cache-Control", "no-cache, must-revalidate")
    handler.send_header("Content-Length", str(end - start + 1))
    if partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    remaining = end - start + 1
    with path.open("rb") as stream:
        stream.seek(start)
        while remaining > 0:
            chunk = stream.read(min(1 << 20, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


class Handler(BaseHTTPRequestHandler):
    """Routes for the page, its assets and the media API."""

    protocol_version = "HTTP/1.1"
    library = None

    def handle(self):
        """Serve one connection.

        A player that seeks, or a page that moves to the next file, drops the
        connection mid-stream. That is ordinary, so it must not land a stack
        trace in the console.
        """
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, template, *args):
        """Keep the console to warnings; every request is otherwise noise."""

    def send_json(self, payload, status=200):
        """Send `payload` as JSON."""
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def asset(self, name):
        """Serve a file from the package's static folder."""
        path = (STATIC_DIR / name).resolve()
        if not path.is_file() or STATIC_DIR.resolve() not in path.parents:
            self.send_error(404)
            return
        send_file(self, path, cache=False)

    def send_image(self, path):
        """Send a derived JPEG, or report that ffmpeg could not make one."""
        if path is None:
            self.send_error(500, "ffmpeg could not render this file")
        else:
            send_file(self, path, "image/jpeg")

    def wanted_item(self, query):
        """The item named by `f=`, answering 404 itself when there is none."""
        try:
            return self.library.find((query.get("f") or [""])[0])
        except ValueError:
            self.send_error(404, "not in the library")
            return None

    def do_GET(self):
        """Dispatch a GET to the page, an asset, or the API."""
        url = urlparse(self.path)
        query = parse_qs(url.query)
        route = url.path
        if route == "/":
            self.asset("index.html")
        elif route.startswith("/static/"):
            self.asset(route[len("/static/"):])
        elif route == "/api/library":
            self.send_json(self.library.catalogue())
        elif route == "/api/thumb":
            item = self.wanted_item(query)
            if item:
                self.send_image(media.thumbnail(self.library.unfiltered,
                                                self.library.cache, item.relpath))
        elif route == "/api/photo":
            item = self.wanted_item(query)
            if item:
                self.send_image(media.view(self.library.unfiltered,
                                           self.library.cache, item.relpath))
        elif route == "/api/prepare":
            item = self.wanted_item(query)
            if item:
                priority = int((query.get("priority") or ["0"])[0])
                state = self.library.proxies.state(item.relpath)
                if state == "absent":
                    state = self.library.proxies.request(item.relpath, priority)
                self.send_json({"state": state,
                                "error": self.library.proxies.complaint(item.relpath)})
        elif route == "/api/warm":
            day = (query.get("day") or [""])[0]
            wanted = [item for item in self.library.items
                      if item.day == day and item.kind == "video"]
            for item in wanted:
                self.library.proxies.request(item.relpath, priority=2)
            self.send_json({"queued": len(wanted)})
        elif route == "/api/video":
            item = self.wanted_item(query)
            if item:
                proxy = self.library.proxies.target(item.relpath)
                if proxy.is_file():
                    send_file(self, proxy, "video/mp4")
                else:
                    self.send_json({"state": self.library.proxies.state(item.relpath)},
                                   status=202)
        else:
            self.send_error(404)

    def do_POST(self):
        """Handle a change the viewer asks of the filtered tree."""
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json({"error": "malformed request"}, status=400)
            return
        if url.path == "/api/refresh":
            self.library.refresh()
            self.send_json(self.library.catalogue())
            return
        library = self.library
        relpath = payload.get("relpath", "")
        actions = {
            "/api/save": lambda: library.save(relpath, payload.get("name", ""),
                                              payload.get("start"), payload.get("end")),
            "/api/update": lambda: library.update(relpath, payload.get("name", ""),
                                                  payload.get("to", ""),
                                                  payload.get("start"), payload.get("end")),
            "/api/star": lambda: library.toggle_star(relpath, payload.get("name", "")),
            "/api/frame": lambda: library.export_frame(relpath, payload.get("at")),
            "/api/day": lambda: library.name_day(payload.get("day", ""),
                                                 payload.get("name", "")),
        }
        if url.path not in actions:
            self.send_error(404)
            return
        try:
            target = actions[url.path]()
        except ValueError as refusal:
            self.send_json({"error": str(refusal)}, status=400)
            return
        self.send_json({"saved": str(target.relative_to(library.root)),
                        "name": target.stem, "starred": target.parent.name == STARRED})


def serve(root, host, port):
    """Run the viewer for the library at `root` until interrupted."""
    Handler.library = Library(root)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
