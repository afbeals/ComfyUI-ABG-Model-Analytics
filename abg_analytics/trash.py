"""Measure files actually present in current and previous trash locations."""

import os
from pathlib import Path

from .inventory import linked, path_key, within
from .moving import check_plain_path


def summarize_trash(locations, current=None):
    roots = sorted({Path(location) for location in locations}, key=lambda path: len(path.parts))
    # Previous settings can produce overlapping trash roots; count each file once.
    parents = []
    for root in roots:
        if not any(within(root, parent) for parent in parents):
            parents.append(root)
    summary = {"size_bytes": 0, "file_count": 0, "locations": [], "warnings": [], "complete": True}
    seen = set()
    for root in parents:
        row = {
            "path": str(root),
            "size_bytes": 0,
            "file_count": 0,
            "exists": False,
            "current": bool(current and within(Path(current), root)),
        }
        try:
            check_plain_path(root)
            row["exists"] = root.is_dir()
            if not row["exists"]:
                row["note"] = "Not present or unavailable"
                summary["complete"] = False
                summary["locations"].append(row)
                continue

            def on_error(error):
                summary["warnings"].append(f"Cannot scan trash {error.filename}: {error.strerror}")

            for directory, subdirs, files in os.walk(root, followlinks=False, onerror=on_error):
                parent = Path(directory)
                kept = []
                for name in subdirs:
                    child = parent / name
                    if linked(child):
                        summary["warnings"].append(f"Skipped a linked trash directory: {child}")
                    else:
                        kept.append(name)
                subdirs[:] = kept
                for name in files:
                    path = parent / name
                    key = path_key(path)
                    try:
                        if key in seen:
                            continue
                        if linked(path):
                            summary["warnings"].append(f"Skipped a linked trash file: {path}")
                            continue
                        if path.is_file():
                            row["size_bytes"] += path.stat().st_size
                            row["file_count"] += 1
                            seen.add(key)
                    except OSError as error:
                        summary["warnings"].append(f"Cannot inspect trash file {path}: {error}")
        except (OSError, ValueError) as error:
            summary["warnings"].append(f"Cannot inspect trash location {root}: {error}")
        summary["locations"].append(row)
        summary["size_bytes"] += row["size_bytes"]
        summary["file_count"] += row["file_count"]
    if summary["warnings"]:
        summary["complete"] = False
    return summary
