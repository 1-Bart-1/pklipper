"""The two human-editable records of what was kept out of the unfiltered tree.

`cuts.csv` holds video cuts in the format `pklipper.slice` already consumes, so a
whole day can be re-cut from the originals at any time. `picks.csv` holds photos,
which are copied rather than cut and therefore need no timestamps.
"""
import csv

from .slice import read_cuts, seconds

CUTS_SHEET = "cuts.csv"
PICKS_SHEET = "picks.csv"


def stamp(total_seconds):
    """Format seconds as H:MM:SS.mmm, the spelling `pklipper.slice` parses."""
    return "{:d}:{:02d}:{:06.3f}".format(int(total_seconds // 3600),
                                         int(total_seconds % 3600 // 60),
                                         total_seconds % 60)


def read_picks(sheet):
    """Rows of (source, name) from a picks sheet, header and blanks skipped."""
    if not sheet.is_file():
        return []
    with sheet.open(newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.reader(handle)
                if row and row[0].strip() and not row[0].lstrip().startswith("#")]
    if rows and rows[0][0].strip().lower() == "source":
        rows = rows[1:]
    return [(row[0].strip(), row[1].strip()) for row in rows if len(row) >= 2]


def saves_by_source(root):
    """Map each source, spelled as the sheet records it, to the saves made from it."""
    saves = {}
    cuts_sheet = root / CUTS_SHEET
    if cuts_sheet.is_file():
        try:
            rows = read_cuts(cuts_sheet)
        except ValueError as complaint:
            print(f"{CUTS_SHEET}: {complaint} — saved clips will not be marked")
            rows = []
        for source, start, end, name in rows:
            saves.setdefault(source, []).append(
                {"name": name, "start": seconds(start), "end": seconds(end)})
    for source, name in read_picks(root / PICKS_SHEET):
        saves.setdefault(source, []).append({"name": name})
    return saves


def names_in_use(root):
    """Every clip and photo name already recorded, to reject duplicates early."""
    return {save["name"] for saves in saves_by_source(root).values() for save in saves}


def append(sheet, header, row):
    """Append one row, writing `header` first when the sheet is new."""
    new = not sheet.is_file()
    with sheet.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(header)
        writer.writerow(row)


def append_cut(root, source, start, end, name):
    """Record a video cut in `cuts.csv`."""
    append(root / CUTS_SHEET, ["source", "start", "end", "name"],
           [source, stamp(start), stamp(end), name])


def append_pick(root, source, name):
    """Record a kept photo in `picks.csv`."""
    append(root / PICKS_SHEET, ["source", "name"], [source, name])


def update(root, name, to, start=None, end=None):
    """Rename the save called `name` to `to`, and give a cut new marks when passed."""
    for sheet, column in ((root / CUTS_SHEET, 3), (root / PICKS_SHEET, 1)):
        if not sheet.is_file():
            continue
        with sheet.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        matches = [row for row in rows if len(row) > column and row[column].strip() == name]
        if not matches:
            continue
        for row in matches:
            row[column] = to
            if start is not None and sheet.name == CUTS_SHEET:
                row[1], row[2] = stamp(start), stamp(end)
        with sheet.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
