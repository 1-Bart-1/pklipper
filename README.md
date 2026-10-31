# PKlipper

A browser-based viewer for going through a day of camera footage fast, marking the bits worth keeping, and cutting them out without re-encoding. Built for a card dump: one folder of originals in, named keepers out, originals never touched.

## Layout it expects

```
<library>/
├── unfiltered/      every original, in any arrangement you like — days come from the metadata
├── filtered/        what you keep: <day> <name>/ holds clips, photos and frames, starred/ the favourites
├── cuts.csv         every clip you saved: source,start,end,name
├── picks.csv        every photo you saved: source,name
└── .pklipper/        thumbnails, playback proxies and the capture-time index (disposable)
```

Days are read from EXIF for photos and from the container's creation time for video, converted to `CAPTURE_TZ` in `pklipper/library.py`. Folder names inside `unfiltered/` are ignored, so a card that restarted its file numbering can be dropped in day-folders without the viewer caring — except a top folder named `<dd-mm> <tag>`, whose files get days of their own, `<tag> <dd-mm>`, to keep say `29-09 lifestyle/` apart from the surf footage of `29-09`. `.mpg` counts as video.

## Download

Every tagged release carries a single-file build for Linux, macOS and Windows, built by CI — grab the one for your machine from the [releases page](../../releases/latest). Builds for the latest commit on `main` are on the [Actions tab](../../actions) as artifacts.

`ffmpeg` and `ffprobe` must be on your PATH (`pacman -S ffmpeg`, `brew install ffmpeg`, `winget install Gyan.FFmpeg`); pklipper says so and stops if they are missing. The builds are unsigned, so macOS wants right-click → Open the first time.

## Install from source

```bash
make install            # venv + launcher in ~/.local/bin + a desktop entry
pklipper ~/Pictures/Ierland
```

The folder you pass is remembered, so the launcher entry and a bare `pklipper` reopen it. `make uninstall` removes the app and leaves every photo and clip alone. No Python packages at all — stdlib and ffmpeg.

## Using it

One video can hold as many clips as you like: mark `i` and `o`, name it, **Save**, and a fresh clip is waiting — `a` adds one by hand, and the chips under the bar switch between them. A name is required before **Save** does anything.

A saved clip stays editable: select it, drag its handles or change its name, and **Re-save** cuts it again from the original and updates its row in `cuts.csv`; for a saved photo the button renames it. Click the PK logo next to the title, or press `s`, to star a keeper: it moves into `filtered/<day>/starred/`, and clicking again moves it back. `e` exports the frame under the playhead, full size from the original, to `filtered/<day>/`. Double-click a day to name it; the name becomes its folder, `filtered/<day> <name>/`.

Marks are draggable on the play bar. Click a marker to select it, then `←`/`→` move it a second and `shift`+`←`/`→` move it a single frame. With no marker selected the arrows seek the video instead. Zoomed into the picture, they pan it. `1`–`4` set playback speed down to 0.25× for checking a turn frame by frame, `l` loops the clip you are working on, `f` is fullscreen, `?` lists every key.

Playback runs off 720p proxies built on demand into `.pklipper/proxies/`, because browsers cannot play the camera's H.264-plus-PCM MOV files. Proxies keep the original timeline, so marks made against a proxy cut correctly from the original.

Saving a clip stream-copies it, which is instant and lossless but can only start on a keyframe — a clip may begin up to a keyframe interval (~2 s) before the in point. `python -m pklipper.slice` re-cuts a whole `cuts.csv` from the originals if you want to redo a day after editing names or times by hand.
